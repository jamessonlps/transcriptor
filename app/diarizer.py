"""Diarizacao de falantes usando pyannote.audio 4.x + speaker-diarization-community-1.

Pipeline:
  1. pyannote analisa o audio e produz "turnos" — intervalos com label de falante
     (speaker_0, speaker_1, ...).
  2. Para cada segmento ja transcrito pelo Whisper, encontramos qual falante
     teve maior sobreposicao temporal e atribuimos como dono daquele segmento.

Modelo: pyannote/speaker-diarization-community-1 (Fev/2026, OSS).
Melhoria significativa em "speaker counting and assignment" vs o legacy 3.1.

API v4 vs v3 (pra referencia):
  v3:   annotation = pipeline(audio); for seg, _, label in annotation.itertracks(yield_label=True)
  v4:   output = pipeline(audio); for turn, speaker in output.speaker_diarization
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"


@contextmanager
def _ensure_wav(audio_path: Path) -> Iterator[Path]:
    """Garante que o caminho retornado e um WAV mono 16kHz.

    pyannote usa torchaudio/soundfile internamente, que so le formatos puros
    de audio (WAV/FLAC/OGG). MP4 precisa ser convertido.
    """
    if audio_path.suffix.lower() == ".wav":
        yield audio_path
        return

    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="diarize_")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        logger.info("Extraindo audio para WAV mono 16kHz: %s -> %s", audio_path.name, tmp_path.name)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str


_PIPELINE: Any = None
_PIPELINE_LOCK = Lock()


class DiarizationUnavailable(Exception):
    """Token HF ausente, modelo nao aceito, ou pyannote nao instalado."""


def get_pipeline() -> Any:
    """Carrega (e cacheia) o pipeline pyannote community-1.

    Requer HF_TOKEN no env e termos aceitos em:
      - huggingface.co/pyannote/speaker-diarization-community-1
      - huggingface.co/pyannote/segmentation-3.0 (dependencia)
    """
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is not None:
            return _PIPELINE

        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        if not token:
            raise DiarizationUnavailable("Token HuggingFace ausente. Crie um arquivo .env com HF_TOKEN=hf_...")

        try:
            from pyannote.audio import Pipeline
        except ImportError as e:
            raise DiarizationUnavailable(f"pyannote.audio nao instalado: {e}") from e

        logger.info("Carregando pipeline %s...", DIARIZATION_MODEL)
        try:
            pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
        except Exception as e:
            raise DiarizationUnavailable(
                f"Falha ao carregar pipeline. Aceite os termos em "
                f"huggingface.co/{DIARIZATION_MODEL.replace('pyannote/', 'pyannote/')} "
                f"e huggingface.co/pyannote/segmentation-3.0. Erro: {e}"
            ) from e

        # Acelera com Apple Silicon (MPS) ou GPU se disponivel.
        try:
            import torch

            if torch.backends.mps.is_available():
                pipe.to(torch.device("mps"))
                logger.info("Pipeline movido para MPS (Apple Silicon GPU)")
            elif torch.cuda.is_available():
                pipe.to(torch.device("cuda"))
                logger.info("Pipeline movido para CUDA")
        except Exception as e:
            logger.warning("Nao foi possivel mover pipeline pra GPU: %s. Usando CPU.", e)

        _PIPELINE = pipe
        logger.info("Pipeline %s pronto", DIARIZATION_MODEL)
        return _PIPELINE


def _extract_turns(output: Any) -> list[SpeakerTurn]:
    """Extrai turnos do output do pyannote 4 (com fallback pra 3.x se necessario)."""
    turns: list[SpeakerTurn] = []

    # API v4: output.speaker_diarization itera (turn, speaker)
    if hasattr(output, "speaker_diarization"):
        for turn, speaker in output.speaker_diarization:
            turns.append(SpeakerTurn(start=turn.start, end=turn.end, speaker=str(speaker)))
        return turns

    # Fallback API v3: itertracks(yield_label=True)
    if hasattr(output, "itertracks"):
        for segment, _, label in output.itertracks(yield_label=True):
            turns.append(SpeakerTurn(start=segment.start, end=segment.end, speaker=str(label)))
        return turns

    raise RuntimeError(f"Formato de output do pyannote desconhecido: {type(output)}")


def diarize(audio_path: Path, num_speakers: int | None = None) -> list[SpeakerTurn]:
    """Roda diarizacao no audio. Retorna turnos ordenados por tempo."""
    pipe = get_pipeline()

    kwargs: dict[str, Any] = {}
    if num_speakers is not None and num_speakers > 0:
        kwargs["num_speakers"] = num_speakers

    logger.info("Diarizando %s (num_speakers=%s)...", audio_path.name, num_speakers or "auto")
    with _ensure_wav(audio_path) as wav_path:
        try:
            output = pipe(str(wav_path), **kwargs)
        except TypeError:
            # community-1 pode nao aceitar num_speakers — tenta sem
            logger.warning("Pipeline nao aceitou num_speakers; usando auto-detect.")
            output = pipe(str(wav_path))

    turns = _extract_turns(output)
    turns.sort(key=lambda t: t.start)
    logger.info("Diarizacao OK: %d turnos, %d falantes unicos", len(turns), len({t.speaker for t in turns}))
    return turns


def assign_speakers_to_segments(
    whisper_segments: list[dict[str, Any]],
    turns: list[SpeakerTurn],
) -> dict[int, str]:
    """Para cada segmento Whisper, atribui o falante com maior overlap temporal."""
    assignments: dict[int, str] = {}
    for seg in whisper_segments:
        s_start, s_end = seg["start"], seg["end"]
        overlaps: dict[str, float] = {}
        for turn in turns:
            o_start = max(s_start, turn.start)
            o_end = min(s_end, turn.end)
            if o_end > o_start:
                overlaps[turn.speaker] = overlaps.get(turn.speaker, 0.0) + (o_end - o_start)
        if overlaps:
            assignments[seg["index"]] = max(overlaps, key=lambda s: overlaps[s])
        else:
            assignments[seg["index"]] = "SPEAKER_UNKNOWN"
    return assignments


def diarize_stream(
    audio_path: Path,
    whisper_segments: list[dict[str, Any]],
    num_speakers: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Gera eventos para o frontend durante a diarizacao."""
    try:
        yield {"type": "diarizing"}
        turns = diarize(audio_path, num_speakers=num_speakers)
        assignments = assign_speakers_to_segments(whisper_segments, turns)
        unique_speakers = sorted({s for s in assignments.values()})
        yield {
            "type": "diarization_done",
            "speakers": unique_speakers,
            "assignments": assignments,
            "turns": [{"start": t.start, "end": t.end, "speaker": t.speaker} for t in turns],
        }
    except DiarizationUnavailable as e:
        logger.warning("Diarizacao indisponivel: %s", e)
        yield {"type": "diarization_error", "message": str(e)}
    except Exception as e:
        logger.exception("Erro inesperado na diarizacao")
        yield {"type": "diarization_error", "message": f"{type(e).__name__}: {e}"}

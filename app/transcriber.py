"""Wrapper sobre faster-whisper que emite eventos em streaming.

A transcricao e exposta como um gerador para que o servidor possa enviar
cada segmento ao cliente assim que ele e produzido (via SSE).
"""

from __future__ import annotations

import gc
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

ModelSize = str

# Cache com eviction: mantemos APENAS 1 modelo em RAM por vez.
# Trocar de medium (~1.5GB) pra large-v3 (~3GB) sem eviction estoura RAM
# em maquinas com 8GB. Vale o re-load (~5-10s) pra economizar memoria.
_CURRENT_MODEL: WhisperModel | None = None
_CURRENT_KEY: tuple[str, str, str, int, int] | None = None
_MODEL_LOCK = Lock()


def _detect_threads() -> int:
    """Numero de threads (OpenMP/intra-op) para o CTranslate2.

    Heuristica:
      - Override explicito via WHISPER_CPU_THREADS no env.
      - Default: cores - 2 (deixa folga pro OS/UI), com clamp [4, 8].
        Cap em 8 porque acima disso o ganho cai (memory bandwidth + cache
        thrashing); em Apple Silicon mistura perf + efficiency cores mais
        lentos. 4 e o piso pra nao virar tartaruga em maquinas pequenas.
    """
    env = os.environ.get("WHISPER_CPU_THREADS")
    if env:
        try:
            n = int(env)
            if n >= 1:
                return n
        except ValueError:
            logger.warning("WHISPER_CPU_THREADS invalido (%r), usando auto", env)

    cores = os.cpu_count() or 8
    return max(4, min(8, cores - 2))


def get_model(
    size: ModelSize,
    device: str = "cpu",
    compute_type: str = "int8",
    cpu_threads: int | None = None,
    num_workers: int = 1,
) -> WhisperModel:
    """Carrega o modelo Whisper, evictando o anterior se for diferente.

    cpu_threads: threads OpenMP por inferencia. None = autodetect via _detect_threads().
    num_workers: workers paralelos do CTranslate2 (pra processar varias requests
                 ou chunks em paralelo). Mantemos em 1 enquanto a Camada 3
                 (chunked parallel transcription) nao for implementada.
    """
    global _CURRENT_MODEL, _CURRENT_KEY
    threads = cpu_threads if cpu_threads is not None else _detect_threads()
    key = (size, device, compute_type, threads, num_workers)
    with _MODEL_LOCK:
        if _CURRENT_KEY == key and _CURRENT_MODEL is not None:
            logger.info("Reusando modelo em cache: %s", size)
            return _CURRENT_MODEL

        if _CURRENT_MODEL is not None:
            logger.info("Descarregando modelo anterior %s para liberar RAM", _CURRENT_KEY)
            _CURRENT_MODEL = None
            gc.collect()

        logger.info(
            "Carregando modelo %s (device=%s, compute=%s, threads=%d, workers=%d)...",
            size, device, compute_type, threads, num_workers,
        )
        _CURRENT_MODEL = WhisperModel(
            size,
            device=device,
            compute_type=compute_type,
            cpu_threads=threads,
            num_workers=num_workers,
        )
        _CURRENT_KEY = key
        logger.info("Modelo %s pronto", size)
        return _CURRENT_MODEL


def evict_if_matches(size: ModelSize) -> bool:
    """Descarrega o modelo em RAM se for o de chave ``size``.

    Necessário antes de remover o cache de disco: senão o modelo continuaria
    "vivo" em memória e a chamada DELETE aparenta sucesso mas a próxima
    inferência ainda funcionaria por inércia.

    Retorna True se algo foi evictado.
    """
    global _CURRENT_MODEL, _CURRENT_KEY
    with _MODEL_LOCK:
        if _CURRENT_KEY is not None and _CURRENT_KEY[0] == size:
            logger.info("Evictando modelo %s da RAM (delete request)", size)
            _CURRENT_MODEL = None
            _CURRENT_KEY = None
            gc.collect()
            return True
    return False


def format_timestamp(seconds: float) -> str:
    """Formato SRT: HH:MM:SS,mmm."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@dataclass
class Segment:
    index: int
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "start_ts": format_timestamp(self.start),
            "end_ts": format_timestamp(self.end),
            "text": self.text,
        }


@dataclass
class TranscriptionResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0

    @property
    def full_text(self) -> str:
        return "\n".join(seg.text for seg in self.segments)

    @property
    def srt(self) -> str:
        lines: list[str] = []
        for seg in self.segments:
            lines.append(str(seg.index))
            lines.append(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}")
            lines.append(seg.text)
            lines.append("")
        return "\n".join(lines)


def transcribe_stream(
    audio_path: Path,
    model_size: ModelSize = "medium",
    language: str = "pt",
) -> Iterator[dict[str, Any]]:
    """Transcreve emitindo eventos como dicts.

    Eventos possiveis:
        {"type": "loading", "model": "..."}
        {"type": "info", "duration": float, "language": str, "language_probability": float}
        {"type": "segment", "segment": {...}}
        {"type": "progress", "current": float, "total": float}
        {"type": "done", "result": {...}}
        {"type": "error", "message": str}
    """
    try:
        yield {"type": "loading", "model": model_size}
        logger.info("Iniciando transcricao: %s (modelo %s)", audio_path.name, model_size)
        model = get_model(model_size)

        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        result = TranscriptionResult(
            language=info.language,
            language_probability=info.language_probability,
            duration=info.duration,
        )
        yield {
            "type": "info",
            "duration": info.duration,
            "language": info.language,
            "language_probability": info.language_probability,
        }

        for i, raw in enumerate(segments_iter, start=1):
            seg = Segment(index=i, start=raw.start, end=raw.end, text=raw.text.strip())
            result.segments.append(seg)
            yield {"type": "segment", "segment": seg.to_dict()}
            yield {"type": "progress", "current": raw.end, "total": info.duration}

        yield {
            "type": "done",
            "result": {
                "full_text": result.full_text,
                "srt": result.srt,
                "language": result.language,
                "language_probability": result.language_probability,
                "duration": result.duration,
                "segments": [s.to_dict() for s in result.segments],
            },
        }
    except Exception as e:
        logger.exception("Erro na transcricao")
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}

"""Worker thread that drives a single transcription task.

Architectural note — why parallel transcribe + diarise?

  Whisper runs on CPU (CTranslate2 / int8). Pyannote 4 runs on MPS on
  Apple Silicon and CUDA on NVIDIA. They don't compete for the same
  hardware, so we let them progress in parallel:

      transcribe (CPU)  ============>
      diarise   (GPU)   ====>          ← usually finishes first
                                       ← join + assign + emit

  Wall-clock becomes ``max(transcribe, diarise)`` instead of the sum.
  On a 30-minute file with the ``medium`` model, that's roughly a 25-40%
  speedup vs running them serially.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.diarizer import (
    DiarizationUnavailable,
    SpeakerTurn,
    assign_speakers_to_segments,
    diarize,
)
from app.domain.task import Task
from app.transcriber import transcribe_stream

logger = logging.getLogger(__name__)


def run(task: Task) -> None:
    """Drive a transcription end-to-end on a worker thread.

    Emits events into ``task.events``. The SSE endpoint consumes that queue
    on a separate thread and writes them to the HTTP response.
    """
    diarize_thread: threading.Thread | None = None
    # Either ``{"turns": [...]}`` on success or
    # ``{"error": str, "available": bool}`` on failure.
    diarize_holder: dict[str, Any] = {}

    def _run_diarize_bg() -> None:
        try:
            turns = diarize(task.audio_path, num_speakers=task.num_speakers)
            diarize_holder["turns"] = turns
        except DiarizationUnavailable as e:
            diarize_holder["error"] = str(e)
            diarize_holder["available"] = False
            logger.warning("Diarizacao indisponivel: %s", e)
        except Exception as e:
            diarize_holder["error"] = f"{type(e).__name__}: {e}"
            diarize_holder["available"] = True
            logger.exception("Erro inesperado na diarizacao em background")

    try:
        if task.diarize:
            diarize_thread = threading.Thread(
                target=_run_diarize_bg,
                name=f"diarize-{task.task_id[:8]}",
                daemon=True,
            )
            diarize_thread.start()
            logger.info("Diarizacao iniciada em paralelo (background)")

        for event in transcribe_stream(task.audio_path, model_size=task.model_size):
            task.events.put(event)
            if event["type"] == "done":
                task.transcription = event["result"]
            elif event["type"] == "error":
                task.error = event["message"]
                # Whisper failed — no transcript to diarise. The background
                # thread is daemonic and finishes on its own; we don't have a
                # cancel API for pyannote, so we just log and return.
                if diarize_thread is not None and diarize_thread.is_alive():
                    logger.info("Transcricao falhou; diarizacao em bg sera abandonada")
                return

        if task.diarize and task.transcription is not None and diarize_thread is not None:
            # Inform the client we're waiting on diarisation. In the common
            # case it already finished in background, so this join is
            # instantaneous.
            task.events.put({"type": "diarizing"})
            diarize_thread.join()

            if "turns" in diarize_holder:
                turns: list[SpeakerTurn] = diarize_holder["turns"]
                assignments = assign_speakers_to_segments(
                    task.transcription["segments"],
                    turns,
                )
                unique_speakers = sorted(set(assignments.values()))
                event = {
                    "type": "diarization_done",
                    "speakers": unique_speakers,
                    "assignments": assignments,
                    "turns": [
                        {"start": t.start, "end": t.end, "speaker": t.speaker} for t in turns
                    ],
                }
                task.diarization = event
                task.events.put(event)
            else:
                msg = diarize_holder.get("error", "Erro desconhecido na diarizacao")
                task.events.put({"type": "diarization_error", "message": msg})
    finally:
        task.mark_finished()
        # End-of-stream sentinel for the SSE generator.
        task.events.put(None)

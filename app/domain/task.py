"""Task — in-memory state of a transcription in progress."""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Task:
    """One row in the task manager.

    ``events`` is a thread-safe queue produced by the worker thread and
    consumed by the SSE stream endpoint. ``None`` sentinels the end of stream.
    """

    task_id: str
    audio_path: Path
    model_size: str
    diarize: bool = False
    num_speakers: int | None = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    events: queue.Queue[dict[str, Any] | None] = field(default_factory=queue.Queue)
    transcription: dict[str, Any] | None = None
    diarization: dict[str, Any] | None = None
    error: str | None = None

    def mark_finished(self) -> None:
        """Stamp the completion time. Used by the TTL eviction policy."""
        self.finished_at = time.monotonic()

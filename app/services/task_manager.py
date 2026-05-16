"""In-memory task registry with TTL-based eviction.

The original implementation kept tasks in a bare ``dict`` that grew without
bound — every transcription left its audio path, full segment list, and
diarisation in RAM forever. On a long session this is a memory leak.

This module solves it with two policies:

  1. **Soft TTL after completion**: a finished task stays around long enough
     for the client to fetch ``/download/{task_id}/...`` again, but is
     evicted afterwards.
  2. **Hard cap on size**: when the dict grows past ``MAX_TASKS``, the
     oldest finished tasks are dropped first.

Eviction runs opportunistically on every ``add`` / ``get`` — no background
thread. Cheap because the operation is O(n) over the (small) task dict.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from app.domain.task import Task

logger = logging.getLogger(__name__)

# Tunables. We keep them at module scope (rather than instance attrs) so
# tests can override via monkeypatch without subclassing the manager.
DEFAULT_TTL_SECONDS = 60 * 60  # 1h — covers a re-download from a stale tab
DEFAULT_MAX_TASKS = 64  # well above a typical user session


class TaskManager:
    """Thread-safe registry of in-flight and recently-finished tasks."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_tasks: int = DEFAULT_MAX_TASKS,
    ) -> None:
        self._ttl = ttl_seconds
        self._cap = max_tasks
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def add(self, task: Task) -> None:
        with self._lock:
            # Reserve room for the incoming task — otherwise we'd accept
            # the add and only evict on the *next* call, briefly exceeding
            # the cap.
            self._evict_locked(reserve=1)
            self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            self._evict_locked()
            return self._tasks.get(task_id)

    def __contains__(self, task_id: str) -> bool:
        return self.get(task_id) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def clear(self) -> None:
        """Drop everything. Used by tests; not invoked at runtime."""
        with self._lock:
            self._tasks.clear()

    def all(self) -> list[Task]:
        """Snapshot for diagnostics. Safe to iterate — it's a copy."""
        with self._lock:
            return list(self._tasks.values())

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def _evict_locked(self, reserve: int = 0) -> None:
        """Caller must hold ``self._lock``.

        ``reserve`` accounts for tasks the caller is about to insert: an
        ``add`` reserves 1 so eviction makes room *before* the dict grows.
        """
        now = time.monotonic()

        # Pass 1: drop tasks that finished long enough ago. Tasks still in
        # progress (``finished_at is None``) are never evicted by TTL.
        expired = [
            tid
            for tid, t in self._tasks.items()
            if t.finished_at is not None and now - t.finished_at > self._ttl
        ]
        for tid in expired:
            self._drop_locked(tid, reason="ttl")

        # Pass 2: drop the oldest finished tasks until we have room.
        # Unfinished tasks are never evicted — that would corrupt the SSE
        # stream the worker is still writing to.
        target = self._cap - reserve
        if len(self._tasks) <= target:
            return
        finished = sorted(
            (t for t in self._tasks.values() if t.finished_at is not None),
            key=lambda t: t.finished_at or 0,
        )
        excess = len(self._tasks) - target
        for t in finished[:excess]:
            self._drop_locked(t.task_id, reason="cap")

    def _drop_locked(self, task_id: str, reason: str) -> None:
        task = self._tasks.pop(task_id, None)
        if task is None:
            return
        # Best-effort cleanup of the uploaded file. Failure here is
        # logged but never propagated — eviction must always succeed.
        try:
            Path(task.audio_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(
                "Failed to remove upload for evicted task %s: %s",
                task_id,
                e,
            )
        logger.info("Task %s evicted (%s)", task_id, reason)

"""Tests for ``services.task_manager``.

The TaskManager replaced a bare global dict that leaked indefinitely.
These tests pin down the TTL + cap eviction policy so we don't regress
back into a leak.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.domain.task import Task
from app.services.task_manager import TaskManager


def _make_task(tmp_path: Path, task_id: str, finished: bool = False) -> Task:
    """Construct a Task with a real on-disk audio path so eviction's
    cleanup logic exercises the unlink path (silently no-ops on missing
    files thanks to ``missing_ok=True``)."""
    audio = tmp_path / f"{task_id}.wav"
    audio.write_bytes(b"")
    t = Task(task_id=task_id, audio_path=audio, model_size="medium")
    if finished:
        t.mark_finished()
    return t


class TestBasicOperations:
    def test_add_and_get(self, tmp_path: Path) -> None:
        mgr = TaskManager()
        t = _make_task(tmp_path, "abc")
        mgr.add(t)
        assert mgr.get("abc") is t

    def test_missing_returns_none(self) -> None:
        assert TaskManager().get("nope") is None

    def test_contains(self, tmp_path: Path) -> None:
        mgr = TaskManager()
        mgr.add(_make_task(tmp_path, "abc"))
        assert "abc" in mgr
        assert "nope" not in mgr

    def test_clear(self, tmp_path: Path) -> None:
        mgr = TaskManager()
        mgr.add(_make_task(tmp_path, "a"))
        mgr.add(_make_task(tmp_path, "b"))
        assert len(mgr) == 2
        mgr.clear()
        assert len(mgr) == 0


class TestTTLEviction:
    def test_unfinished_tasks_never_expire(self, tmp_path: Path) -> None:
        # TTL=0 would normally expire everything immediately. We assert
        # that unfinished tasks survive regardless.
        mgr = TaskManager(ttl_seconds=0.0)
        t = _make_task(tmp_path, "live", finished=False)
        mgr.add(t)
        # Trigger eviction by calling get/add again.
        mgr.get("live")
        assert "live" in mgr

    def test_finished_task_evicts_after_ttl(self, tmp_path: Path) -> None:
        mgr = TaskManager(ttl_seconds=0.05)
        t = _make_task(tmp_path, "done", finished=True)
        mgr.add(t)
        assert "done" in mgr
        time.sleep(0.1)
        # Adding any new task triggers eviction of expired ones.
        mgr.add(_make_task(tmp_path, "fresh"))
        assert "done" not in mgr

    def test_within_ttl_survives(self, tmp_path: Path) -> None:
        mgr = TaskManager(ttl_seconds=60.0)
        t = _make_task(tmp_path, "done", finished=True)
        mgr.add(t)
        # Don't sleep — eviction must not trip.
        mgr.add(_make_task(tmp_path, "fresh"))
        assert "done" in mgr


class TestCapEviction:
    def test_cap_drops_oldest_finished_first(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When over cap, finished tasks are dropped in age order. Unfinished
        ones survive — dropping a live stream would corrupt the SSE feed."""
        mgr = TaskManager(ttl_seconds=10_000.0, max_tasks=2)
        # Two finished tasks with distinct timestamps — anchor them to
        # ``time.monotonic()`` so the TTL pass doesn't classify them as ancient.
        now = time.monotonic()
        t1 = _make_task(tmp_path, "old", finished=True)
        t1.finished_at = now - 5.0  # finished 5s ago
        t2 = _make_task(tmp_path, "new", finished=True)
        t2.finished_at = now - 1.0  # finished 1s ago (newer)
        mgr.add(t1)
        mgr.add(t2)
        # Now exceed cap with a third — oldest finished must be evicted.
        mgr.add(_make_task(tmp_path, "third"))
        assert "old" not in mgr
        assert "new" in mgr
        assert "third" in mgr

    def test_cap_does_not_evict_unfinished(self, tmp_path: Path) -> None:
        mgr = TaskManager(ttl_seconds=10_000.0, max_tasks=1)
        t_live = _make_task(tmp_path, "live", finished=False)
        mgr.add(t_live)
        # Adding a second pushes us over cap, but live is unfinished so the
        # eviction pass must not touch it. Result: both stay.
        t_new = _make_task(tmp_path, "new", finished=False)
        mgr.add(t_new)
        assert "live" in mgr
        assert "new" in mgr


class TestUploadCleanup:
    def test_evicting_removes_audio_file(self, tmp_path: Path) -> None:
        mgr = TaskManager(ttl_seconds=0.0)
        t = _make_task(tmp_path, "done", finished=True)
        assert t.audio_path.exists()
        mgr.add(t)
        # Force expiration.
        time.sleep(0.01)
        mgr.add(_make_task(tmp_path, "fresh"))
        assert not t.audio_path.exists()

    def test_missing_audio_file_does_not_raise(self, tmp_path: Path) -> None:
        # Sometimes the file was already deleted (manual cleanup, restart).
        # Eviction must not crash on FileNotFoundError.
        mgr = TaskManager(ttl_seconds=0.0)
        t = _make_task(tmp_path, "done", finished=True)
        t.audio_path.unlink()  # delete *before* eviction
        mgr.add(t)
        time.sleep(0.01)
        mgr.add(_make_task(tmp_path, "fresh"))  # must not raise
        assert "done" not in mgr

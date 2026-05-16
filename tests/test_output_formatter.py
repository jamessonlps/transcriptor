"""Tests for ``services.output_formatter``.

Speaker labelling has three subtle behaviours we want pinned down:

  1. Without diarisation, the formatter falls back to the plain transcript.
  2. With diarisation, every speaker change inserts a header line.
  3. The ``labels`` map (UI-side renames) overrides the raw SPEAKER_xx tag.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.task import Task
from app.services import output_formatter


def _make_task_with_transcript(audio_path: Path) -> Task:
    """Build a Task with two short segments — enough to exercise both the
    "same speaker continues" and "speaker switch" branches."""
    t = Task(task_id="t", audio_path=audio_path, model_size="medium")
    t.transcription = {
        "full_text": "ola\nmundo",
        "srt": "1\n00:00:00,000 --> 00:00:01,000\nola\n\n2\n...",
        "segments": [
            {
                "index": 1,
                "start": 0.0,
                "end": 1.0,
                "start_ts": "00:00:00,000",
                "end_ts": "00:00:01,000",
                "text": "ola",
            },
            {
                "index": 2,
                "start": 1.0,
                "end": 2.0,
                "start_ts": "00:00:01,000",
                "end_ts": "00:00:02,000",
                "text": "mundo",
            },
        ],
    }
    return t


class TestBuildText:
    def test_no_diarisation_returns_full_text(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        # Without ``task.diarization``, callers use ``transcription["full_text"]``
        # directly; this function returns "" because it expects diarisation.
        # We assert on the contract: it doesn't crash and returns a string.
        assert output_formatter.build_text(t, {}) == "ola\nmundo"

    def test_speaker_header_inserted_on_change(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {
            "assignments": {1: "SPEAKER_00", 2: "SPEAKER_01"},
        }
        out = output_formatter.build_text(t, {})
        # Headers must reference the raw SPEAKER_xx tag when no labels given.
        assert "[SPEAKER_00]" in out
        assert "[SPEAKER_01]" in out
        # Segment text must be preserved.
        assert "ola" in out
        assert "mundo" in out

    def test_no_header_when_speaker_continues(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {
            "assignments": {1: "SPEAKER_00", 2: "SPEAKER_00"},
        }
        out = output_formatter.build_text(t, {})
        # Only one header line — speaker didn't change.
        assert out.count("[SPEAKER_00]") == 1

    def test_labels_override_raw_speaker(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {
            "assignments": {1: "SPEAKER_00", 2: "SPEAKER_01"},
        }
        out = output_formatter.build_text(
            t,
            {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        )
        assert "[Alice]" in out
        assert "[Bob]" in out
        assert "SPEAKER_00" not in out


class TestBuildSrt:
    def test_srt_blocks_have_speaker_prefix(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {
            "assignments": {1: "SPEAKER_00", 2: "SPEAKER_01"},
        }
        out = output_formatter.build_srt(t, {"SPEAKER_00": "Alice"})
        lines = out.split("\n")
        # Block 1 should have "[Alice]" prefix.
        assert lines[0] == "1"
        assert lines[1] == "00:00:00,000 --> 00:00:01,000"
        assert lines[2] == "[Alice] ola"
        # Block 2 keeps the raw SPEAKER_01 (not in labels).
        assert lines[4] == "2"
        assert lines[6] == "[SPEAKER_01] mundo"

    def test_no_speaker_no_prefix(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {"assignments": {}}
        out = output_formatter.build_srt(t, {})
        # Without a speaker assignment, the segment text is unprefixed.
        assert "[" not in out
        assert "ola" in out


class TestAssignmentsKeyType:
    """The diarisation JSON serialises assignment keys as strings (JSON
    spec); inside the worker thread they come in as int. The formatter
    must accept both — that bug was the original motivation for this
    helper module."""

    def test_string_keys(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {"assignments": {"1": "SPEAKER_00", "2": "SPEAKER_01"}}
        out = output_formatter.build_text(t, {})
        assert "[SPEAKER_00]" in out
        assert "[SPEAKER_01]" in out

    def test_int_keys(self, tmp_path: Path) -> None:
        t = _make_task_with_transcript(tmp_path / "a.wav")
        t.diarization = {"assignments": {1: "SPEAKER_00", 2: "SPEAKER_01"}}
        out = output_formatter.build_text(t, {})
        assert "[SPEAKER_00]" in out
        assert "[SPEAKER_01]" in out

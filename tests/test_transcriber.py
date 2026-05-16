"""Tests for ``app.transcriber`` — formatters, segment serialisation, eviction.

We never actually load a Whisper model. The point of these tests is to
verify pure logic that surrounds the ML inference: timestamps, SRT
serialisation, and the in-memory model eviction policy.
"""

from __future__ import annotations

import pytest

from app import transcriber
from app.transcriber import (
    Segment,
    TranscriptionResult,
    _detect_threads,
    evict_if_matches,
    format_timestamp,
)


class TestFormatTimestamp:
    """SRT timestamp format is HH:MM:SS,mmm. Easy to break with off-by-one
    ms rounding, so we cover the boundaries explicitly."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "00:00:00,000"),
            (0.001, "00:00:00,001"),
            (1.5, "00:00:01,500"),
            (61.0, "00:01:01,000"),
            (3600.0, "01:00:00,000"),
            (3661.234, "01:01:01,234"),
            # Rounding boundary — 0.9995 should round up to 1.000s
            (0.9995, "00:00:01,000"),
        ],
    )
    def test_known_values(self, seconds: float, expected: str) -> None:
        assert format_timestamp(seconds) == expected


class TestSegment:
    def test_to_dict_round_trip(self) -> None:
        seg = Segment(index=3, start=10.0, end=12.5, text="olá mundo")
        d = seg.to_dict()
        assert d == {
            "index": 3,
            "start": 10.0,
            "end": 12.5,
            "start_ts": "00:00:10,000",
            "end_ts": "00:00:12,500",
            "text": "olá mundo",
        }


class TestTranscriptionResult:
    def test_full_text_joins_segments_with_newlines(self) -> None:
        result = TranscriptionResult(
            segments=[
                Segment(1, 0.0, 1.0, "primeira"),
                Segment(2, 1.0, 2.0, "segunda"),
            ],
        )
        assert result.full_text == "primeira\nsegunda"

    def test_srt_emits_four_line_blocks(self) -> None:
        result = TranscriptionResult(
            segments=[
                Segment(1, 0.0, 1.5, "olá"),
                Segment(2, 1.5, 3.0, "mundo"),
            ],
        )
        lines = result.srt.split("\n")
        # Block: index, "start --> end", text, blank
        assert lines[0] == "1"
        assert lines[1] == "00:00:00,000 --> 00:00:01,500"
        assert lines[2] == "olá"
        assert lines[3] == ""
        assert lines[4] == "2"

    def test_empty_result(self) -> None:
        result = TranscriptionResult()
        assert result.full_text == ""
        assert result.srt == ""


class TestEvictIfMatches:
    """``_CURRENT_MODEL`` is module-global. Each test patches it explicitly
    instead of relying on import order."""

    def test_no_current_model_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transcriber, "_CURRENT_MODEL", None)
        monkeypatch.setattr(transcriber, "_CURRENT_KEY", None)
        assert evict_if_matches("medium") is False

    def test_evicts_matching_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Sentinel object stands in for a loaded WhisperModel — we never
        # touch its methods, only check that eviction nullifies the ref.
        sentinel = object()
        monkeypatch.setattr(transcriber, "_CURRENT_MODEL", sentinel)
        monkeypatch.setattr(transcriber, "_CURRENT_KEY", ("medium", "cpu", "int8", 4, 1))
        assert evict_if_matches("medium") is True
        assert transcriber._CURRENT_MODEL is None
        assert transcriber._CURRENT_KEY is None

    def test_does_not_evict_different_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        monkeypatch.setattr(transcriber, "_CURRENT_MODEL", sentinel)
        monkeypatch.setattr(transcriber, "_CURRENT_KEY", ("medium", "cpu", "int8", 4, 1))
        assert evict_if_matches("large-v3") is False
        assert transcriber._CURRENT_MODEL is sentinel


class TestDetectThreads:
    def test_respects_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHISPER_CPU_THREADS", "16")
        assert _detect_threads() == 16

    def test_ignores_invalid_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHISPER_CPU_THREADS", "not-a-number")
        # Falls back to auto-detect; just assert it's in the documented clamp [4, 8].
        n = _detect_threads()
        assert 4 <= n <= 8

    def test_clamp_lower_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHISPER_CPU_THREADS", raising=False)
        monkeypatch.setattr("os.cpu_count", lambda: 2)
        assert _detect_threads() == 4

    def test_clamp_upper_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHISPER_CPU_THREADS", raising=False)
        monkeypatch.setattr("os.cpu_count", lambda: 32)
        assert _detect_threads() == 8

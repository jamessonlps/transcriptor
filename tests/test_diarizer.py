"""Tests for ``app.diarizer.assign_speakers_to_segments``.

The overlap-assignment math is the heart of the diarisation pipeline: for
each Whisper segment, find which pyannote turn overlaps it the most. It's
also the only piece we can test deterministically without loading a real
model — so we cover it thoroughly.
"""

from __future__ import annotations

from app.diarizer import SpeakerTurn, assign_speakers_to_segments


def _seg(index: int, start: float, end: float) -> dict:
    """Build a minimal segment dict matching what Whisper emits."""
    return {"index": index, "start": start, "end": end, "text": "..."}


class TestAssignSpeakersToSegments:
    def test_single_turn_covers_segment(self) -> None:
        segments = [_seg(1, 0.0, 5.0)]
        turns = [SpeakerTurn(0.0, 10.0, "SPEAKER_00")]
        assert assign_speakers_to_segments(segments, turns) == {1: "SPEAKER_00"}

    def test_two_speakers_picks_larger_overlap(self) -> None:
        # Segment 0-10s. SPEAKER_00 covers 0-3 (3s), SPEAKER_01 covers 3-10 (7s).
        # SPEAKER_01 should win.
        segments = [_seg(1, 0.0, 10.0)]
        turns = [
            SpeakerTurn(0.0, 3.0, "SPEAKER_00"),
            SpeakerTurn(3.0, 10.0, "SPEAKER_01"),
        ]
        assert assign_speakers_to_segments(segments, turns) == {1: "SPEAKER_01"}

    def test_no_overlap_assigns_unknown(self) -> None:
        # Segment is entirely outside any turn.
        segments = [_seg(1, 100.0, 105.0)]
        turns = [SpeakerTurn(0.0, 50.0, "SPEAKER_00")]
        assert assign_speakers_to_segments(segments, turns) == {1: "SPEAKER_UNKNOWN"}

    def test_partial_overlap_accumulates_per_speaker(self) -> None:
        # SPEAKER_00 contributes two separate windows totalling 4s; SPEAKER_01
        # contributes one window of 3s. SPEAKER_00 wins on cumulative time.
        segments = [_seg(1, 0.0, 10.0)]
        turns = [
            SpeakerTurn(0.0, 2.0, "SPEAKER_00"),  # 2s
            SpeakerTurn(2.0, 5.0, "SPEAKER_01"),  # 3s
            SpeakerTurn(5.0, 7.0, "SPEAKER_00"),  # 2s — SPEAKER_00 total = 4s
        ]
        assert assign_speakers_to_segments(segments, turns) == {1: "SPEAKER_00"}

    def test_multiple_segments(self) -> None:
        segments = [
            _seg(1, 0.0, 2.0),
            _seg(2, 2.0, 5.0),
            _seg(3, 5.0, 8.0),
        ]
        turns = [
            SpeakerTurn(0.0, 2.5, "SPEAKER_00"),
            SpeakerTurn(2.5, 8.0, "SPEAKER_01"),
        ]
        result = assign_speakers_to_segments(segments, turns)
        assert result == {
            1: "SPEAKER_00",
            2: "SPEAKER_01",  # overlaps 2.0-2.5 (0.5s) vs 2.5-5.0 (2.5s)
            3: "SPEAKER_01",
        }

    def test_zero_length_turn_is_ignored(self) -> None:
        # Edge case: a turn with start == end shouldn't crash and shouldn't
        # take precedence over a real overlap.
        segments = [_seg(1, 0.0, 5.0)]
        turns = [
            SpeakerTurn(0.0, 0.0, "SPEAKER_00"),
            SpeakerTurn(1.0, 4.0, "SPEAKER_01"),
        ]
        assert assign_speakers_to_segments(segments, turns) == {1: "SPEAKER_01"}

    def test_empty_inputs(self) -> None:
        assert assign_speakers_to_segments([], []) == {}

    def test_empty_turns(self) -> None:
        segments = [_seg(1, 0.0, 5.0), _seg(2, 5.0, 10.0)]
        result = assign_speakers_to_segments(segments, [])
        assert result == {1: "SPEAKER_UNKNOWN", 2: "SPEAKER_UNKNOWN"}

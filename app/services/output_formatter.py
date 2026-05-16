"""Build TXT and SRT downloads, with optional speaker labels.

A subtle detail: ``assignments`` may have JSON-string keys (sent by the
frontend) or int keys (when called server-side). We accept both forms so
this function works regardless of who built the dict.
"""

from __future__ import annotations

from typing import Any

from app.domain.task import Task


def _speaker_for_segment(
    seg_index: int,
    assignments: dict[Any, str],
    labels: dict[str, str],
) -> str | None:
    """Resolve the user-facing speaker label for one segment, if any."""
    raw = assignments.get(str(seg_index)) or assignments.get(seg_index)
    if not raw:
        return None
    return labels.get(raw, raw)


def build_text(task: Task, labels: dict[str, str]) -> str:
    """Plain text with `[Falante X]` headers when speakers change."""
    if task.transcription is None:
        return ""
    assignments: dict[Any, str] = task.diarization["assignments"] if task.diarization else {}
    lines: list[str] = []
    last_speaker: str | None = None
    for seg in task.transcription["segments"]:
        speaker = _speaker_for_segment(seg["index"], assignments, labels)
        if speaker and speaker != last_speaker:
            lines.append(f"\n[{speaker}]")
            last_speaker = speaker
        lines.append(seg["text"])
    return "\n".join(lines).strip()


def build_srt(task: Task, labels: dict[str, str]) -> str:
    """SRT subtitle file, prefixing each segment with `[Falante X]` when known."""
    if task.transcription is None:
        return ""
    assignments: dict[Any, str] = task.diarization["assignments"] if task.diarization else {}
    lines: list[str] = []
    for seg in task.transcription["segments"]:
        speaker = _speaker_for_segment(seg["index"], assignments, labels)
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(str(seg["index"]))
        lines.append(f"{seg['start_ts']} --> {seg['end_ts']}")
        lines.append(f"{prefix}{seg['text']}")
        lines.append("")
    return "\n".join(lines)

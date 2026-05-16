"""Model catalog: list, download (SSE progress), delete."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models import delete_model, download_model, list_models, summary_line
from app.services.sse import STREAM_HEADERS, format_event

router = APIRouter(prefix="/api/models", tags=["models"])

# Per-key locks: prevent concurrent downloads of the same model from
# clashing in the HF cache (snapshot_download is not atomic). Different
# keys can still download in parallel.
_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()


def _get_download_lock(key: str) -> threading.Lock:
    with _DOWNLOAD_LOCKS_GUARD:
        if key not in _DOWNLOAD_LOCKS:
            _DOWNLOAD_LOCKS[key] = threading.Lock()
        return _DOWNLOAD_LOCKS[key]


@router.get("")
def models_list() -> dict[str, Any]:
    """Full catalog with per-model status."""
    return list_models()


@router.get("/summary")
def models_summary() -> dict[str, str]:
    return {"summary": summary_line()}


@router.get("/{key}/download")
def models_download(key: str) -> StreamingResponse:
    """Download a model, emitting progress via SSE.

    GET (not POST) because ``EventSource`` only supports GET. Idempotent
    cache-wise: if the model is already on disk, ``snapshot_download`` is
    essentially a no-op (~50ms verifying hashes).
    """
    lock = _get_download_lock(key)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, f"Download de '{key}' ja em andamento.")

    def gen():
        try:
            for event in download_model(key):
                yield format_event(event)
        finally:
            lock.release()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@router.delete("/{key}")
def models_delete(key: str) -> dict[str, Any]:
    """Remove the model cache from disk (and from RAM if loaded)."""
    try:
        return delete_model(key)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None

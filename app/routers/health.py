"""Health probe + capability discovery.

``/api/health`` is used by the Docker HEALTHCHECK directive — keep it cheap
and synchronous. ``/api/config`` is consumed by the SPA on boot to decide
which features to expose.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import __version__, token_store

router = APIRouter(tags=["health"])

# Whisper sizes accepted by ``POST /api/transcribe``. Anything else returns 400.
ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/config")
def config() -> dict[str, Any]:
    """What the frontend needs to know at boot.

    ``diarization_available`` here only reflects "is there a token at all".
    The real validation (token accepted + per-repo terms accepted) is done
    on demand by ``/api/hf/status`` and ``/api/hf/access`` — those are
    heavier and we don't want every page load to pay that cost.
    """
    return {
        "diarization_available": bool(token_store.get_token()),
        "models": sorted(ALLOWED_MODELS),
        "version": __version__,
    }

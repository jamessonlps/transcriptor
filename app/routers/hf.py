"""Hugging Face token + access endpoints.

The token is persisted by ``app.token_store``; here we just wire the HTTP
shape and call into ``services.hf_client`` for the actual network calls.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import diarizer as _diarizer_mod
from app import token_store
from app.models import DIARIZATION_MODELS
from app.services import hf_client

router = APIRouter(prefix="/api/hf", tags=["hf"])


class _TokenPayload(BaseModel):
    """Body for ``POST /api/hf/token``."""

    token: str


@router.get("/status")
def hf_status() -> dict[str, Any]:
    """Token state: present? valid? from where? for whom?

    Calls HF ``whoami`` — typically 200-800ms. Not cached: this endpoint is
    usually hit *after* the user changed something (saved token, accepted
    terms), and we want to reflect reality.
    """
    token = token_store.get_token()
    source = token_store.get_source()
    payload: dict[str, Any] = {
        "configured": bool(token),
        "valid": None,
        "source": source,
        "username": None,
        "masked": token_store.mask(token) if token else None,
        "error": None,
        "config_path": str(token_store.CONFIG_FILE),
    }
    if not token:
        return payload
    try:
        info = hf_client.whoami(token)
        payload["valid"] = True
        payload["username"] = info.get("name") or info.get("fullname")
    except HTTPException as e:
        # whoami failed -> token present but invalid. Don't propagate as
        # an HTTP error: we want the UI to render the real state.
        payload["valid"] = False
        payload["error"] = e.detail
    return payload


@router.post("/token")
def hf_set_token(payload: _TokenPayload) -> dict[str, Any]:
    """Receive a token, validate via whoami, persist, refresh the env."""
    raw = (payload.token or "").strip()
    if not raw:
        raise HTTPException(400, "Campo 'token' e obrigatorio.")
    if not raw.startswith("hf_"):
        raise HTTPException(400, "Token Hugging Face deve comecar com 'hf_'.")
    if any(c.isspace() for c in raw):
        raise HTTPException(400, "Token nao pode conter espacos ou quebras de linha.")

    info = hf_client.whoami(raw)

    try:
        token_store.set_token(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    # Reset pyannote pipeline — without this the next diarisation would
    # still use the old token cached in RAM.
    _diarizer_mod.reset_pipeline()

    return {
        "configured": True,
        "valid": True,
        "source": token_store.get_source(),
        "username": info.get("name") or info.get("fullname"),
        "masked": token_store.mask(raw),
    }


@router.delete("/token")
def hf_delete_token() -> dict[str, Any]:
    """Remove the saved token. Falls back to .env if one is present."""
    token_store.delete_token()
    _diarizer_mod.reset_pipeline()
    new_token = token_store.get_token()
    return {
        "removed": True,
        "configured": bool(new_token),
        "source": token_store.get_source(),
        "masked": token_store.mask(new_token) if new_token else None,
    }


@router.get("/access")
def hf_access() -> dict[str, Any]:
    """Per-repo access check for the gated models we need.

    For each repo in ``DIARIZATION_MODELS``:
      - 200 -> terms accepted
      - 403/404 -> terms not accepted (presented as "gated")
      - 401 -> token invalid
      - timeout/network error -> reported so the UI can show useful feedback
    """
    token = token_store.get_token()
    has_token = bool(token)

    items: list[dict[str, Any]] = []
    for spec in DIARIZATION_MODELS:
        item: dict[str, Any] = {
            "key": spec.key,
            "repo_id": spec.repo_id,
            "label": spec.label,
            "url": f"https://huggingface.co/{spec.repo_id}",
            "accessible": False,
            "reason": None,
        }
        if not has_token or token is None:
            item["reason"] = "no_token"
        else:
            reason = hf_client.check_repo_access(token, spec.repo_id)
            if reason is None:
                item["accessible"] = True
            else:
                item["reason"] = reason
        items.append(item)
    return {"models": items, "has_token": has_token}

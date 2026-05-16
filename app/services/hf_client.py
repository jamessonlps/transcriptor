"""Thin Hugging Face HTTP client.

We call HF directly via ``requests`` (rather than ``HfApi``) for one reason:
``HfApi.whoami()`` doesn't expose a timeout, so it hangs indefinitely when
HF is slow or unreachable. A web UI that blocks on a never-responding call
is a worse UX than one that fails fast.

All exposed functions raise ``fastapi.HTTPException`` so routers can simply
re-raise. That coupling is acceptable here because this service is
specifically the bridge between HF and the HTTP layer.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from fastapi import HTTPException

logger = logging.getLogger(__name__)

HF_API_BASE = "https://huggingface.co"

# Short timeout: the UI calls these synchronously, so a hanging request
# would translate to an infinite spinner. 8s covers slow connections while
# staying under the threshold where users assume the app is broken.
HF_API_TIMEOUT = 8.0


def whoami(token: str) -> dict[str, Any]:
    """Verify the token and return account info.

    Raises ``HTTPException`` with a status code the UI can render:
        401 — token rejected (revoked, wrong, or missing scope)
        502 — HF unreachable / DNS failure
        504 — HF timeout
        500 — HF returned non-JSON (very rare)
    """
    try:
        resp = requests.get(
            f"{HF_API_BASE}/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=HF_API_TIMEOUT,
        )
    except requests.Timeout:
        raise HTTPException(
            504,
            f"Hugging Face nao respondeu em {HF_API_TIMEOUT:.0f}s. "
            "Verifique sua conexao com a internet e tente novamente.",
        ) from None
    except requests.RequestException as e:
        raise HTTPException(
            502,
            f"Falha conectando ao Hugging Face: {type(e).__name__}.",
        ) from None
    if resp.status_code in (401, 403):
        raise HTTPException(
            401,
            "Token rejeitado pelo Hugging Face. Verifique se copiou o token "
            "completo e se ele ainda esta ativo em huggingface.co/settings/tokens.",
        ) from None
    if not resp.ok:
        raise HTTPException(
            resp.status_code,
            f"Hugging Face retornou HTTP {resp.status_code}.",
        ) from None
    try:
        data: dict[str, Any] = resp.json()
        return data
    except ValueError:
        raise HTTPException(500, "Resposta invalida do Hugging Face.") from None


def check_repo_access(token: str, repo_id: str) -> str | None:
    """Return ``None`` if the repo is accessible, else a reason string.

    Reasons mirror what the UI displays:
        ``"timeout"`` / ``"network_error"`` — transport failure
        ``"unauthorized"`` — token invalid (HTTP 401)
        ``"gated"`` — terms not accepted (HTTP 403 or 404)
        ``"http_error"`` — unexpected status
    """
    try:
        resp = requests.get(
            f"{HF_API_BASE}/api/models/{repo_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=HF_API_TIMEOUT,
        )
    except requests.Timeout:
        return "timeout"
    except requests.RequestException:
        return "network_error"
    if resp.ok:
        return None
    if resp.status_code == 401:
        return "unauthorized"
    # HF returns 403 (gated) or 404 (private repo hidden to your token).
    # We collapse both into "gated" — the user action is the same: accept terms.
    if resp.status_code in (403, 404):
        return "gated"
    logger.warning("HTTP %s checando acesso a %s", resp.status_code, repo_id)
    return "http_error"

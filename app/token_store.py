"""Gerenciamento do token Hugging Face do usuario.

Armazena o token em ``~/.cache/transcriptor/config.json`` (ou em
``$TRANSCRIPTOR_CONFIG_DIR/config.json`` se a variavel estiver setada).

Prioridade do token ativo (maior primeiro):
    1. Token salvo via UI (config.json — sob controle do usuario).
    2. Token vindo de variavel de ambiente (.env ou shell, lido no startup).

Se o usuario remove o token via UI, voltamos automaticamente pro valor da
variavel de ambiente (se houver). Isso evita que clicar em "remover" deixe a
app num estado bagunçado quando ainda existe um token herdado.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Localizacao do arquivo de config:
#   - $TRANSCRIPTOR_CONFIG_DIR/config.json se setado
#   - ~/.cache/transcriptor/config.json por default
_DEFAULT_DIR = Path.home() / ".cache" / "transcriptor"
CONFIG_DIR = Path(os.environ.get("TRANSCRIPTOR_CONFIG_DIR") or _DEFAULT_DIR)
CONFIG_FILE = CONFIG_DIR / "config.json"

# Tokens HF começam com "hf_" — checamos só o prefixo localmente; a validacao
# real (HTTP whoami) decide se ele é aceito de fato.
_TOKEN_PREFIX = "hf_"

# Snapshot do token vindo do ambiente no startup. Permite voltar pro valor
# original (do .env ou do shell) quando o usuario remove o token via UI.
_ENV_TOKEN: str | None = None
_INITIALIZED = False


def initialize() -> None:
    """Chame uma vez no startup do servidor, apos ``load_dotenv``.

    Snapshot do token vindo do ambiente e, se houver token salvo no arquivo,
    sobrescreve em RAM (via os.environ) pra que toda a stack — huggingface_hub,
    pyannote, faster_whisper — passe a enxergar o token "novo" sem precisar de
    restart.
    """
    global _ENV_TOKEN, _INITIALIZED
    if _INITIALIZED:
        return
    _ENV_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    file_token = _read_file_token()
    if file_token:
        os.environ["HF_TOKEN"] = file_token
    _INITIALIZED = True
    logger.info(
        "Token store inicializado: env_token=%s file_token=%s config=%s",
        bool(_ENV_TOKEN),
        bool(file_token),
        CONFIG_FILE,
    )


def get_token() -> str | None:
    """Token ativo (qualquer que seja a fonte)."""
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or None


def get_source() -> str | None:
    """De onde veio o token ativo: 'ui' (arquivo), 'env' (variavel) ou None."""
    if _read_file_token():
        return "ui"
    if _ENV_TOKEN:
        return "env"
    return None


def set_token(token: str) -> None:
    """Salva o token no arquivo e atualiza ``os.environ`` imediatamente.

    Nao valida contra a API do HF — quem chama deve ter validado antes
    (com ``HfApi.whoami()`` por exemplo). Aqui so cuidamos da persistencia
    e da propagacao em RAM.
    """
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("Token vazio.")
    if not cleaned.startswith(_TOKEN_PREFIX):
        raise ValueError("Token deve comecar com 'hf_'.")
    if any(c.isspace() for c in cleaned):
        raise ValueError("Token nao pode conter espacos.")
    _write_file_token(cleaned)
    os.environ["HF_TOKEN"] = cleaned
    logger.info("Token HF atualizado via UI (mascarado: %s)", mask(cleaned))


def delete_token() -> None:
    """Remove o token do arquivo de config e restaura o do ambiente, se houver."""
    _write_file_token(None)
    if _ENV_TOKEN:
        os.environ["HF_TOKEN"] = _ENV_TOKEN
        logger.info("Token HF removido via UI; voltando pro valor original do .env")
    else:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGINGFACE_TOKEN", None)
        logger.info("Token HF removido via UI; nao havia fallback no ambiente")


def mask(token: str | None) -> str:
    """Versao ofuscada (para UI/log)."""
    if not token:
        return ""
    if len(token) <= 10:
        return "***"
    return f"{token[:5]}…{token[-4:]}"


# ---------- Internas ----------


def _read_file_token() -> str | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Falha lendo config (%s): %s", CONFIG_FILE, e)
        return None
    if not isinstance(data, dict):
        return None
    tok = data.get("hf_token")
    if isinstance(tok, str) and tok.strip():
        return tok.strip()
    return None


def _write_file_token(token: str | None) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except (OSError, json.JSONDecodeError):
            pass
    if token is None:
        data.pop("hf_token", None)
    else:
        data["hf_token"] = token
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # 0600: o token e segredo; ninguem alem do dono deve ler o arquivo.
    # Windows ignora chmod fora do bit de readonly — suprimimos o erro.
    with contextlib.suppress(OSError):
        CONFIG_FILE.chmod(0o600)


__all__ = [
    "CONFIG_DIR",
    "CONFIG_FILE",
    "delete_token",
    "get_source",
    "get_token",
    "initialize",
    "mask",
    "set_token",
]

"""Shared pytest fixtures.

Two design goals here:

  1. **Never load real ML models.** The point of unit tests is to verify
     domain logic — overlap math, formatters, state transitions — not the
     ML libraries themselves. We monkeypatch ``transcriber.get_model`` and
     ``diarizer.get_pipeline`` so tests run in <1 second on CI.

  2. **Isolate disk side-effects.** Token store and HF cache use absolute
     paths under ``~/.cache``; we redirect them to a temp directory per test
     to keep CI hermetic and the developer's local cache untouched.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest


def _scrub_hf_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise any HF tokens the developer has set locally.

    Subtle interaction: ``app.main`` calls ``load_dotenv()`` at import time,
    and ``load_dotenv`` defaults to ``override=False`` — meaning if we
    *delete* the env var, the .env file repopulates it. Setting it to an
    empty string keeps it "defined but falsy" so:

      - ``token_store.get_token()`` returns ``None`` (empty string is falsy
        in the ``or`` chain)
      - ``load_dotenv`` skips it because it's already set
    """
    for var in ("HF_TOKEN", "HUGGINGFACE_TOKEN"):
        monkeypatch.setenv(var, "")


@pytest.fixture
def tmp_token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``token_store`` to a temp directory.

    Reimports the module so the module-level ``CONFIG_DIR`` constant picks
    up the new env var. Without the reload, tests would share whatever path
    was resolved at first import.
    """
    monkeypatch.setenv("TRANSCRIPTOR_CONFIG_DIR", str(tmp_path / "config"))
    _scrub_hf_env(monkeypatch)

    from app import token_store

    importlib.reload(token_store)
    token_store._INITIALIZED = False
    token_store._ENV_TOKEN = None
    return tmp_path


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip HF-related env vars so tests don't accidentally inherit a token
    from the developer's shell."""
    _scrub_hf_env(monkeypatch)
    monkeypatch.delenv("TRANSCRIPTOR_CONFIG_DIR", raising=False)
    yield

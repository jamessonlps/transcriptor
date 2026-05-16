"""Tests for ``app.token_store``.

The token store is small but its priority logic (UI file > env > none) and
its delete-falls-back-to-env behaviour have caused real bugs in the past.
These tests pin down the contract.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from app import token_store


def _read_config(tmp_path: Path) -> dict:
    """Read the config file directly to verify what was persisted."""
    cfg = tmp_path / "config" / "config.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text(encoding="utf-8"))


class TestMask:
    def test_none_returns_empty(self) -> None:
        assert token_store.mask(None) == ""

    def test_short_token_returns_stars(self) -> None:
        assert token_store.mask("short") == "***"

    def test_long_token_shows_head_and_tail(self) -> None:
        masked = token_store.mask("hf_abcdefghij1234567890")
        assert masked.startswith("hf_ab")
        assert masked.endswith("7890")
        # Verify it doesn't leak the middle.
        assert "cdef" not in masked


class TestInitialize:
    def test_idempotent(self, tmp_token_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HF_TOKEN", "hf_from_env")
        token_store.initialize()
        first_snapshot = token_store._ENV_TOKEN
        token_store.initialize()  # second call — should not re-snapshot
        assert first_snapshot == token_store._ENV_TOKEN

    def test_env_only(self, tmp_token_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HF_TOKEN", "hf_from_env")
        token_store.initialize()
        assert token_store.get_token() == "hf_from_env"
        assert token_store.get_source() == "env"

    def test_file_overrides_env(
        self,
        tmp_token_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pre-write a file token, set an env token, then initialise.
        # The file should win and the env should be remembered as fallback.
        cfg_dir = tmp_token_dir / "config"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(
            json.dumps({"hf_token": "hf_from_file"}),
        )
        monkeypatch.setenv("HF_TOKEN", "hf_from_env")
        token_store.initialize()
        assert token_store.get_token() == "hf_from_file"
        assert token_store.get_source() == "ui"
        # The env snapshot is what makes "delete" fall back gracefully.
        assert token_store._ENV_TOKEN == "hf_from_env"

    def test_no_token_anywhere(self, tmp_token_dir: Path) -> None:
        token_store.initialize()
        assert token_store.get_token() is None
        assert token_store.get_source() is None


class TestSetToken:
    def test_persists_to_file(self, tmp_token_dir: Path) -> None:
        token_store.initialize()
        token_store.set_token("hf_validtokenABCDEF")
        assert _read_config(tmp_token_dir) == {"hf_token": "hf_validtokenABCDEF"}
        assert token_store.get_token() == "hf_validtokenABCDEF"

    def test_file_permission_is_0600(self, tmp_token_dir: Path) -> None:
        """The token is a secret. On POSIX, only the owner should be able to
        read it. We can't check this on Windows, so we skip there."""
        if os.name != "posix":
            pytest.skip("POSIX-only permission check")
        token_store.initialize()
        token_store.set_token("hf_validtokenABCDEF")
        cfg = tmp_token_dir / "config" / "config.json"
        mode = stat.S_IMODE(cfg.stat().st_mode)
        assert mode == 0o600

    def test_rejects_empty(self, tmp_token_dir: Path) -> None:
        token_store.initialize()
        with pytest.raises(ValueError, match="vazio"):
            token_store.set_token("   ")

    def test_rejects_wrong_prefix(self, tmp_token_dir: Path) -> None:
        token_store.initialize()
        with pytest.raises(ValueError, match="hf_"):
            token_store.set_token("nope_abc123")

    def test_rejects_whitespace(self, tmp_token_dir: Path) -> None:
        token_store.initialize()
        with pytest.raises(ValueError, match="espacos"):
            token_store.set_token("hf_has space")


class TestDeleteToken:
    def test_falls_back_to_env(
        self,
        tmp_token_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HF_TOKEN", "hf_from_env")
        token_store.initialize()
        token_store.set_token("hf_from_ui_typed_in")
        # Token from UI is now active.
        assert token_store.get_token() == "hf_from_ui_typed_in"
        token_store.delete_token()
        # After delete, the file token is gone but env survives.
        assert token_store.get_token() == "hf_from_env"
        assert token_store.get_source() == "env"

    def test_no_fallback_when_no_env(self, tmp_token_dir: Path) -> None:
        token_store.initialize()
        token_store.set_token("hf_only_in_ui_no_env")
        token_store.delete_token()
        assert token_store.get_token() is None
        assert token_store.get_source() is None

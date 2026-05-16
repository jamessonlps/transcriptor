"""Tests for ``app.models`` — model catalog, status detection, deletion.

We exercise the side-effect-free pieces (catalog lookup, status detection
with a fake HF cache layout) and stub the heavy operations
(``snapshot_download``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import models


class TestGetSpec:
    def test_known_keys(self) -> None:
        spec = models.get_spec("medium")
        assert spec.kind == "whisper"
        assert spec.repo_id == "Systran/faster-whisper-medium"

    def test_diarization_key(self) -> None:
        spec = models.get_spec("pyannote-community-1")
        assert spec.kind == "diarization"

    def test_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            models.get_spec("nonexistent-model")


class TestModelStatus:
    """Simulates the HF cache directory layout to exercise status detection
    without actually downloading anything."""

    def _make_fake_cache(
        self,
        root: Path,
        repo_id: str,
        file_name: str = "model.bin",
    ) -> Path:
        """Mirror the HF cache layout: ``models--<org>--<repo>/snapshots/<hash>/<file>``."""
        repo_dir = root / f"models--{repo_id.replace('/', '--')}"
        snap = repo_dir / "snapshots" / "deadbeef"
        snap.mkdir(parents=True)
        (snap / file_name).write_bytes(b"x" * 1024)  # 1 KB sentinel
        return repo_dir

    def test_missing_is_not_downloaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        status = models.model_status(models.get_spec("medium"))
        assert status["downloaded"] is False
        assert status["size_bytes_actual"] == 0

    def test_present_with_bin_is_downloaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = models.get_spec("medium")
        self._make_fake_cache(tmp_path, spec.repo_id, "model.bin")
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        status = models.model_status(spec)
        assert status["downloaded"] is True
        assert status["size_bytes_actual"] > 0
        assert status["path"] is not None

    def test_present_with_safetensors_is_downloaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = models.get_spec("medium")
        self._make_fake_cache(tmp_path, spec.repo_id, "model.safetensors")
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        assert models.model_status(spec)["downloaded"] is True

    def test_pyannote_with_config_yaml_is_downloaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # pyannote ships config.yaml; we detect that as a valid model file.
        spec = models.get_spec("pyannote-community-1")
        self._make_fake_cache(tmp_path, spec.repo_id, "config.yaml")
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        assert models.model_status(spec)["downloaded"] is True

    def test_directory_without_weights_is_not_downloaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Just an empty repo dir — a half-failed download leaves this state.
        spec = models.get_spec("medium")
        repo_dir = tmp_path / f"models--{spec.repo_id.replace('/', '--')}"
        (repo_dir / "snapshots" / "deadbeef").mkdir(parents=True)
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        assert models.model_status(spec)["downloaded"] is False


class TestListModels:
    def test_groups_by_kind(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        data = models.list_models()
        assert {m["kind"] for m in data["whisper"]} == {"whisper"}
        assert {m["kind"] for m in data["diarization"]} == {"diarization"}
        assert data["total_bytes"] == 0  # nothing downloaded


class TestDeleteModel:
    def test_not_downloaded_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        result = models.delete_model("medium")
        assert result["removed"] is False
        assert result["reason"] == "not_downloaded"

    def test_removes_existing_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = models.get_spec("medium")
        repo_dir = tmp_path / f"models--{spec.repo_id.replace('/', '--')}"
        (repo_dir / "snapshots" / "deadbeef").mkdir(parents=True)
        (repo_dir / "blobs").mkdir()
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        # Stub the eviction call — transcriber state is exercised in its own suite.
        monkeypatch.setattr(models.transcriber, "evict_if_matches", lambda _k: False)
        result = models.delete_model("medium")
        assert result["removed"] is True
        assert not repo_dir.exists()

    def test_unknown_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            models.delete_model("nope")


class TestSummaryLine:
    def test_zero_models_in_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path)
        line = models.summary_line()
        assert "0/" in line
        assert "GB" in line


class TestSilentTqdm:
    """The tqdm stub satisfies a fragile contract — it must not raise when
    huggingface_hub calls its methods under the hood. We exercise the
    documented protocol pieces explicitly."""

    def test_iterable_passthrough(self) -> None:
        items = list(models._SilentTqdm(iterable=[1, 2, 3]))
        assert items == [1, 2, 3]

    def test_update_accumulates(self) -> None:
        bar = models._SilentTqdm(total=100)
        bar.update(10)
        bar.update(20)
        assert bar.n == 30

    def test_context_manager(self) -> None:
        with models._SilentTqdm(total=10) as bar:
            bar.update(5)
        # No exception means we're good.

    def test_no_op_methods_dont_raise(self) -> None:
        bar = models._SilentTqdm()
        bar.refresh()
        bar.clear()
        bar.display()
        bar.write("anything")
        bar.set_description("desc")
        bar.set_postfix(loss=0.1)
        # format_dict must return something dict-like for hf_hub to inspect.
        assert isinstance(bar.format_dict, dict)

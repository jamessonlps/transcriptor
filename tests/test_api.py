"""Integration tests for the FastAPI app via TestClient.

We test the HTTP contract: status codes, payload shapes, validation. The
underlying ML calls are stubbed — the goal is to verify the boundary
behaviour, not to re-test pieces covered by the unit suites.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(
    tmp_path: Path,
    tmp_token_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """Boot the FastAPI app with disk side-effects redirected to a tmp dir.

    Note: we import ``main`` *inside* the fixture (not at module level) so
    each test gets a fresh module state — important because ``_TASKS``,
    ``_DOWNLOAD_LOCKS``, and the HF cache helpers are module-global.
    """
    # Neutralise the project's .env: ``main`` calls ``load_dotenv()`` at
    # import time, which would repopulate ``HF_TOKEN`` from disk even after
    # ``tmp_token_dir`` cleared the env. Patching to a no-op keeps tests
    # hermetic regardless of what's on the developer's filesystem.
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: True)

    import importlib

    from app import main as main_module

    importlib.reload(main_module)
    # ``main`` re-initialises the token store at module level — re-scrub
    # any env that load_dotenv (now a no-op) left in place.
    main_module.token_store._INITIALIZED = False
    main_module.token_store._ENV_TOKEN = None
    monkeypatch.setattr(main_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main_module, "OUTPUT_DIR", tmp_path / "outputs")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "outputs").mkdir()
    # Wipe any tasks that survived the module reload.
    from app.routers import transcribe as transcribe_router

    transcribe_router.tasks.clear()

    with TestClient(main_module.app) as c:
        yield c


class TestHealth:
    def test_health_endpoint(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestConfig:
    def test_shape_when_no_token(self, client: TestClient) -> None:
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert data["diarization_available"] is False
        assert "medium" in data["models"]
        assert "large-v3" in data["models"]
        assert data["version"]  # whatever it is, must be truthy

    def test_version_matches_package(self, client: TestClient) -> None:
        from app import __version__

        r = client.get("/api/config")
        assert r.json()["version"] == __version__


class TestHfStatus:
    def test_no_token_configured(self, client: TestClient) -> None:
        r = client.get("/api/hf/status")
        assert r.status_code == 200
        data = r.json()
        assert data["configured"] is False
        assert data["valid"] is None
        assert data["username"] is None


class TestHfSetToken:
    def test_rejects_empty_token(self, client: TestClient) -> None:
        r = client.post("/api/hf/token", json={"token": ""})
        assert r.status_code == 400

    def test_rejects_invalid_prefix(self, client: TestClient) -> None:
        r = client.post("/api/hf/token", json={"token": "wrong_prefix_abc"})
        assert r.status_code == 400
        assert "hf_" in r.json()["detail"]

    def test_rejects_whitespace(self, client: TestClient) -> None:
        r = client.post("/api/hf/token", json={"token": "hf_has space"})
        assert r.status_code == 400

    def test_accepts_when_hf_validates(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stub the HF whoami call so we don't hit the network."""

        def fake_whoami(_token: str) -> dict[str, Any]:
            return {"name": "test-user", "fullname": "Test User"}

        # Patch both the source module *and* the binding inside the hf router,
        # since ``from x import y`` creates an independent reference.
        from app.routers import hf as hf_router
        from app.services import hf_client

        monkeypatch.setattr(hf_client, "whoami", fake_whoami)
        monkeypatch.setattr(hf_router.hf_client, "whoami", fake_whoami)
        r = client.post("/api/hf/token", json={"token": "hf_validtokenABCDEF12345"})
        assert r.status_code == 200
        data = r.json()
        assert data["configured"] is True
        assert data["valid"] is True
        assert data["username"] == "test-user"


class TestTranscribeValidation:
    def test_rejects_unknown_model(self, client: TestClient) -> None:
        files = {"file": ("audio.wav", b"fake content", "audio/wav")}
        r = client.post(
            "/api/transcribe",
            files=files,
            data={"model": "ultra-pro-max", "diarize": "false"},
        )
        assert r.status_code == 400
        assert "Modelo invalido" in r.json()["detail"]

    def test_rejects_diarize_without_token(self, client: TestClient) -> None:
        files = {"file": ("audio.wav", b"fake content", "audio/wav")}
        r = client.post(
            "/api/transcribe",
            files=files,
            data={"model": "medium", "diarize": "true"},
        )
        assert r.status_code == 400
        assert "token" in r.json()["detail"].lower()


class TestModelsListing:
    def test_list_returns_catalog(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from app import models

        monkeypatch.setattr(models, "_hf_cache_root", lambda: tmp_path / "hf-cache")
        r = client.get("/api/models")
        assert r.status_code == 200
        data = r.json()
        # Catalog matches WHISPER_MODELS + DIARIZATION_MODELS.
        assert len(data["whisper"]) == 6
        assert len(data["diarization"]) == 1
        assert data["total_bytes"] == 0

    def test_delete_unknown_returns_404(self, client: TestClient) -> None:
        r = client.delete("/api/models/nope-not-a-real-model")
        assert r.status_code == 404


class TestDownloadValidation:
    def test_unknown_task_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/download/does-not-exist/txt")
        assert r.status_code == 404

    def test_unknown_format_returns_400(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Inject a fake completed task so we can hit the format-check branch."""
        from app.domain.task import Task
        from app.routers import transcribe

        task = Task(
            task_id="fake-task",
            audio_path=tmp_path / "audio.wav",
            model_size="medium",
        )
        task.transcription = {
            "full_text": "olá",
            "srt": "1\n00:00:00,000 --> 00:00:01,000\nolá\n",
            "segments": [],
        }
        transcribe.tasks.add(task)

        r = client.get("/api/download/fake-task/json")
        assert r.status_code == 400

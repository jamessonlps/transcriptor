"""FastAPI application entry point.

The app is intentionally thin: it bootstraps the token store, mounts the
SPA assets, and includes the routers that contain the actual endpoints.
Business logic lives in ``app.services`` and ``app.routers``.

Why this layout?

  - **One module per HTTP domain** (health/config, hf, transcribe, models)
    keeps each router focused enough that ``main.py`` doesn't grow back
    into a 600-line monolith.
  - **Services are FastAPI-agnostic** so they can be unit-tested without
    spinning up TestClient — see ``tests/test_models.py`` and friends.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
# Load the .env from the project root *before* importing modules that read
# environment variables at import time (e.g. token_store).
load_dotenv(BASE_DIR.parent / ".env")

from app import token_store  # noqa: E402

# Initialize token store *after* load_dotenv: this lets it snapshot the
# token coming from .env, then optionally overlay the one persisted by the
# UI via the in-process env var.
token_store.initialize()

from app.routers import health, hf, models, transcribe  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR.parent / "uploads"
OUTPUT_DIR = BASE_DIR.parent / "outputs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


app = FastAPI(
    title="Transcriptor",
    description=(
        "Local web app for transcribing video/audio in pt-br using "
        "faster-whisper. 100% offline — see /docs for the API."
    ),
)


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """Serve the SPA shell."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Order doesn't matter (no overlapping prefixes), but we list them so the
# /docs UI presents a stable, logical grouping: health -> hf -> models ->
# transcribe.
app.include_router(health.router)
app.include_router(hf.router)
app.include_router(models.router)
app.include_router(transcribe.router)

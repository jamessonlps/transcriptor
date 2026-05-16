# Transcriptor

> Local web app for transcribing video and audio in Brazilian Portuguese.
> Everything runs on your machine — nothing goes to the cloud. Stack:
> FastAPI + faster-whisper + pyannote, with SSE streaming and speaker
> identification running in parallel on the GPU.

[![CI](https://github.com/jamessonfelipe/transcriptor/actions/workflows/ci.yml/badge.svg)](https://github.com/jamessonfelipe/transcriptor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![100% local](https://img.shields.io/badge/100%25-local-success)](#)
[![Docker ready](https://img.shields.io/badge/Docker-ready-2496ED)](./Dockerfile)

🇧🇷 Portuguese version: [`README.md`](./README.md)

## What it is

- 🎙️ Transcribe any audio or video (mp4, mov, mp3, wav, m4a, webm…) in
    Brazilian Portuguese using `faster-whisper` (CTranslate2, int8 on CPU).
- 👥 Optional speaker identification (diarisation) via `pyannote.audio` 4,
    running **in parallel** with transcription (MPS on Apple Silicon, CUDA
    on NVIDIA, CPU fallback).
- 🌊 SSE streaming — segments show up live as they're produced, not at
    the end.
- 💾 In-app model management: disk usage, downloads with live progress,
    deletion.
- 🔐 Hugging Face token managed in-app (persisted to
    `~/.cache/transcriptor/config.json` with mode `0600`, validated via
    `whoami`, setup wizard for gated models).
- 🐳 Runs natively, as an installed app (.app/.desktop/.lnk), or in
    Docker (~1.6 GB CPU-only).

## Why it's worth a technical read

This project is as much about **engineering** as about the product. The
most interesting parts to review:

- **`app/services/transcription_worker.py`** — CPU/GPU parallelism
    between the two models, with explicit handling of every failure mode.
    ~25-40% faster on long files with diarisation enabled (vs sequential).
    See [ADR-0004](./docs/adr/0004-parallel-transcribe-diarise.md).
- **`app/services/task_manager.py`** — in-memory registry with **TTL +
    capacity eviction**. Replaces a global dict that never evicted and
    leaked RAM in long sessions. Covered by
    [`tests/test_task_manager.py`](./tests/test_task_manager.py).
- **`app/transcriber.py`** — single-slot cache with explicit eviction,
    so switching from `medium` (1.5 GB) to `large-v3` (3 GB) is safe on
    machines with 8 GB of RAM.
    [ADR-0002](./docs/adr/0002-whisper-model-eviction.md).
- **`app/static/js/views/transcribe.js`** — vanilla JS with no
    framework, but clean separation between a state machine
    (`class State extends EventTarget`) and a pure render layer.
    Vanilla-JS Flux without dependencies.
- **SSE instead of WebSocket** — rationale in
    [ADR-0001](./docs/adr/0001-sse-over-websockets.md).
- **No build step on the frontend** — rationale in
    [ADR-0003](./docs/adr/0003-no-build-step.md).

Full technical docs in [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md).

## Requirements

- Python 3.10+
- ffmpeg
    - macOS: `brew install ffmpeg`
    - Ubuntu/Debian: `sudo apt install ffmpeg`
    - Windows: download from https://ffmpeg.org/download.html

## Quick start

```bash
./run.sh                  # bootstraps venv + deps, starts on :8765
PORT=9000 ./run.sh        # different port
```

Or with Docker:

```bash
docker compose up -d --build
# http://localhost:8765
```

Or install as a native app:

```bash
./installer/install.sh                                # macOS / Linux
powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1   # Windows
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                      # 90+ tests, <1s
ruff check . && mypy app    # lint + typecheck
```

See [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## Project structure

```
transcriptor/
├── app/
│   ├── main.py                    # FastAPI factory
│   ├── routers/                   # HTTP layer (health, hf, transcribe, models)
│   ├── services/                  # Orchestration
│   ├── domain/                    # Pure data structures (Task)
│   ├── transcriber.py             # faster-whisper wrapper
│   ├── diarizer.py                # pyannote 4 wrapper
│   ├── models.py                  # Model catalog + download/delete
│   ├── token_store.py             # HF token persistence
│   └── static/                    # SPA (no build step)
├── tests/                         # pytest — 90+ tests
├── docs/
│   ├── ARCHITECTURE.md
│   └── adr/
├── installer/                     # macOS / Linux / Windows installers
├── .github/workflows/ci.yml
└── Dockerfile
```

## License

[MIT](./LICENSE).

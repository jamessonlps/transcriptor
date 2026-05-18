# Transcriptor

> Local web app for transcribing audio and video. Everything runs on your
> machine — nothing leaves your computer. FastAPI + faster-whisper +
> pyannote, with live streaming and optional speaker identification.

[![CI](https://github.com/jamessonlps/transcriptor/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jamessonlps/transcriptor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![100% local](https://img.shields.io/badge/100%25-local-success)](#)
[![Docker ready](https://img.shields.io/badge/Docker-ready-2496ED)](./Dockerfile)

## What it does

- **Transcribes** audio and video (`mp4`, `mov`, `mkv`, `mp3`, `wav`,
  `m4a`, `webm`, …) using `faster-whisper`. Default language is
  Brazilian Portuguese; other languages work too.
- **Identifies speakers** (optional) via `pyannote.audio` 4 — tags each
  segment with `[Speaker 1]`, `[Speaker 2]`, …
- **Streams results live** over Server-Sent Events. You read text as
  it's produced; you don't wait for the whole file.
- **Manages models** from the UI — see disk usage, download with a live
  progress bar, delete.
- **Runs everywhere offline**: native (`./run.sh`), installed as a
  desktop app, or in Docker (~1.6 GB CPU-only image).

## How it works

```
   upload  →  Whisper (CPU or CUDA)  ──┐
                                       ├─► merge by overlap  →  SSE stream  →  UI
            pyannote (CPU/CUDA/MPS) ──┘            (live)
```

The two models run **in parallel** when diarization is enabled —
Whisper on CPU/CUDA, pyannote on whatever GPU is available. Wall-clock
time becomes `max(transcribe, diarize)` instead of the sum
(~25–40% faster on long files). Details:
[`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) and the
[ADRs](./docs/adr/).

A badge in the top-right of the UI shows where inference is running
(`CPU`, `Apple Silicon GPU`, or the NVIDIA GPU name). Click it for
details per engine.

## Quick start

You need **Python 3.10+** and **ffmpeg**.

### macOS

```bash
brew install ffmpeg
./run.sh                       # boots on http://localhost:8765
./installer/install.sh         # optional: install as Launchpad app
```

### Linux

```bash
sudo apt install ffmpeg        # or dnf/pacman equivalent
./run.sh
./installer/install.sh         # optional: install as .desktop entry
```

### Windows

Install ffmpeg from <https://ffmpeg.org/download.html> and make sure
`ffmpeg` is on `PATH`, then:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Optional native shortcut (no console window, auto-opens browser):

```powershell
powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1
```

### Docker (any OS)

```bash
docker compose up -d --build   # http://localhost:8765
```

Multi-stage build, CPU-only Torch (~1.6 GB). Named volumes persist
models across `down`/`up`. `PORT=9000 docker compose up -d` to change
the host port.

## GPU acceleration

The app auto-detects available hardware on boot. No flags needed for
the common cases.

| Platform | Transcription (Whisper) | Diarization (pyannote) |
|---|---|---|
| **Windows / Linux + NVIDIA** | GPU (CUDA, automatic) | GPU (CUDA, automatic) |
| **macOS (Apple Silicon)** | CPU* | GPU (MPS, automatic) |
| **No GPU** | CPU | CPU |

\* `faster-whisper` is built on CTranslate2, which has no Metal/MPS
backend. The Mac GPU is used for diarization but not transcription. See
[Limitations](#limitations).

### Enabling CUDA on Windows/Linux

```bash
pip install -e ".[gpu]"        # pulls cuBLAS + cuDNN via pip
```

You don't need the CUDA Toolkit installed — recent NVIDIA drivers are
enough. Typical speedup vs CPU `int8` on `large-v3`: **5–10× faster**.

### Overrides (environment variables)

```bash
TRANSCRIPTOR_DEVICE=cpu|cuda         # force a device (default: auto)
TRANSCRIPTOR_COMPUTE=float16|int8_float16|int8   # override precision
WHISPER_CPU_THREADS=8                # CPU thread count
```

Use `int8_float16` on GPUs with <6 GB VRAM running `large-v3`.

## Speaker identification

Diarization requires a free Hugging Face token (the model is gated).
**Settings → Hugging Face** in the UI walks you through it:

1. Create an account at <https://huggingface.co/join>.
2. Generate a read token at <https://huggingface.co/settings/tokens>.
3. Accept the terms at
   <https://huggingface.co/pyannote/speaker-diarization-community-1>
   with the **same account**.
4. Paste the token in Settings → Save.

The token is stored in `~/.cache/transcriptor/config.json` (mode
`0600`). Alternatively, set `HF_TOKEN=hf_xxx` in a `.env` file.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                         # ~100 tests, <2s
ruff check . && mypy app       # lint + typecheck
```

CI runs the same on every push. See [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## Project structure

```
transcriptor/
├── app/
│   ├── main.py                # FastAPI factory
│   ├── routers/               # HTTP (health, hf, transcribe, models)
│   ├── services/              # Orchestration (task manager, runtime, ...)
│   ├── domain/                # Pure data structures
│   ├── transcriber.py         # faster-whisper wrapper + device detection
│   ├── diarizer.py            # pyannote 4 wrapper
│   ├── models.py              # Model catalog + download/delete
│   ├── token_store.py         # HF token persistence
│   └── static/                # SPA (HTML + vanilla JS, no build step)
├── tests/                     # pytest
├── docs/                      # ARCHITECTURE.md + ADRs
├── installer/                 # macOS / Linux / Windows installers
├── Dockerfile                 # Multi-stage, CPU-only Torch
└── docker-compose.yml
```

## Limitations

- **Mac GPU for transcription** is not supported. CTranslate2 has no
  Metal backend, so Whisper runs on CPU on Apple Silicon. Diarization
  uses the Mac GPU via MPS without issues.
- **Single user, single machine.** No auth, no multi-tenancy. By
  design — this is a local tool.
- **Models are large.** `large-v3` is ~3 GB on disk + ~6 GB VRAM in
  `float16`. Pick a smaller model on constrained hardware.
- **Diarization needs a Hugging Face token.** Free, but it's an extra
  step (gated model).

## Roadmap

- [ ] Optional `mlx-whisper` backend for true Apple Silicon GPU
      transcription (~2–4× faster than CPU on M-series).
- [ ] Chunked parallel transcription for very long files (>1 h).
- [ ] Word-level timestamps in the SRT export.
- [ ] Optional translation pass (target-language output).
- [ ] Batch mode (drop a folder, transcribe all).

Issues and PRs welcome.

## License

[MIT](./LICENSE).

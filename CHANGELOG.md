# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Test suite (`pytest`) covering transcriber, diarizer, token store, model
  catalog, and API contracts.
- GitHub Actions CI: lint (ruff), type check (mypy), tests, Docker build.
- `LICENSE` (MIT), `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`.
- `docs/ARCHITECTURE.md` with the SSE + parallel transcribe/diarize flow.
- Architecture Decision Records (`docs/adr/`).
- Ruff, mypy, and editorconfig configuration committed to the repo.
- Centralised version in `app/__init__.py`.

### Changed
- Split `app/main.py` (613 LOC) into FastAPI routers and service modules
  (`app/routers/`, `app/services/`, `app/domain/`).
- `TaskManager` with TTL eviction replacing the bare `_TASKS` dict (no more
  unbounded RAM growth).
- Frontend views refactored to separate state from rendering. Inline SVGs
  consolidated in `static/js/components/icons.js`.
- CSS split per component / view (`static/css/...`).

## [0.2.0] — 2026-05-12

### Added
- Hugging Face token management UI with persistence to
  `~/.cache/transcriptor/config.json` (mode `0600`).
- Setup wizard explaining HF account → token → gated-model acceptance.
- Per-repo access checks with explicit error mapping (`no_token`,
  `no_access`, `invalid_token`).
- Diarisation runs in parallel with transcription on a background thread
  (~25-40% wall-clock improvement on long files).
- Model management page: storage usage, per-model download with live
  progress, deletion.
- Multi-stage Docker image (~1.6 GB, CPU-only Torch) + `docker-compose.yml`.
- Native installers: macOS `.app` bundle, Linux `.desktop` entry, Windows
  `.vbs`-wrapped shortcut.

## [0.1.0] — 2026-05-09

### Added
- Initial release: drag-and-drop upload, faster-whisper transcription, SSE
  streaming of segments, `.txt`/`.srt` download.

# Contributing

Thanks for your interest in improving Transcriptor.

## Quick start

```bash
git clone https://github.com/jamessonfelipe/transcriptor.git
cd transcriptor
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest           # run the test suite
ruff check .     # lint
mypy app         # type check
```

To run the app locally for manual testing:

```bash
./run.sh
# http://localhost:8765
```

## Style

- **Ruff** is the source of truth for formatting and linting. Run `ruff format`
    and `ruff check --fix` before opening a PR. The CI will reject anything that
    doesn't match.
- **Type hints** on all new public functions. `mypy app` must pass.
- **Comments explain *why*, not *what*.** If the code already says what it does,
    don't repeat it. Reserve comments for surprising decisions, constraints, or
    references to incidents.

## Adding a new endpoint

1. Add the route to a router under `app/routers/` (group by domain — don't
     dump everything in `main.py`).
2. Keep business logic in `app/services/`. Routers should be thin: parse
     input, call a service, return a response.
3. Add at least one test under `tests/` using FastAPI's `TestClient`.
4. Update the OpenAPI docs implicitly via type hints and docstrings — they
     show up at `/docs`.

## Adding a new view

1. Add `app/static/js/views/<name>.js` exporting `mount(container, ctx)`.
2. Register the route in `app/static/js/main.js`.
3. Reuse helpers from `ui.js` (`el`, `show`, `hide`, `cloneTemplate`, `toast`,
     `confirmModal`).
4. Reuse icons from `app/static/js/components/icons.js`. Don't inline SVGs in
     view files.

## Commit messages

Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
The CHANGELOG is generated from these — keep them readable.

## Release process

1. Bump `app/__init__.py:__version__` and `pyproject.toml`.
2. Move CHANGELOG entries from `[Unreleased]` to the new version section.
3. Tag the commit: `git tag v0.x.0 && git push --tags`.

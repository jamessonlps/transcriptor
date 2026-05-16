# ADR 0003 — No build step on the frontend

**Status:** Accepted · 2026-05-12

## Context

The frontend is a small SPA: two views, ~1000 lines of JavaScript total,
some CSS, a few HTML templates. A "proper" production frontend would
typically run:

- Vite/Webpack/esbuild for ES module bundling
- Tailwind CLI for tree-shaken CSS
- TypeScript for type checking
- ESLint + Prettier for linting

All of that means a `node_modules` install, a `dist/` build artefact, a
watch process for development, and the operational overhead of keeping a
JS toolchain in sync with the Python one.

## Decision

No build step. The frontend ships as the files in `app/static/`, served
directly by FastAPI. Specifically:

- **ES modules** loaded natively by the browser (`<script type="module">`).
- **Tailwind via the CDN JIT runtime** (~400 KB shipped as
  `static/tailwind.js`) plus custom CSS in `static/css/*.css` linked via
  `@import`.
- **No TypeScript** — pure JS with JSDoc where typing helps.

## Consequences

- **"Clone and run" stays cheap.** `./run.sh` produces a working app
  with one Python toolchain. No `nvm`, no `npm install`, no
  cross-toolchain version drift to manage.
- **Hot-reload is "save and refresh".** The dev loop is genuinely the
  same as production — no source map mismatch, no build cache to
  invalidate.
- **The bundle is honest.** Anyone inspecting the app in DevTools sees
  exactly what's in the repo. There's no minified blob obscuring how
  things work, which matters for a portfolio piece.
- **Trade-off accepted:** the Tailwind CDN JIT is officially "not for
  production" — it parses HTML in the browser to generate CSS classes.
  For a single-user local app that runs on `127.0.0.1` and stores
  nothing in the cloud, the cost (a one-time 400 KB download, then
  cached) is acceptable. For a production multi-user deployment the
  right move is the Tailwind CLI build, generating one minified
  `dist/style.css`. The migration would be ~20 lines in `run.sh` and a
  download of the standalone Tailwind binary; no other file changes.
- **No tree-shaking.** We don't ship unused JS because we don't import
  unused modules. The biggest single file is `static/tailwind.js`
  itself.
- **No TypeScript means runtime type errors are possible.** Mitigation:
  the JS surface is small, the protocol with the server is documented
  in `ARCHITECTURE.md`, and the SSE event types are switched on
  exhaustively in `views/transcribe.js`.

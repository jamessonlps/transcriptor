# ADR 0001 — Server-Sent Events over WebSockets

**Status:** Accepted · 2026-05-09

## Context

The transcription pipeline produces output incrementally — segments come
out of `faster-whisper` as a generator, and the UI should display them
live rather than wait for the full file. Two natural options:

1. **WebSockets** — bidirectional, well-known protocol, mature client API.
2. **Server-Sent Events (SSE)** — uni-directional from server to client,
   built on plain HTTP, with `EventSource` baked into every browser.

Server → client streaming is the only direction we need. The client
sends one upload (a POST) and afterwards only consumes events. There's
no chat-style back-and-forth that would justify the full bidirectional
machinery of WebSockets.

## Decision

Use Server-Sent Events for both the transcription stream
(`GET /api/stream/:task_id`) and the model-download progress stream
(`GET /api/models/:key/download`).

## Consequences

- **Simpler server code.** `StreamingResponse` with a generator and a
  small `format_event()` helper is the entire transport layer
  (see `app/services/sse.py`). No upgrade handshake, no ping/pong,
  no frame parsing.
- **Plays well with HTTP infra.** Reverse proxies, gzip, browser
  caching all behave normally. We just disable buffering with
  `X-Accel-Buffering: no` and `Cache-Control: no-cache`.
- **No reconnection logic needed for the use case.** `EventSource`
  auto-reconnects with backoff for free, but our streams are short
  (one transcription) and the client treats a dropped connection as
  "task failed" — see `onSSEError` in `views/transcribe.js`. That's the
  right behaviour because we can't safely resume a partial transcription.
- **GET-only endpoints.** `EventSource` doesn't support POST, so the
  upload is a separate POST that returns a `task_id`, and the client
  opens an `EventSource` keyed by that id. Two round-trips instead of
  one, but the upload would dominate latency anyway.
- **Trade-off accepted:** if we ever need to *cancel* a task mid-stream,
  we'd need an out-of-band endpoint (DELETE the task). With WebSockets
  the client could send a cancel frame on the same channel. Acceptable
  because we don't have a working pyannote cancellation primitive
  anyway — see ARCHITECTURE.md.

# Architecture Decision Records

ADRs capture *why* a non-obvious technical choice was made. They're not
documentation of behaviour — that lives in code and in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md). They're the artefact you
reach for when someone asks "wait, why did we do it this way?" three
months later.

The format is intentionally short: **Status / Context / Decision /
Consequences**. If a record needs to be more than a single screen of
text, it probably wants to be split.

## Index

| #    | Title                                                       | Status   |
|------|-------------------------------------------------------------|----------|
| 0001 | [Server-Sent Events over WebSockets](./0001-sse-over-websockets.md) | Accepted |
| 0002 | [Single-model RAM cache with eviction](./0002-whisper-model-eviction.md) | Accepted |
| 0003 | [No build step on the frontend](./0003-no-build-step.md)    | Accepted |
| 0004 | [Parallel transcribe + diarise](./0004-parallel-transcribe-diarise.md) | Accepted |

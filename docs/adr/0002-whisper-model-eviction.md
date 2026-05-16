# ADR 0002 — Single-model RAM cache with eviction

**Status:** Accepted · 2026-05-09

## Context

Whisper models range from ~75 MB (`tiny`) to ~3 GB (`large-v3`). Once
loaded into RAM by `faster-whisper`, the cost is paid until the process
exits. On a developer machine with 8 GB of RAM, two co-resident large
models will trigger swap or OOM.

Three viable strategies:

1. **No cache** — load on every request. Simple, but a ~5-10 second
   load before each transcription is bad UX, and the disk read becomes
   the bottleneck for short clips.
2. **Pool with N entries** — keep up to N models alive, evict LRU on
   miss. Maximises throughput for users who switch back and forth.
3. **Single-slot cache** — keep exactly one model alive. Evict when
   the user picks a different size.

## Decision

Strategy 3 — a single-slot module-global cache, with eviction triggered
both by mismatch (`get_model` with a different key) and by external
calls (`evict_if_matches` from the model-deletion endpoint).

## Consequences

- **Memory bounded to one model.** Switching from `medium` to
  `large-v3` is safe even on 8 GB machines. Eviction is explicit
  (`_CURRENT_MODEL = None; gc.collect()`).
- **One thread-local lock.** All access goes through `_MODEL_LOCK`,
  which guards both the swap and the rare two-callers-want-the-same-model
  case. Performance-wise this lock is held only during model load,
  not during inference.
- **Trade-off:** a user that switches between two models repeatedly
  pays the load cost on every switch (5-10 s). Pool with N=2 would
  fix that. We chose not to because:
  - The common path is "pick one model, transcribe many files."
  - Pool eviction policies (LRU, LFU) are easy to get wrong on edge
    cases and we want to ship.
  - It's a one-line change to grow the cache if profiling ever shows
    real users switching.
- **Delete-cache must trigger eviction.** Without
  `evict_if_matches`, deleting a model from disk would still leave it
  "alive" in RAM, and the next inference would silently work. That
  was the bug that motivated this code path.

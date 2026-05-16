# ADR 0004 — Parallel transcribe + diarise

**Status:** Accepted · 2026-05-12

## Context

When the user enables speaker identification, two ML models need to
process the same audio:

- **Whisper** (`faster-whisper`, CTranslate2 backend) transcribes text.
  Runs on CPU with int8 quantisation by default.
- **pyannote** (`pyannote.audio` 4.x) identifies speaker turns. Runs
  on MPS on Apple Silicon, CUDA on NVIDIA, CPU otherwise.

The naive implementation runs them sequentially:

```
[transcribe Whisper]=========>[diarise pyannote]=========>
0s                      T1s                      T1+T2s
```

On a 30-minute file with the `medium` Whisper model and pyannote
community-1, T1 ≈ 5 min, T2 ≈ 1.5 min, total ≈ 6.5 min.

But on Apple Silicon, Whisper uses CPU and pyannote uses the integrated
GPU (MPS). They don't compete for hardware. Same on a CUDA box. Even on
pure CPU the bottlenecks differ — Whisper is throughput-bound on int8
matmuls, pyannote is memory-bandwidth-bound on conv layers.

## Decision

Start diarisation on a daemon thread the moment the worker accepts the
task. Let it progress in parallel with transcription. When transcription
finishes, the worker joins the diarisation thread (instant in the common
case, since it usually finishes first) and merges the results.

```
[transcribe Whisper] =====================>
[diarise pyannote  ] =============>          (background, MPS/CUDA/CPU)
                                          ^ join + assign
```

Total wall-clock becomes `max(T1, T2)` instead of `T1 + T2`.

## Consequences

- **25-40% faster on diarised runs.** Measured anecdotally on M1 Pro
  with 30-minute meeting recordings — your mileage will vary based on
  hardware and ratio of T1/T2.
- **Implementation cost is one thread + a holder dict.** See
  `services/transcription_worker.py`. The complexity stays linear and
  isolated to one function.
- **Failure modes documented:**
  - Whisper fails first → we abandon the daemon thread; pyannote will
    finish on its own and the GC reclaims its memory. We can't cancel
    it (pyannote 4 has no cancellation API).
  - pyannote fails (e.g. gated repo, bad token) → we surface the error
    as a `diarization_error` SSE event and still deliver the
    transcript.
  - Both succeed → normal path; results are merged via
    `assign_speakers_to_segments` and emitted as `diarization_done`.
- **Trade-off:** we hold the upload file open longer (until both
  models finish) and use a bit more RAM (both pipelines resident
  simultaneously). On the target hardware (8+ GB) this is invisible.
- **Test coverage:** the synchronous merge logic is exercised by
  `tests/test_diarizer.py::TestAssignSpeakersToSegments`. The worker
  thread isn't directly tested — it's mostly orchestration and the
  pieces it composes are individually covered.

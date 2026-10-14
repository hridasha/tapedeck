# Benchmarks

Measured on Windows 11, Python 3.12.10, everything on one machine (so numbers are a floor for overhead, not a promise).

## Recording proxy overhead

500 sequential GETs of the same small JSON endpoint.

| | median | p95 | p99 |
|---|---|---|---|
| straight to the API | 1.15 ms | 1.40 ms | 1.67 ms |
| through the recorder | 15.57 ms | 26.85 ms | 30.34 ms |
| **added** | **14.42 ms** | 25.45 ms | 28.67 ms |

## Fake server throughput

20 concurrent users for 4 s (after 1 s warm-up) against a mock answering from a tape: **154 requests/s**, p50 60.6 ms, p99 622.0 ms, errors 0.

The load generator (Python + httpx on Windows, one process) may be the limit here rather than the fake server,
so read this as 'at least this much'.

## Load generator accuracy

Asked for a fixed 300 requests/s for 4 s: sent **300 requests/s**, dropped 0, errors 0.

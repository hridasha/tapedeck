"""Numbers for a load test: percentiles, per-second ticks, per-endpoint table and the "knee" where it got slow."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass
class Sample:
    t: float  # seconds after the start when the request was (or should have been) sent
    done: float  # seconds after the start when its answer arrived
    ms: float  # how long the caller waited
    status: int  # 0 = no answer at all (connection error, timeout)
    key: str  # "GET /videos/{id}"
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == 0 or self.status >= 500


@dataclass
class Tick:
    """What happened during one second."""

    t: int
    requests: int
    errors: int
    p50: float | None
    p95: float | None
    p99: float | None
    users: int  # users (closed model) or requests in flight (open model) at the end of that second

    @property
    def rps(self) -> float:
        return float(self.requests)


def percentile(sorted_values: list[float], p: float) -> float | None:
    """Nearest-rank percentile of an already sorted list (p from 0 to 100)."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def make_tick(second: int, samples: list[Sample], users: int) -> Tick:
    ok = sorted(s.ms for s in samples if not s.failed)
    return Tick(
        t=second,
        requests=len(samples),
        errors=sum(1 for s in samples if s.failed),
        p50=percentile(ok, 50),
        p95=percentile(ok, 95),
        p99=percentile(ok, 99),
        users=users,
    )


def summarize(samples: list[Sample], duration: float, warmup: float = 0.0) -> dict:
    """Totals over the whole run, leaving out the first `warmup` seconds."""
    used = [s for s in samples if s.t >= warmup]
    ok = sorted(s.ms for s in used if not s.failed)
    window = max(duration - warmup, 1e-9)
    n = len(used)
    return {
        "requests": n,
        "errors": sum(1 for s in used if s.failed),
        "error_rate": (sum(1 for s in used if s.failed) / n) if n else 0.0,
        "rps": n / window,
        "mean_ms": (sum(ok) / len(ok)) if ok else None,
        "p50_ms": percentile(ok, 50),
        "p90_ms": percentile(ok, 90),
        "p95_ms": percentile(ok, 95),
        "p99_ms": percentile(ok, 99),
        "max_ms": ok[-1] if ok else None,
        "status_codes": dict(sorted(Counter(s.status for s in used).items())),
        "warmup_s": warmup,
    }


def endpoint_table(samples: list[Sample], warmup: float = 0.0, top: int = 15) -> list[dict]:
    groups: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        if s.t >= warmup:
            groups[s.key].append(s)
    rows = []
    for key, group in groups.items():
        ok = sorted(s.ms for s in group if not s.failed)
        rows.append(
            {
                "endpoint": key,
                "requests": len(group),
                "errors": sum(1 for s in group if s.failed),
                "p50_ms": percentile(ok, 50),
                "p95_ms": percentile(ok, 95),
                "max_ms": ok[-1] if ok else None,
            }
        )
    return sorted(rows, key=lambda r: (r["p95_ms"] is None, -(r["p95_ms"] or 0)))[:top]


def find_knee(ticks: list[Tick], min_gap_ms: float = 30.0, sustained: int = 3) -> dict | None:
    """When did latency take off while load was still growing?

    The baseline is the typical p95 while load was light (the first fifth of the run, at least 3 seconds). The knee
    is the first second after which p95 stays above max(2 x baseline, baseline + min_gap_ms) for `sustained` seconds.
    """
    with_data = [t for t in ticks if t.p95 is not None]
    if len(with_data) < 3 + sustained:
        return None
    head = with_data[: max(3, len(with_data) // 5)]
    baseline = sorted(t.p95 for t in head)[len(head) // 2]
    limit = max(2 * baseline, baseline + min_gap_ms)
    for i in range(len(with_data) - sustained + 1):
        window = with_data[i : i + sustained]
        if all(t.p95 > limit for t in window):
            hit = window[0]
            return {
                "t": hit.t,
                "users": hit.users,
                "p95_ms": hit.p95,
                "baseline_p95_ms": baseline,
                "limit_ms": limit,
            }
    return None

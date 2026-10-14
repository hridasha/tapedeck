"""Measure Tapedeck's own overhead. Run:  python benchmarks/run_benchmarks.py   (writes docs/benchmarks.md)

1. Recorder overhead: the same GET straight to the sample API vs through the recording proxy.
2. Mock throughput: requests per second a fake server answers from a tape.
3. Load-generator accuracy: ask for a fixed rate against the mock and see what was really sent.
"""

from __future__ import annotations

import asyncio
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from examples.sample_api.app import app as sample_app  # noqa: E402
from tapedeck.core.storage import Storage  # noqa: E402
from tapedeck.load.runner import LoadOptions, run_load  # noqa: E402
from tapedeck.recorder.proxy import create_recorder_app  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app) -> str:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}"


def timed(url: str, n: int) -> list[float]:
    out = []
    with httpx.Client() as c:
        for _ in range(20):  # warm-up
            c.get(url)
        for _ in range(n):
            t = time.perf_counter()
            c.get(url)
            out.append((time.perf_counter() - t) * 1000)
    return out


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def main() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    direct = serve(sample_app)
    store = Storage(str(tmp))
    tape = store.create_tape("bench", source="record")
    proxy = serve(create_recorder_app(store, tape.id, direct))

    n = 500
    d, p = timed(f"{direct}/items/1", n), timed(f"{proxy}/items/1", n)

    entries = store.get_entries(tape.id)
    # the fake server runs in its own process, so it does not share a Python lock with the load generator
    store.close()
    mport = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tapedeck.cli", "mock", "bench", "--port", str(mport), "--db", str(tmp)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    mock = f"http://127.0.0.1:{mport}"
    for _ in range(100):
        try:
            httpx.get(f"{mock}/items/1")
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    mock_result = asyncio.run(run_load(entries, LoadOptions(target=mock, users=20, duration=5, warmup=1)))

    rate = 300
    open_result = asyncio.run(run_load(entries, LoadOptions(target=mock, rate=rate, users=1, duration=5, warmup=1)))

    proc.terminate()

    lines = [
        "# Benchmarks",
        "",
        f"Measured on {platform.system()} {platform.release()}, Python {platform.python_version()}, "
        "everything on one machine (so numbers are a floor for overhead, not a promise).",
        "",
        "## Recording proxy overhead",
        "",
        f"{n} sequential GETs of the same small JSON endpoint.",
        "",
        "| | median | p95 | p99 |",
        "|---|---|---|---|",
        f"| straight to the API | {statistics.median(d):.2f} ms | {pct(d, 95):.2f} ms | {pct(d, 99):.2f} ms |",
        f"| through the recorder | {statistics.median(p):.2f} ms | {pct(p, 95):.2f} ms | {pct(p, 99):.2f} ms |",
        f"| **added** | **{statistics.median(p) - statistics.median(d):.2f} ms** | "
        f"{pct(p, 95) - pct(d, 95):.2f} ms | {pct(p, 99) - pct(d, 99):.2f} ms |",
        "",
        "## Fake server throughput",
        "",
        f"20 concurrent users for 4 s (after 1 s warm-up) against a mock answering from a tape: "
        f"**{mock_result.summary['rps']:.0f} requests/s**, p50 {mock_result.summary['p50_ms']:.1f} ms, "
        f"p99 {mock_result.summary['p99_ms']:.1f} ms, errors {mock_result.summary['errors']}.",
        "",
        "The load generator (Python + httpx, one process) may be the limit here, not the fake server,",
        "so read this as 'at least this much'.",
        "",
        "## Load generator accuracy",
        "",
        f"Asked for a fixed {rate} requests/s for 4 s: sent **{open_result.summary['rps']:.0f} requests/s**, "
        f"dropped {open_result.dropped}, errors {open_result.summary['errors']}.",
        "",
    ]
    out = ROOT / "docs" / "benchmarks.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

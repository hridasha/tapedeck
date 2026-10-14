"""Send a tape's requests as a crowd and measure how the server copes.

Closed model (`users=N`): N virtual users, each sends a request, waits for the answer, then sends the next.
Open model (`rate=R`): a new request every 1/R seconds no matter how slow the server is. Latency is measured from
the moment each request SHOULD have been sent, so a stalled server cannot hide its stall ("coordinated omission").
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from tapedeck.core.http import clean_request_headers, has_scrubbed_body, require_allowed_target
from tapedeck.core.matcher import template_path
from tapedeck.core.models import Entry
from tapedeck.replay.runner import SAFE_METHODS

from .stats import Sample, Tick, endpoint_table, find_knee, make_tick, summarize


@dataclass
class LoadOptions:
    target: str
    users: int = 10
    rate: float | None = None  # requests per second; switches to the open model
    duration: float = 30.0  # seconds, the ramp-up included
    ramp: float = 0.0  # seconds over which the users are started (closed model)
    warmup: float = 0.0  # seconds left out of the summary
    think_ms: float = 0.0  # pause between one user's requests
    headers: dict[str, str] = field(default_factory=dict)
    allow_writes: bool = False
    allow_remote: bool = False
    timeout: float = 30.0
    max_inflight: int = 1000  # open model: requests beyond this are dropped and counted
    transport: httpx.AsyncBaseTransport | None = None
    on_tick: Callable[[Tick], None] | None = None


@dataclass
class LoadResult:
    options: dict
    summary: dict
    ticks: list[Tick]
    endpoints: list[dict]
    knee: dict | None
    skipped: int  # tape entries that were not sent (writes, hidden secrets)
    dropped: int  # open model: requests not sent because too many were already in flight

    @property
    def model(self) -> str:
        return "open" if self.options.get("rate") else "closed"


def usable_entries(entries: list[Entry], opts: LoadOptions) -> tuple[list[Entry], int]:
    keep = [e for e in entries if (e.method in SAFE_METHODS or opts.allow_writes) and not has_scrubbed_body(e.req_body)]
    return keep, len(entries) - len(keep)


async def run_load(entries: list[Entry], opts: LoadOptions) -> LoadResult:
    require_allowed_target(opts.target, opts.allow_remote)
    entries_to_send, skipped = usable_entries(entries, opts)
    if not entries_to_send:
        raise ValueError(
            "nothing to send: the tape has no GET requests (use --allow-writes) or they all had secrets hidden"
        )
    if opts.rate is None and opts.users < 1:
        raise ValueError("--users must be at least 1")
    if opts.rate is not None and opts.rate <= 0:
        raise ValueError("--rate must be above 0")

    samples: list[Sample] = []
    per_second: dict[int, list[Sample]] = defaultdict(list)
    ticks: list[Tick] = []
    state = {"active": 0, "inflight": 0, "dropped": 0}
    n = len(entries_to_send)
    open_model = opts.rate is not None
    limits = httpx.Limits(
        max_connections=max(opts.max_inflight if open_model else opts.users, 1) + 10,
        max_keepalive_connections=max(opts.users, 20),
    )

    async with httpx.AsyncClient(
        base_url=opts.target.rstrip("/"),
        transport=opts.transport,
        timeout=opts.timeout,
        follow_redirects=False,
        limits=limits,
    ) as client:
        t_zero = time.perf_counter()

        def now() -> float:
            return time.perf_counter() - t_zero

        async def send(entry: Entry, intended: float) -> None:
            url = f"{entry.path}?{entry.query}" if entry.query else entry.path
            headers = clean_request_headers(entry.req_headers, opts.headers)
            state["inflight"] += 1
            status, error = 0, None
            try:
                resp = await client.request(entry.method, url, headers=headers, content=entry.req_body or None)
                status = resp.status_code
            except httpx.HTTPError as exc:
                error = type(exc).__name__
            finally:
                state["inflight"] -= 1
            done = now()
            sample = Sample(
                t=intended,
                done=done,
                ms=(done - intended) * 1000,
                status=status,
                key=f"{entry.method} {template_path(entry.path)}",
                error=error,
            )
            samples.append(sample)
            per_second[int(done)].append(sample)

        def load_now() -> int:
            return state["inflight"] if open_model else state["active"]

        async def ticker() -> None:
            second = 0
            while True:
                await asyncio.sleep(max(0.0, (second + 1) - now()))
                tick = make_tick(second, per_second.get(second, []), load_now())
                ticks.append(tick)
                if opts.on_tick:
                    opts.on_tick(tick)
                second += 1

        async def user(index: int, start_delay: float) -> None:
            await asyncio.sleep(start_delay)
            state["active"] += 1
            i = index
            try:
                while now() < opts.duration:
                    entry = entries_to_send[i % n]
                    i += 1
                    await send(entry, now())
                    if opts.think_ms:
                        await asyncio.sleep(opts.think_ms / 1000)
            finally:
                state["active"] -= 1

        async def arrivals() -> None:
            interval = 1.0 / opts.rate  # type: ignore[operator]
            pending: set[asyncio.Task] = set()
            sent = 0
            while True:
                intended = sent * interval
                if intended >= opts.duration:
                    break
                wait = intended - now()
                if wait > 0:
                    await asyncio.sleep(wait)
                if state["inflight"] >= opts.max_inflight:
                    state["dropped"] += 1
                else:
                    task = asyncio.create_task(send(entries_to_send[sent % n], intended))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                sent += 1
            if pending:
                await asyncio.wait(pending, timeout=opts.timeout)

        tick_task = asyncio.create_task(ticker())
        try:
            if open_model:
                await arrivals()
            else:
                ramp_step = (opts.ramp / opts.users) if opts.ramp > 0 else 0.0
                await asyncio.gather(*(user(i, i * ramp_step) for i in range(opts.users)))
        finally:
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass

    # the last, partial second(s)
    last_second = int(max((s.done for s in samples), default=0))
    for second in range(len(ticks), last_second + 1):
        ticks.append(make_tick(second, per_second.get(second, []), 0))

    return LoadResult(
        options={
            "target": opts.target,
            "users": None if open_model else opts.users,
            "rate": opts.rate,
            "duration": opts.duration,
            "ramp": opts.ramp,
            "warmup": opts.warmup,
        },
        summary=summarize(samples, opts.duration, opts.warmup),
        ticks=ticks,
        endpoints=endpoint_table(samples, opts.warmup),
        knee=None if open_model or opts.ramp <= 0 else find_knee(ticks),
        skipped=skipped,
        dropped=state["dropped"],
    )

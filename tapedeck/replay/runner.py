"""Send a tape's requests to a server and check the answers against what was recorded."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from tapedeck.core.http import clean_request_headers, has_scrubbed_body, require_allowed_target
from tapedeck.core.models import Entry
from tapedeck.core.noise import differing_paths

from .diff import TYPE, Diff, failing, json_diff

SAFE_METHODS = {"GET", "HEAD"}


@dataclass
class ReplayOptions:
    target: str
    headers: dict[str, str] = field(default_factory=dict)  # e.g. a fresh Authorization (recorded ones were hidden)
    ignore: set[str] = field(default_factory=set)  # JSON paths to skip
    allow_writes: bool = False  # POST / PUT / PATCH / DELETE are skipped unless this is set
    allow_remote: bool = False
    learn_noise: bool = False  # ask GETs twice; fields that differ between the two answers are ignored
    compare_headers: tuple[str, ...] = ("content-type",)
    slower_factor: float = 3.0
    min_slow_ms: float = 100.0
    fail_on_slow: bool = False
    timeout: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None  # tests plug an in-process app in here
    on_result: Callable[[EntryResult, int], None] | None = None  # called after every request (progress)


@dataclass
class EntryResult:
    order: int
    method: str
    path: str
    query: str
    outcome: str  # pass | fail | skipped | error
    status_expected: int
    status_actual: int | None = None
    ms_expected: float = 0.0
    ms_actual: float | None = None
    reasons: list[str] = field(default_factory=list)  # why it failed
    warnings: list[str] = field(default_factory=list)  # worth a look, not a failure
    diffs: list[Diff] = field(default_factory=list)
    detail: str = ""  # skip reason or error text


@dataclass
class ReplayResult:
    target: str
    started_at: float
    finished_at: float
    results: list[EntryResult]

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    @property
    def ok(self) -> bool:
        return self.count("fail") == 0 and self.count("error") == 0

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "pass": self.count("pass"),
            "fail": self.count("fail"),
            "skipped": self.count("skipped"),
            "error": self.count("error"),
            "warnings": sum(1 for r in self.results if r.warnings),
        }


def _json(body: bytes):
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def _preview(body: bytes, limit: int = 200) -> str:
    text = body.decode("utf-8", errors="replace")
    return text if len(text) <= limit else text[:limit] + "…"


def _media_type(value: str) -> str:
    return value.split(";")[0].strip().lower()


def compare_response(
    entry: Entry, status: int, headers: httpx.Headers | dict, body: bytes, opts: ReplayOptions, extra_ignore: set[str]
) -> tuple[list[str], list[Diff]]:
    """(reasons it differs, body differences). Both empty means the answer matches."""
    reasons: list[str] = []
    diffs: list[Diff] = []
    if status != entry.status:
        reasons.append(f"status {entry.status} -> {status}")

    for name in opts.compare_headers:
        before, after = entry.resp_headers.get(name, ""), headers.get(name, "")
        if _media_type(before) != _media_type(after) and (before or after):
            reasons.append(f"header {name}: '{before}' -> '{after}'")

    expected_json, actual_json = _json(entry.resp_body), _json(body)
    ignore = opts.ignore | extra_ignore
    if expected_json is not None and actual_json is not None:
        diffs = json_diff(expected_json, actual_json, ignore)
        if failing(diffs):
            reasons.append(f"{len(failing(diffs))} field(s) differ")
    elif expected_json is not None or actual_json is not None:
        diffs = [Diff("body", TYPE, _preview(entry.resp_body), _preview(body))]
        reasons.append("body changed between JSON and not-JSON")
    elif entry.resp_body != body:
        diffs = [Diff("body", "changed", _preview(entry.resp_body), _preview(body))]
        reasons.append("body differs")
    return reasons, diffs


async def replay(entries: list[Entry], opts: ReplayOptions) -> ReplayResult:
    require_allowed_target(opts.target, opts.allow_remote)
    started = time.time()
    results: list[EntryResult] = []

    async with httpx.AsyncClient(
        base_url=opts.target.rstrip("/"), transport=opts.transport, timeout=opts.timeout, follow_redirects=False
    ) as client:
        for entry in entries:
            result = await _replay_one(client, entry, opts)
            results.append(result)
            if opts.on_result:
                opts.on_result(result, len(entries))
    return ReplayResult(opts.target, started, time.time(), results)


async def _replay_one(client: httpx.AsyncClient, entry: Entry, opts: ReplayOptions) -> EntryResult:
    base = EntryResult(
        order=entry.order,
        method=entry.method,
        path=entry.path,
        query=entry.query,
        outcome="pass",
        status_expected=entry.status,
        ms_expected=entry.duration_ms,
    )
    if entry.method not in SAFE_METHODS and not opts.allow_writes:
        base.outcome, base.detail = "skipped", "write request (add --allow-writes to send it)"
        return base
    if has_scrubbed_body(entry.req_body):
        base.outcome, base.detail = "skipped", "its request body had secrets hidden, so it cannot be sent as recorded"
        return base

    url = f"{entry.path}?{entry.query}" if entry.query else entry.path
    headers = clean_request_headers(entry.req_headers, opts.headers)

    async def send() -> tuple[httpx.Response, float]:
        t0 = time.perf_counter()
        response = await client.request(entry.method, url, headers=headers, content=entry.req_body or None)
        return response, (time.perf_counter() - t0) * 1000

    try:
        response, ms = await send()
        learned: set[str] = set()
        if opts.learn_noise and entry.method in SAFE_METHODS:
            again, _ = await send()
            a, b = _json(response.content), _json(again.content)
            if a is not None and b is not None:
                learned = differing_paths(a, b)
    except httpx.HTTPError as exc:
        base.outcome, base.detail = "error", f"{type(exc).__name__}: {exc}"
        return base

    base.status_actual, base.ms_actual = response.status_code, ms
    base.reasons, base.diffs = compare_response(
        entry, response.status_code, response.headers, response.content, opts, learned
    )
    if base.reasons:
        base.outcome = "fail"
    if ms > max(opts.min_slow_ms, opts.slower_factor * entry.duration_ms):
        base.warnings.append(f"slower: {ms:.0f} ms (recorded {entry.duration_ms:.0f} ms)")
        if opts.fail_on_slow:
            base.outcome = "fail"
            base.reasons.append("slower than recorded")
    if any(d.volatile for d in base.diffs):
        base.warnings.append("timestamp/id values changed (ignored)")
    return base

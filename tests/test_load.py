import asyncio
import json

import httpx
import pytest
from typer.testing import CliRunner

from examples.sample_api.app import app as sample_app
from tapedeck.cli import app as cli
from tapedeck.core.models import Entry
from tapedeck.core.storage import Storage
from tapedeck.load.report import line_chart, render_html, to_dict
from tapedeck.load.runner import LoadOptions, run_load
from tapedeck.load.stats import Sample, Tick, endpoint_table, find_knee, make_tick, percentile, summarize

from .helpers import record_calls

IN_PROCESS = httpx.ASGITransport(app=sample_app)


def go(entries, **kw):
    kw.setdefault("transport", IN_PROCESS)
    kw.setdefault("duration", 1.2)
    return asyncio.run(run_load(entries, LoadOptions(target="http://test", **kw)))


@pytest.fixture
def tape():
    return record_calls(sample_app, [("GET", "/items?page=1"), ("GET", "/items/1"), ("GET", "/items/2")])


def sample(t=0.0, ms=10.0, status=200, key="GET /x"):
    return Sample(t=t, done=t + ms / 1000, ms=ms, status=status, key=key)


# ------------------------------------------------------------------------------------------- statistics
def test_percentile_uses_nearest_rank():
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100
    assert percentile([7], 99) == 7
    assert percentile([], 50) is None


def test_summary_counts_errors_and_leaves_out_warmup():
    samples = [sample(t=0.5, ms=500)] + [sample(t=2 + i * 0.1, ms=10) for i in range(10)] + [sample(t=3, status=503)]
    s = summarize(samples, duration=5, warmup=1)
    assert s["requests"] == 11 and s["errors"] == 1
    assert s["p50_ms"] == 10 and s["max_ms"] == 10  # the slow warm-up request is not counted
    assert s["error_rate"] == pytest.approx(1 / 11)
    assert s["status_codes"] == {200: 10, 503: 1}
    assert s["rps"] == pytest.approx(11 / 4)


def test_connection_failures_count_as_errors_with_status_zero():
    assert sample(status=0).failed and sample(status=500).failed and not sample(status=404).failed


def test_make_tick_ignores_failed_requests_in_latency():
    tick = make_tick(3, [sample(ms=10), sample(ms=30), sample(ms=5, status=500)], users=7)
    assert (tick.requests, tick.errors, tick.p50, tick.users) == (3, 1, 10, 7)


def test_endpoint_table_sorts_slowest_first():
    rows = endpoint_table([sample(ms=5, key="GET /a")] * 3 + [sample(ms=90, key="GET /b")] * 3)
    assert [r["endpoint"] for r in rows] == ["GET /b", "GET /a"]


def test_knee_is_found_when_latency_takes_off():
    ticks = [Tick(t=i, requests=10, errors=0, p50=10, p95=20, p99=25, users=i) for i in range(8)]
    ticks += [Tick(t=8 + i, requests=10, errors=0, p50=100, p95=300, p99=400, users=8 + i) for i in range(5)]
    knee = find_knee(ticks)
    assert knee and knee["users"] == 8 and knee["baseline_p95_ms"] == 20


def test_no_knee_when_latency_stays_flat_or_data_is_short():
    flat = [Tick(t=i, requests=10, errors=0, p50=10, p95=20, p99=25, users=i) for i in range(20)]
    assert find_knee(flat) is None
    assert find_knee(flat[:3]) is None


def test_one_slow_second_is_not_a_knee():
    ticks = [Tick(t=i, requests=10, errors=0, p50=10, p95=20, p99=25, users=i) for i in range(12)]
    ticks[6] = Tick(t=6, requests=10, errors=0, p50=10, p95=900, p99=900, users=6)
    assert find_knee(ticks) is None


# ----------------------------------------------------------------------------------------------- runs
def test_closed_model_sends_requests_and_reports_everything(tape):
    result = go(tape, users=3)
    s = result.summary
    assert result.model == "closed" and s["requests"] > 20 and s["errors"] == 0
    assert s["p95_ms"] is not None and s["rps"] > 10
    assert {e["endpoint"] for e in result.endpoints} == {"GET /items", "GET /items/{id}"}
    assert result.ticks and all(t.errors == 0 for t in result.ticks)


def test_ramp_starts_users_gradually(tape):
    seen = []
    go(tape, users=4, ramp=1.6, duration=2.2, on_tick=lambda t: seen.append(t.users))
    assert max(seen) == 4 and seen[0] < 4


def test_open_model_keeps_to_the_requested_rate(tape):
    result = go(tape, rate=40, duration=1.5)
    assert result.model == "open" and result.options["users"] is None
    assert 45 <= result.summary["requests"] <= 62  # 40/s for 1.5 s = 60, minus timer slack


def test_a_slow_server_cannot_hide_in_the_open_model():
    """The server needs 0.3 s per request. One user could only ever do ~3 per second; the open model keeps sending
    20 per second and the latency numbers show the real wait."""

    async def slow(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={})

    entry = Entry(method="GET", path="/x", status=200, resp_body=b"{}")
    result = go([entry], rate=20, duration=1.5, transport=httpx.MockTransport(slow))
    assert result.summary["requests"] >= 24  # 30 were sent; a closed single user would have managed ~5
    assert 280 <= result.summary["p50_ms"] <= 450


def test_overload_drops_requests_instead_of_piling_up_forever():
    async def stuck(request):
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={})

    entry = Entry(method="GET", path="/x", status=200, resp_body=b"{}")
    result = go([entry], rate=50, duration=1.0, max_inflight=5, transport=httpx.MockTransport(stuck))
    assert result.dropped > 20


def test_server_errors_and_connection_failures_are_counted():
    entry = Entry(method="GET", path="/x", status=200)
    broken = go([entry], users=2, duration=0.6, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert broken.summary["error_rate"] == 1.0 and broken.summary["status_codes"] == {500: broken.summary["requests"]}

    def refuse(request):
        raise httpx.ConnectError("no")

    down = go([entry], users=2, duration=0.6, transport=httpx.MockTransport(refuse))
    assert down.summary["error_rate"] == 1.0 and 0 in down.summary["status_codes"]


def test_client_errors_are_not_server_failures():
    entry = Entry(method="GET", path="/missing", status=404)
    result = go([entry], users=1, duration=0.5, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert result.summary["errors"] == 0


def test_writes_and_scrubbed_requests_are_not_sent_by_default():
    entries = record_calls(
        sample_app,
        [
            ("GET", "/items"),
            ("POST", "/items", {"name": "a", "price": 1}),
            ("POST", "/login", {"email": "a@b.c", "password": "pw"}),
        ],
    )
    sent = []

    def handler(request):
        sent.append(request.method + " " + request.url.path)
        return httpx.Response(200, json={})

    result = go(entries, users=1, duration=0.5, transport=httpx.MockTransport(handler))
    assert result.skipped == 2 and set(sent) == {"GET /items"}


def test_with_allow_writes_only_scrubbed_bodies_are_still_held_back():
    entries = record_calls(
        sample_app,
        [
            ("GET", "/items"),
            ("POST", "/items", {"name": "a", "price": 1}),
            ("POST", "/login", {"email": "a@b.c", "password": "pw"}),
        ],
    )
    sent = set()

    def handler(request):
        sent.add(request.method + " " + request.url.path)
        return httpx.Response(200, json={})

    go(entries, users=1, duration=0.5, allow_writes=True, transport=httpx.MockTransport(handler))
    assert sent == {"GET /items", "POST /items"}


def test_nothing_to_send_is_a_clear_error():
    entries = record_calls(sample_app, [("POST", "/items", {"name": "a", "price": 1})])
    with pytest.raises(ValueError, match="nothing to send"):
        go(entries, users=1)


def test_bad_settings_and_remote_targets_are_refused(tape):
    with pytest.raises(ValueError, match="allow-remote"):
        asyncio.run(run_load(tape, LoadOptions(target="https://api.example.com")))
    with pytest.raises(ValueError, match="--users"):
        go(tape, users=0)
    with pytest.raises(ValueError, match="--rate"):
        go(tape, rate=-1)


def test_fresh_headers_are_sent(tape):
    seen = set()

    def handler(request):
        seen.add(request.headers.get("authorization"))
        return httpx.Response(200, json={})

    go(tape, users=1, duration=0.4, headers={"authorization": "Bearer abc"}, transport=httpx.MockTransport(handler))
    assert seen == {"Bearer abc"}


# --------------------------------------------------------------------------------------------- reports
def test_html_report_has_charts_numbers_and_the_knee(tape):
    result = go(tape, users=2, duration=1.2)
    result.knee = {"t": 5, "users": 12, "p95_ms": 300.0, "baseline_p95_ms": 20.0, "limit_ms": 40.0}
    page = render_html(result, "my tape")
    assert page.count("<svg") == 4 and "my tape" in page and "GET /items/{id}" in page
    assert "12 users" in page and "95th percentile" in page


def test_json_report_is_serialisable_and_complete(tape):
    data = to_dict(go(tape, users=2, duration=1.0), "t")
    json.dumps(data)
    assert {"summary", "ticks", "endpoints", "options", "model"} <= data.keys()


def test_chart_handles_empty_and_gappy_data():
    assert "<svg" in line_chart("Empty", "ms", {"p95": []})
    svg = line_chart("Gaps", "ms", {"p95": [(0, 10.0), (1, None), (2, 30.0)]})
    assert "polyline" in svg


# -------------------------------------------------------------------------------------------------- CLI
@pytest.fixture
def cli_tape(tmp_path, tape):
    db = tmp_path / "t.db"
    with Storage(db) as s:
        t = s.create_tape("demo", source="record")
        s.add_entries(t.id, tape)
    return db


def test_cli_runs_a_load_test_and_writes_reports(cli_tape, serve, tmp_path):
    url = serve(sample_app)
    html_file, json_file = tmp_path / "l.html", tmp_path / "l.json"
    out = CliRunner().invoke(
        cli,
        [
            "load",
            "demo",
            "--target",
            url,
            "--users",
            "2",
            "--duration",
            "1.5",
            "--db",
            str(cli_tape),
            "--report",
            str(html_file),
            "--json",
            str(json_file),
        ],
    )
    assert out.exit_code == 0, out.output
    assert "requests" in out.output and "p95" in out.output
    assert html_file.exists() and json.loads(json_file.read_text())["summary"]["requests"] > 0


def test_cli_thresholds_fail_the_run_for_ci(cli_tape, serve):
    url = serve(sample_app)
    args = ["load", "demo", "--target", url, "--users", "1", "--duration", "1", "--db", str(cli_tape)]
    assert CliRunner().invoke(cli, [*args, "--fail-p95", "100000"]).exit_code == 0
    too_strict = CliRunner().invoke(cli, [*args, "--fail-p95", "0.0001"])
    assert too_strict.exit_code == 1 and "FAIL" in too_strict.output


def test_cli_refuses_remote_targets(cli_tape):
    out = CliRunner().invoke(cli, ["load", "demo", "--target", "https://api.example.com", "--db", str(cli_tape)])
    assert out.exit_code == 2 and "allow-remote" in out.output

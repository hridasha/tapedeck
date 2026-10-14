import asyncio
import json
import time

import httpx
import pytest
from typer.testing import CliRunner

from examples.sample_api import app as sample_module
from examples.sample_api.app import app as sample_app
from tapedeck.cli import app as cli
from tapedeck.core.models import Entry
from tapedeck.core.storage import Storage
from tapedeck.replay.diff import CHANGED, EXTRA, MISSING, TYPE, failing, is_ignored, json_diff
from tapedeck.replay.report import render_html, to_dict
from tapedeck.replay.runner import ReplayOptions, replay

from .helpers import record_calls

IN_PROCESS = httpx.ASGITransport(app=sample_app)


def run_replay(entries, **kw):
    kw.setdefault("transport", IN_PROCESS)
    return asyncio.run(replay(entries, ReplayOptions(target="http://test", **kw)))


# ----------------------------------------------------------------------------------------------- diff
def test_diff_reports_changed_missing_extra_and_type():
    diffs = json_diff({"a": 1, "b": 2, "c": "x", "l": [1, 2]}, {"a": 9, "c": 5, "d": 1, "l": [1]})
    got = {(d.path, d.kind) for d in diffs}
    assert got == {("$.a", CHANGED), ("$.b", MISSING), ("$.c", TYPE), ("$.d", EXTRA), ("$.l[1]", MISSING)}


def test_identical_values_have_no_diff_and_int_float_are_compatible():
    assert json_diff({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == []
    assert [d.kind for d in json_diff({"p": 1}, {"p": 1.5})] == [CHANGED]


def test_bool_is_not_a_number():
    assert [d.kind for d in json_diff({"p": True}, {"p": 1})] == [TYPE]


def test_ignore_paths_skip_whole_subtrees_and_list_items():
    exp = {"meta": {"t": 1}, "items": [{"at": 1, "n": "a"}, {"at": 2, "n": "b"}]}
    act = {"meta": {"t": 2}, "items": [{"at": 7, "n": "a"}, {"at": 8, "n": "b"}]}
    assert json_diff(exp, act, ignore=["$.meta", "$.items[].at"]) == []
    assert is_ignored("$.items[3].at", ["$.items[].at"])
    assert not is_ignored("$.items[3].n", ["$.items[].at"])


def test_timestamp_changes_are_volatile_not_failures():
    diffs = json_diff({"now": "2025-01-01T00:00:00Z", "n": 1}, {"now": "2025-06-01T12:00:00Z", "n": 1})
    assert len(diffs) == 1 and diffs[0].volatile and failing(diffs) == []
    assert failing(json_diff({"now": "2025-01-01T00:00:00Z"}, {"now": "yesterday"})) != []
    assert (
        json_diff({"now": "2025-01-01T00:00:00Z"}, {"now": "2025-02-01T00:00:00Z"}, forgive_volatile=False)[0].volatile
        is False
    )


# --------------------------------------------------------------------------------------------- replay
@pytest.fixture
def tape():
    entries = record_calls(
        sample_app,
        [
            ("GET", "/items?page=1"),
            ("GET", "/items/1"),
            ("GET", "/items/999"),
            ("GET", "/time"),
            ("POST", "/items", {"name": "cable", "price": 2.0}),
            ("POST", "/login", {"email": "a@b.c", "password": "hunter2"}),
        ],
    )
    sample_module.ITEMS.pop(3, None)  # recording the POST created item 3; go back to the state the tape starts from
    return entries


def test_unchanged_api_passes_and_timestamps_are_forgiven(tape):
    time.sleep(0.05)  # the Windows clock ticks every ~15 ms; make sure /time really returns a new value
    result = run_replay(tape)
    assert result.ok, [(r.path, r.reasons) for r in result.results if r.outcome == "fail"]
    time_row = next(r for r in result.results if r.path == "/time")
    assert time_row.outcome == "pass" and any("timestamp" in w for w in time_row.warnings)


def test_a_changed_field_is_reported_with_its_exact_path(tape):
    sample_module.ITEMS[1]["price"] = 50.0
    result = run_replay(tape)
    row = next(r for r in result.results if r.path == "/items/1")
    assert row.outcome == "fail" and not result.ok
    assert [(d.path, d.expected, d.actual) for d in failing(row.diffs)] == [("$.price", 49.0, 50.0)]


def test_list_changes_show_up_inside_list_items(tape):
    sample_module.ITEMS[2]["name"] = "trackball"
    row = next(r for r in run_replay(tape).results if r.path == "/items" and r.query == "page=1")
    assert row.outcome == "fail" and failing(row.diffs)[0].path == "$.items[1].name"


def test_status_change_is_a_failure(tape):
    del sample_module.ITEMS[1]
    row = next(r for r in run_replay(tape).results if r.path == "/items/1")
    assert row.outcome == "fail" and "status 200 -> 404" in row.reasons[0]


def test_ignore_list_hides_known_noise(tape):
    sample_module.ITEMS[1]["price"] = 50.0
    result = run_replay(tape, ignore={"$.price", "$.items[].price"})
    assert next(r for r in result.results if r.path == "/items/1").outcome == "pass"


def test_writes_are_skipped_unless_allowed(tape):
    result = run_replay(tape)
    post = next(r for r in result.results if r.method == "POST" and r.path == "/items")
    assert post.outcome == "skipped" and "--allow-writes" in post.detail
    assert 3 not in sample_module.ITEMS

    run_replay(tape, allow_writes=True)
    assert 3 in sample_module.ITEMS  # it really was sent this time


def test_requests_whose_body_was_scrubbed_are_never_sent(tape):
    result = run_replay(tape, allow_writes=True)
    login = next(r for r in result.results if r.path == "/login")
    assert login.outcome == "skipped" and "secrets hidden" in login.detail


def test_learn_noise_ignores_fields_that_change_on_their_own():
    counter = {"n": 0}

    def handler(request):
        counter["n"] += 1
        return httpx.Response(200, json={"stable": 1, "tick": counter["n"]})

    recorded = [
        Entry(
            method="GET",
            path="/t",
            status=200,
            resp_body=b'{"stable":1,"tick":0}',
            resp_headers={"content-type": "application/json"},
        )
    ]
    transport = httpx.MockTransport(handler)
    assert run_replay(recorded, transport=transport).results[0].outcome == "fail"
    assert run_replay(recorded, transport=transport, learn_noise=True).results[0].outcome == "pass"


def test_remote_targets_are_refused_unless_allowed():
    with pytest.raises(ValueError, match="allow-remote"):
        asyncio.run(replay([], ReplayOptions(target="https://api.example.com")))


def test_an_unreachable_server_is_an_error_not_a_crash(tape):
    def boom(request):
        raise httpx.ConnectError("refused")

    result = run_replay(tape[:2], transport=httpx.MockTransport(boom))
    assert [r.outcome for r in result.results] == ["error", "error"] and "ConnectError" in result.results[0].detail


def test_slow_answers_warn_and_can_fail():
    entry = Entry(
        method="GET",
        path="/slow",
        query="ms=150",
        status=200,
        resp_body=b'{"waited_ms":150}',
        resp_headers={"content-type": "application/json"},
        duration_ms=1.0,
    )
    row = run_replay([entry]).results[0]
    assert row.outcome == "pass" and any("slower" in w for w in row.warnings)
    assert run_replay([entry], fail_on_slow=True).results[0].outcome == "fail"


def test_fresh_headers_replace_hidden_ones():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    e = Entry(
        method="GET",
        path="/x",
        status=200,
        resp_body=b"{}",
        resp_headers={"content-type": "application/json"},
        req_headers={"authorization": "***REDACTED***"},
    )
    run_replay([e], transport=httpx.MockTransport(handler))
    assert seen["auth"] is None
    run_replay([e], transport=httpx.MockTransport(handler), headers={"authorization": "Bearer fresh"})
    assert seen["auth"] == "Bearer fresh"


def test_non_json_bodies_are_compared_as_text():
    e = Entry(method="GET", path="/t", status=200, resp_body=b"hello", resp_headers={"content-type": "text/plain"})
    same = httpx.MockTransport(lambda r: httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"}))
    other = httpx.MockTransport(lambda r: httpx.Response(200, content=b"bye", headers={"content-type": "text/plain"}))
    assert run_replay([e], transport=same).results[0].outcome == "pass"
    assert run_replay([e], transport=other).results[0].outcome == "fail"


# --------------------------------------------------------------------------------------------- reports
def test_report_shows_the_failing_path_and_escapes_html(tape):
    sample_module.ITEMS[1]["name"] = "<script>alert(1)</script>"
    result = run_replay(tape)
    page = render_html(result, "my tape")
    assert "$.name" in page and "<script>alert" not in page and "&lt;script&gt;" in page
    assert "my tape" in page and "fail" in page


def test_json_report_has_summary_and_diffs(tape):
    sample_module.ITEMS[1]["price"] = 50.0
    data = to_dict(run_replay(tape), "t")
    assert data["ok"] is False and data["summary"]["fail"] >= 1
    row = next(r for r in data["results"] if r["path"] == "/items/1")
    assert row["diffs"][0]["path"] == "$.price"
    json.dumps(data)  # must be serialisable


# ------------------------------------------------------------------------------------------------ CLI
@pytest.fixture
def cli_tape(tmp_path, tape):
    db = tmp_path / "t.db"
    with Storage(db) as s:
        t = s.create_tape("demo", source="record")
        s.add_entries(t.id, tape)
    return db


def test_cli_exit_codes_and_reports(cli_tape, serve, tmp_path):
    url = serve(sample_app)
    sample_module.ITEMS.pop(3, None)
    runner = CliRunner()
    html_file, json_file = tmp_path / "r.html", tmp_path / "r.json"

    ok = runner.invoke(
        cli,
        [
            "replay",
            "demo",
            "--target",
            url,
            "--db",
            str(cli_tape),
            "--report",
            str(html_file),
            "--json",
            str(json_file),
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert "passed" in ok.output and html_file.exists() and json.loads(json_file.read_text())["ok"] is True

    sample_module.ITEMS[1]["price"] = 99.0
    bad = runner.invoke(cli, ["replay", "demo", "--target", url, "--db", str(cli_tape)])
    assert bad.exit_code == 1 and "$.price" in bad.output and "FAIL" in bad.output

    ignored = runner.invoke(
        cli, ["replay", "demo", "--target", url, "--db", str(cli_tape), "-i", "$.price", "-i", "$.items[].price"]
    )
    assert ignored.exit_code == 0


def test_cli_refuses_remote_targets_and_unknown_tapes(cli_tape):
    runner = CliRunner()
    remote = runner.invoke(cli, ["replay", "demo", "--target", "https://api.example.com", "--db", str(cli_tape)])
    assert remote.exit_code == 2 and "allow-remote" in remote.output
    ghost = runner.invoke(cli, ["replay", "ghost", "--target", "http://localhost:1", "--db", str(cli_tape)])
    assert ghost.exit_code == 1 and "No tape called" in ghost.output

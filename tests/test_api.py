import json
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from examples.sample_api.app import app as sample_app
from tapedeck.api.app import body_view, create_api_app
from tapedeck.core.storage import Storage

from .helpers import record_calls


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture
def api(db):
    with TestClient(create_api_app(db)) as client:
        yield client


@pytest.fixture
def har_bytes(sample_har):
    return json.dumps(sample_har).encode()


def import_tape(api, har_bytes, name="demo", **form):
    return api.post("/api/tapes/import-har", files={"file": ("s.har", har_bytes)}, data={"name": name, **form})


@pytest.fixture
def recorded(db):
    """A tape recorded from the sample API, ready for mock / replay / load."""
    entries = record_calls(sample_app, [("GET", "/items?page=1"), ("GET", "/items/1"), ("GET", "/items/2")])
    with Storage(db) as s:
        t = s.create_tape("rec", source="record")
        s.add_entries(t.id, entries)
    return "rec"


def wait_for(api, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = api.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------------------------------ tapes
def test_health_and_empty_list(api):
    assert api.get("/api/health").json()["ok"] is True
    assert api.get("/api/tapes").json() == []


def test_import_har_creates_a_tape_with_secrets_hidden(api, har_bytes):
    r = import_tape(api, har_bytes)
    assert r.status_code == 201 and r.json()["name"] == "demo" and r.json()["entry_count"] == 4
    assert [t["name"] for t in api.get("/api/tapes").json()] == ["demo"]
    detail = api.get("/api/tapes/demo/entries").json()[0]
    body = api.get(f"/api/tapes/demo/entries/{detail['id']}").json()
    assert body["request"]["headers"]["authorization"] == "***REDACTED***"
    assert json.loads(body["request"]["body"]["text"])["password"] == "***REDACTED***"


def test_import_name_defaults_to_the_file_name_and_duplicates_conflict(api, har_bytes):
    r = api.post("/api/tapes/import-har", files={"file": ("my session.har", har_bytes)})
    assert r.json()["name"] == "my session"
    assert import_tape(api, har_bytes, "x").status_code == 201
    assert import_tape(api, har_bytes, "x").status_code == 409


def test_bad_uploads_are_rejected_with_a_message(api):
    assert api.post("/api/tapes/import-har", files={"file": ("a.har", b"not json")}).status_code == 400
    empty = json.dumps({"log": {"entries": []}}).encode()
    r = api.post("/api/tapes/import-har", files={"file": ("a.har", empty)})
    assert r.status_code == 400 and "no API calls" in r.json()["detail"]


def test_entry_list_and_detail_and_binary_bodies(api, har_bytes):
    import_tape(api, har_bytes, keep_static="true")
    rows = api.get("/api/tapes/demo/entries").json()
    assert {"id", "order", "method", "path", "status", "duration_ms", "size"} <= rows[0].keys()
    png = next(r for r in rows if r["path"] == "/logo.png")
    detail = api.get(f"/api/tapes/demo/entries/{png['id']}").json()
    assert detail["response"]["body"]["binary"] is True and detail["response"]["body"]["base64"]
    items = next(r for r in rows if r["path"] == "/items")
    body = api.get(f"/api/tapes/demo/entries/{items['id']}").json()["response"]["body"]
    assert body["is_json"] and json.loads(body["text"])["page"] == 1
    assert api.get("/api/tapes/demo/entries/99999").status_code == 404


def test_delete_and_unknown_tapes(api, har_bytes):
    import_tape(api, har_bytes)
    assert api.delete("/api/tapes/demo").status_code == 204
    assert api.get("/api/tapes").json() == []
    assert api.delete("/api/tapes/demo").status_code == 404
    assert api.get("/api/tapes/nope/entries").status_code == 404


def test_noise_candidates_can_be_saved(api, har_bytes):
    import_tape(api, har_bytes)
    cand = api.get("/api/tapes/demo/noise").json()["candidates"]
    assert "$.name" in cand  # the same request was recorded twice with a different answer
    assert api.put("/api/tapes/demo/noise", json={"paths": ["$.name"]}).json()["saved"] == ["$.name"]
    assert api.get("/api/tapes").json()[0]["noise"] == ["$.name"]


def test_body_view_truncates_and_pretty_prints():
    assert body_view(b"")["size"] == 0
    assert body_view(b'{"a":1}')["text"] == '{\n  "a": 1\n}'
    assert body_view(b"x" * 300_000)["truncated"] is True
    assert body_view(b"\xff\xfe")["binary"] is True


# ---------------------------------------------------------------------------------------- servers
def test_a_mock_can_be_started_used_and_stopped(api, recorded):
    port = free_port()
    started = api.post("/api/servers/mock", json={"tape": recorded, "port": port})
    assert started.status_code == 201 and started.json()["id"] == f"mock:{port}"

    r = httpx.get(f"http://127.0.0.1:{port}/items/1")
    assert r.status_code == 200 and r.json()["name"] == "keyboard"
    log = api.get(f"/api/servers/mock:{port}/log").json()
    assert log[-1]["path"] == "/items/1" and log[-1]["match"] == "exact"
    assert api.get("/api/servers").json()[0]["requests_served"] == 1

    assert api.delete(f"/api/servers/mock:{port}").status_code == 204
    assert api.get("/api/servers").json() == []
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{port}/items/1")


def test_mock_errors_are_explained(api, recorded):
    assert api.post("/api/servers/mock", json={"tape": "ghost", "port": free_port()}).status_code == 404
    port = free_port()
    api.post("/api/servers/mock", json={"tape": recorded, "port": port})
    again = api.post("/api/servers/mock", json={"tape": recorded, "port": port})
    assert again.status_code == 400 and "already in use" in again.json()["detail"]
    assert (
        api.post("/api/servers/mock", json={"tape": recorded, "port": port + 1, "latency": "fast"}).status_code == 400
    )
    api.delete(f"/api/servers/mock:{port}")


def test_mock_options_are_applied(api, recorded):
    port = free_port()
    api.post("/api/servers/mock", json={"tape": recorded, "port": port, "fail_rate": 1.0})
    assert httpx.get(f"http://127.0.0.1:{port}/items/1").status_code == 503
    api.delete(f"/api/servers/mock:{port}")


def test_a_recorder_can_be_started_through_the_api(api, serve, db):
    target = serve(sample_app)
    port = free_port()
    assert api.post("/api/servers/record", json={"tape": "live", "target": target, "port": port}).status_code == 201
    assert httpx.get(f"http://127.0.0.1:{port}/items").json()["page"] == 1
    assert api.get(f"/api/servers/record:{port}/log").json()["recorded"] == 1

    clash = api.post("/api/servers/record", json={"tape": "live", "target": target, "port": free_port()})
    assert clash.status_code == 409
    api.delete(f"/api/servers/record:{port}")
    with Storage(db) as s:
        assert s.get_tape("live").entry_count == 1


# ------------------------------------------------------------------------------------------ jobs
def test_replay_job_runs_and_reports(api, recorded, serve):
    target = serve(sample_app)
    job = api.post("/api/replays", json={"tape": recorded, "target": target}).json()
    done = wait_for(api, job["id"])
    assert done["status"] == "done" and done["result"]["ok"] is True and done["result"]["summary"]["pass"] == 3
    page = api.get(f"/api/jobs/{job['id']}/report.html")
    assert page.status_code == 200 and "Replay" in page.text
    assert any(j["id"] == job["id"] for j in api.get("/api/jobs").json())


def test_replay_reports_what_changed(api, recorded, serve):
    from examples.sample_api import app as module

    module.ITEMS[1]["price"] = 99.0
    done = wait_for(api, api.post("/api/replays", json={"tape": recorded, "target": serve(sample_app)}).json()["id"])
    assert done["result"]["ok"] is False
    changed = next(r for r in done["result"]["results"] if r["path"] == "/items/1")
    assert changed["diffs"][0]["path"] == "$.price"


def test_load_job_streams_ticks_and_ends_with_a_summary(api, recorded, serve):
    job = api.post(
        "/api/loads", json={"tape": recorded, "target": serve(sample_app), "users": 2, "duration": 2.2}
    ).json()
    done = wait_for(api, job["id"])
    assert done["status"] == "done" and done["result"]["summary"]["requests"] > 10
    assert len(done["ticks"]) >= 2 and "Load test" in api.get(f"/api/jobs/{job['id']}/report.html").text


def test_a_running_load_test_can_be_cancelled(api, recorded, serve):
    job = api.post(
        "/api/loads", json={"tape": recorded, "target": serve(sample_app), "users": 1, "duration": 60}
    ).json()
    time.sleep(0.5)
    assert api.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert wait_for(api, job["id"])["status"] == "cancelled"


def test_remote_targets_and_unknown_tapes_are_refused_up_front(api, recorded):
    remote = api.post("/api/replays", json={"tape": recorded, "target": "https://api.example.com"})
    assert remote.status_code == 400 and "allow-remote" in remote.json()["detail"]
    assert api.post("/api/loads", json={"tape": "ghost", "target": "http://localhost:1"}).status_code == 404
    assert (
        api.post("/api/loads", json={"tape": recorded, "target": "http://localhost:1", "users": 0}).status_code == 422
    )


def test_unreachable_server_makes_the_job_report_errors_not_crash(api, recorded):
    done = wait_for(
        api, api.post("/api/replays", json={"tape": recorded, "target": f"http://127.0.0.1:{free_port()}"}).json()["id"]
    )
    assert done["status"] == "done" and done["result"]["summary"]["error"] == 3


def test_job_websocket_pushes_progress_until_the_end(api, recorded, serve):
    job = api.post(
        "/api/loads", json={"tape": recorded, "target": serve(sample_app), "users": 1, "duration": 1.5}
    ).json()
    seen = []
    with api.websocket_connect(f"/ws/jobs/{job['id']}") as ws:
        while True:
            try:
                seen.append(json.loads(ws.receive_text()))
            except Exception:
                break
    assert seen and seen[-1]["status"] == "done" and seen[-1]["result"]["summary"]["requests"] > 0


def test_unknown_job(api):
    assert api.get("/api/jobs/nope").status_code == 404
    assert api.delete("/api/jobs/nope").status_code == 404
    with pytest.raises(Exception):  # noqa: B017 - the test client raises a bare disconnect error
        with api.websocket_connect("/ws/jobs/nope") as ws:
            ws.receive_text()


# --------------------------------------------------------------------------------------- the page
def test_the_built_page_is_served_with_spa_fallback(db, tmp_path):
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>tapedeck ui</html>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    with TestClient(create_api_app(db, web_dir=web)) as c:
        assert "tapedeck ui" in c.get("/").text
        assert "tapedeck ui" in c.get("/tapes/demo").text  # a page route, handled by the browser app
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404
        assert "nope" not in c.get("/..%2Fsecret.txt").text
        assert c.get("/api/health").json()["ok"] is True

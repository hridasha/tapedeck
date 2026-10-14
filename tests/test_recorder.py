import gzip
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from examples.sample_api.app import app as sample_app
from tapedeck.core.scrub import REDACTED
from tapedeck.core.storage import Storage
from tapedeck.recorder.proxy import create_recorder_app


@pytest.fixture
def store(tmp_path):
    with Storage(tmp_path / "t.db") as s:
        yield s


@pytest.fixture
def rec(store):
    tape = store.create_tape("live", source="record")
    app = create_recorder_app(store, tape.id, "http://sample", transport=httpx.ASGITransport(app=sample_app))
    with TestClient(app) as client:
        yield client, store, tape


def test_answers_pass_through_unchanged_and_are_recorded(rec):
    client, store, tape = rec
    r = client.get("/items", params={"page": 2})
    assert r.status_code == 200 and r.json()["page"] == 2

    entries = store.get_entries(tape.id)
    assert len(entries) == 1
    e = entries[0]
    assert (e.method, e.path, e.query, e.status) == ("GET", "/items", "page=2", 200)
    assert json.loads(e.resp_body)["page"] == 2
    assert e.duration_ms > 0 and e.started_at and e.order == 0


def test_post_body_and_status_are_forwarded_and_recorded(rec):
    client, store, tape = rec
    r = client.post("/items", json={"name": "cable", "price": 3.5})
    assert r.status_code == 201 and r.json()["name"] == "cable"
    e = store.get_entries(tape.id)[0]
    assert (e.method, e.status) == ("POST", 201)
    assert json.loads(e.req_body) == {"name": "cable", "price": 3.5}


def test_secrets_are_hidden_in_the_tape_but_the_app_still_gets_the_real_answer(rec):
    client, store, tape = rec
    r = client.post("/login", json={"email": "a@b.c", "password": "hunter2"}, headers={"Authorization": "Bearer real"})
    assert r.json()["access_token"] == "super-secret-token"  # the caller is not affected
    e = store.get_entries(tape.id)[0]
    assert e.req_headers["authorization"] == REDACTED
    assert json.loads(e.req_body)["password"] == REDACTED
    assert json.loads(e.resp_body)["access_token"] == REDACTED


def test_no_scrub_keeps_everything(store):
    tape = store.create_tape("raw")
    app = create_recorder_app(
        store, tape.id, "http://sample", scrub=False, transport=httpx.ASGITransport(app=sample_app)
    )
    with TestClient(app) as c:
        c.post("/login", json={"email": "a@b.c", "password": "hunter2"})
    assert json.loads(store.get_entries(tape.id)[0].req_body)["password"] == "hunter2"


def test_upstream_errors_are_recorded_too(rec):
    client, store, tape = rec
    assert client.get("/items/999").status_code == 404
    assert store.get_entries(tape.id)[0].status == 404


def test_unreachable_target_gives_502_and_nothing_is_recorded(store):
    tape = store.create_tape("down")

    def boom(request):
        raise httpx.ConnectError("refused")

    app = create_recorder_app(store, tape.id, "http://nowhere", transport=httpx.MockTransport(boom))
    with TestClient(app) as c:
        r = c.get("/x")
    assert r.status_code == 502 and "could not reach" in r.json()["error"]
    assert store.get_entries(tape.id) == []


def test_preflight_is_forwarded_but_not_recorded(rec):
    client, store, tape = rec
    client.options("/items")
    assert store.get_entries(tape.id) == []


def test_streaming_responses_are_passed_through_not_recorded(store):
    tape = store.create_tape("sse")

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: hi\n\n")

    app = create_recorder_app(store, tape.id, "http://sse", transport=httpx.MockTransport(handler))
    with TestClient(app) as c:
        r = c.get("/events")
        status = c.get("/__tapedeck/status").json()
    assert r.content == b"data: hi\n\n"
    assert store.get_entries(tape.id) == []
    assert status["skipped_streaming"] == 1


def test_huge_responses_are_passed_through_not_recorded(store):
    tape = store.create_tape("big")

    def handler(request):
        return httpx.Response(200, content=b"x" * 3000)

    app = create_recorder_app(store, tape.id, "http://big", max_body_bytes=1000, transport=httpx.MockTransport(handler))
    with TestClient(app) as c:
        assert len(c.get("/blob").content) == 3000
    assert store.get_entries(tape.id) == []


def test_compressed_answers_are_stored_readable(store):
    tape = store.create_tape("gz")
    payload = json.dumps({"hello": "world"}).encode()

    def handler(request):
        return httpx.Response(
            200,
            content=gzip.compress(payload),
            headers={"content-encoding": "gzip", "content-type": "application/json"},
        )

    app = create_recorder_app(store, tape.id, "http://gz", transport=httpx.MockTransport(handler))
    with TestClient(app) as c:
        r = c.get("/data")
    assert r.json() == {"hello": "world"}
    e = store.get_entries(tape.id)[0]
    assert json.loads(e.resp_body) == {"hello": "world"}
    assert "content-encoding" not in e.resp_headers


def test_order_continues_when_appending_to_a_tape(store):
    tape = store.create_tape("again", source="record")
    for _ in range(2):
        app = create_recorder_app(store, tape.id, "http://sample", transport=httpx.ASGITransport(app=sample_app))
        with TestClient(app) as c:
            c.get("/items")
    assert [e.order for e in store.get_entries(tape.id)] == [0, 1]


def test_status_endpoint_counts_recorded_requests(rec):
    client, _, _ = rec
    client.get("/items")
    client.get("/items")
    assert client.get("/__tapedeck/status").json()["recorded"] == 2


def test_a_recorded_tape_can_be_mocked_straight_away(rec):
    from tapedeck.mock.server import create_mock_app

    client, store, tape = rec
    client.get("/items/1")
    with TestClient(create_mock_app(store.get_entries(tape.id))) as mock:
        r = mock.get("/items/1")
    assert r.status_code == 200 and r.json()["name"] == "keyboard"

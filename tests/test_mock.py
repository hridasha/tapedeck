import pytest
from fastapi.testclient import TestClient

from tapedeck.core.har import parse_har
from tapedeck.mock.server import create_mock_app


@pytest.fixture
def client(sample_har):
    with TestClient(create_mock_app(parse_har(sample_har))) as c:
        yield c


def test_answers_with_the_recorded_body_and_status(client):
    r = client.get("/items", params={"page": 1})
    assert r.status_code == 200
    assert r.json() == {"items": [{"id": 1}], "page": 1}
    assert r.headers["x-tapedeck-match"] == "exact"


def test_noisy_query_values_do_not_break_matching(client):
    r = client.get("/items?page=1&_=424242")
    assert r.status_code == 200 and r.headers["x-tapedeck-match"] == "exact"


def test_other_ids_fall_back_to_the_same_shape(client):
    r = client.get("/items/999")
    assert r.status_code == 200
    assert r.headers["x-tapedeck-match"] == "shape"


def test_repeated_calls_return_the_recorded_answers_in_order(client):
    first = client.get("/items/1").json()["name"]
    second = client.get("/items/1").json()["name"]
    third = client.get("/items/1").json()["name"]
    assert (first, second, third) == ("keyboard", "keyboard v2", "keyboard v2")


def test_unrecorded_request_gets_a_helpful_404(client):
    r = client.get("/never/recorded")
    assert r.status_code == 404
    assert r.headers["x-tapedeck-match"] == "none"
    body = r.json()
    assert body["path"] == "/never/recorded" and "hint" in body


def test_hidden_secrets_are_what_the_mock_serves(client):
    r = client.post("/login", json={"email": "a@b.c", "password": "anything"})
    assert r.json()["access_token"] == "***REDACTED***"


def test_headers_about_the_original_encoding_are_dropped(client):
    r = client.post("/login", json={"email": "a@b.c", "password": "anything"})
    assert "gzip" not in r.headers.get("content-encoding", "")
    assert r.json()  # body is readable, so no leftover gzip header confused the client


def test_cors_preflight_and_headers_work_for_browsers(client):
    pre = client.options("/items", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200
    r = client.get("/items?page=1", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_fail_rate_one_always_fails_with_503(sample_har):
    with TestClient(create_mock_app(parse_har(sample_har), fail_rate=1.0)) as c:
        r = c.get("/items?page=1")
    assert r.status_code == 503 and r.headers["x-tapedeck-match"] == "injected"


def test_fixed_latency_delays_the_answer(sample_har):
    import time

    with TestClient(create_mock_app(parse_har(sample_har), latency=150)) as c:
        t = time.perf_counter()
        c.get("/items?page=1")
        assert time.perf_counter() - t >= 0.14


def test_log_endpoint_reports_what_was_matched(client):
    client.get("/items?page=1")
    client.get("/nope")
    log = client.get("/__tapedeck/log").json()
    assert [(row["path"], row["match"]) for row in log] == [("/items", "exact"), ("/nope", "none")]
    assert client.get("/__tapedeck/health").json()["entries"] == 4
    client.post("/__tapedeck/reset")
    assert client.get("/__tapedeck/log").json() == []

import json

import pytest

from tapedeck.core.har import parse_har
from tapedeck.core.scrub import REDACTED


def test_skips_static_files_preflight_and_blocked_requests(sample_har):
    entries = parse_har(sample_har)
    assert [(e.method, e.path) for e in entries] == [
        ("POST", "/login"),
        ("GET", "/items"),
        ("GET", "/items/1"),
        ("GET", "/items/1"),
    ]


def test_keep_static_brings_assets_back_but_never_preflight(sample_har):
    paths = [e.path for e in parse_har(sample_har, keep_static=True)]
    assert "/logo.png" in paths and "/assets/app.js" in paths
    assert not any(e.method == "OPTIONS" for e in parse_har(sample_har, keep_static=True))


def test_entries_get_consecutive_order_numbers(sample_har):
    assert [e.order for e in parse_har(sample_har)] == [0, 1, 2, 3]


def test_query_and_timing_and_headers_are_lowercased_and_kept(sample_har):
    entries = parse_har(sample_har)
    items = entries[1]
    assert items.query == "page=1&_=1699999"
    assert items.duration_ms == 40
    assert "content-type" in entries[0].req_headers


def test_secrets_are_hidden_by_default(sample_har):
    login = parse_har(sample_har)[0]
    assert login.req_headers["authorization"] == REDACTED
    assert login.resp_headers["set-cookie"] == REDACTED
    assert json.loads(login.req_body)["password"] == REDACTED
    assert json.loads(login.resp_body)["access_token"] == REDACTED
    assert json.loads(login.resp_body)["token_type"] == "bearer"


def test_no_scrub_keeps_everything(sample_har):
    login = parse_har(sample_har, scrub=False)[0]
    assert login.req_headers["authorization"] == "Bearer xyz"
    assert json.loads(login.req_body)["password"] == "hunter2"


def test_base64_bodies_are_decoded(sample_har):
    png = next(e for e in parse_har(sample_har, keep_static=True) if e.path == "/logo.png")
    assert png.resp_body == b"\x89PNG"


def test_accepts_str_bytes_and_dict(sample_har):
    raw = json.dumps(sample_har)
    assert len(parse_har(raw)) == len(parse_har(raw.encode())) == len(parse_har(sample_har)) == 4


def test_rejects_things_that_are_not_har():
    with pytest.raises(ValueError, match="HAR"):
        parse_har({"hello": "world"})

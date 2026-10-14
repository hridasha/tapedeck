import json

from tapedeck.core.models import Entry
from tapedeck.core.scrub import REDACTED, scrub_body, scrub_entry, scrub_headers


def test_headers_with_secrets_are_hidden_others_kept():
    out = scrub_headers({"Authorization": "Bearer x", "Cookie": "a=b", "Accept": "application/json"})
    assert out["Authorization"] == REDACTED
    assert out["Cookie"] == REDACTED
    assert out["Accept"] == "application/json"


def test_json_secret_fields_are_hidden_at_any_depth():
    body = json.dumps({"user": {"email": "a@b.c", "password": "p"}, "tokens": [{"access_token": "t"}]}).encode()
    data = json.loads(scrub_body(body))
    assert data["user"]["password"] == REDACTED
    assert data["tokens"][0]["access_token"] == REDACTED
    assert data["user"]["email"] == "a@b.c"


def test_lookalike_keys_are_not_hidden():
    body = json.dumps({"token_type": "bearer", "tokens_used": 5, "name": "x"}).encode()
    data = json.loads(scrub_body(body))
    assert data == {"token_type": "bearer", "tokens_used": 5, "name": "x"}


def test_non_json_bodies_are_left_alone():
    assert scrub_body(b"plain text password=1") == b"plain text password=1"
    assert scrub_body(b"") == b""
    assert scrub_body(b"{not json") == b"{not json"


def test_scrub_entry_hits_all_four_places():
    e = scrub_entry(
        Entry(
            method="POST",
            path="/login",
            req_headers={"authorization": "x"},
            req_body=b'{"password":"p"}',
            resp_headers={"set-cookie": "s"},
            resp_body=b'{"token":"t"}',
        )
    )
    assert e.req_headers["authorization"] == REDACTED
    assert e.resp_headers["set-cookie"] == REDACTED
    assert REDACTED in e.req_body.decode() and REDACTED in e.resp_body.decode()

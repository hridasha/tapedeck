import pytest

from tapedeck.core.http import (
    clean_request_headers,
    has_scrubbed_body,
    is_local_target,
    parse_header_args,
    require_allowed_target,
)
from tapedeck.core.scrub import REDACTED


def test_clean_request_headers_drops_what_must_not_travel():
    out = clean_request_headers(
        {
            "Host": "old.example",
            "Connection": "keep-alive",
            "Content-Length": "5",
            "Accept-Encoding": "gzip",
            "Authorization": REDACTED,
            "X-Keep": "yes",
            "content-type": "application/json",
        }
    )
    assert out == {"X-Keep": "yes", "content-type": "application/json"}


def test_overrides_win_regardless_of_case():
    out = clean_request_headers({"Authorization": "Bearer old", "x-a": "1"}, {"authorization": "Bearer new"})
    assert out == {"x-a": "1", "authorization": "Bearer new"}


def test_has_scrubbed_body():
    assert has_scrubbed_body(b'{"password":"***REDACTED***"}')
    assert not has_scrubbed_body(b'{"password":"x"}')


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:5001/x",
        "http://[::1]:9",
        "http://192.168.1.20",
        "http://10.0.0.5",
        "http://172.20.1.1",
        "http://host.docker.internal:8000",
        "http://api:8000",
        "http://app.localhost",
    ],
)
def test_local_targets(url):
    assert is_local_target(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.stripe.com",
        "http://8.8.8.8",
        "http://example.com",
        "https://my-prod.example.org/v1",
        "",
    ],
)
def test_remote_targets(url):
    assert not is_local_target(url)


def test_parse_header_args():
    assert parse_header_args(["Authorization: Bearer x", "X-Env:test"]) == {
        "authorization": "Bearer x",
        "x-env": "test",
    }
    assert parse_header_args(None) == {}
    with pytest.raises(ValueError):
        parse_header_args(["nonsense"])


def test_require_allowed_target():
    require_allowed_target("http://localhost:8000", allow_remote=False)
    require_allowed_target("https://api.example.com", allow_remote=True)
    with pytest.raises(ValueError, match="allow-remote"):
        require_allowed_target("https://api.example.com", allow_remote=False)
    with pytest.raises(ValueError, match="valid target"):
        require_allowed_target("localhost:8000", allow_remote=False)

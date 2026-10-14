"""Small HTTP helpers shared by the recorder, replayer and load tester."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from .scrub import REDACTED

# Headers that describe one network hop, not the message. Never copied from one hop to the next.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def clean_request_headers(headers: dict[str, str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Headers safe to send to a (different) server.

    Drops hop-by-hop headers, Host / Content-Length (the HTTP client sets them), compression negotiation (so
    bodies stay readable) and any value that was hidden by the scrubber. `overrides` (e.g. a fresh
    Authorization) win over what was recorded.
    """
    out = {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP | {"host", "content-length", "accept-encoding"} and v != REDACTED
    }
    for k, v in (overrides or {}).items():
        for existing in [e for e in out if e.lower() == k.lower()]:
            del out[existing]
        out[k.lower()] = v
    return out


def has_scrubbed_body(body: bytes) -> bool:
    """True if the body (or its JSON) contains a value the scrubber hid, so it cannot be sent as is."""
    return REDACTED.encode() in body


def is_local_target(url: str) -> bool:
    """Loopback, private networks, Docker's host alias and bare names like `api` (Docker services)."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if host in {"localhost", "host.docker.internal", "gateway.docker.internal"} or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_unspecified
    except ValueError:
        return "." not in host  # a bare hostname is only reachable inside a private network


def parse_header_args(values: list[str] | None) -> dict[str, str]:
    """['Authorization: Bearer x', 'X-Env: test'] -> {'authorization': 'Bearer x', 'x-env': 'test'}"""
    out: dict[str, str] = {}
    for raw in values or []:
        name, sep, value = raw.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"'{raw}' is not a header; use 'Name: value'")
        out[name.strip().lower()] = value.strip()
    return out


def require_allowed_target(url: str, allow_remote: bool) -> None:
    if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname:
        raise ValueError(f"'{url}' is not a valid target. Use something like http://localhost:8000")
    if not allow_remote and not is_local_target(url):
        raise ValueError(
            f"'{url}' is not a local address. Only test servers you own; if this one is yours, add --allow-remote."
        )

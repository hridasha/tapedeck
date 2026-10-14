"""Black-marker pass: hide secrets before a tape is saved.

Headers like Authorization / Cookie are always hidden. In JSON bodies, values of keys that look like secrets
(password, token, api key...) are hidden. Anything that is not JSON is left alone.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import Entry

REDACTED = "***REDACTED***"

REDACT_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
}

# password, secret, api key, token, access_token, refresh_token ... but NOT token_type or tokens_used
SECRET_KEY = re.compile(
    r"^(password|passwd|pwd|secret|client_?secret|api_?key|authorization|(access|refresh|id|auth|session|csrf|bearer)?_?token)$",
    re.IGNORECASE,
)


def scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: (REDACTED if k.lower() in REDACT_HEADERS else v) for k, v in headers.items()}


def scrub_json(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if SECRET_KEY.match(str(k)) and not isinstance(v, (dict, list)) and v is not None:
                out[k] = REDACTED
            else:
                out[k] = scrub_json(v)
        return out
    if isinstance(value, list):
        return [scrub_json(v) for v in value]
    return value


def scrub_body(body: bytes) -> bytes:
    if not body or body.lstrip()[:1] not in (b"{", b"["):
        return body
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    return json.dumps(scrub_json(data), separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def scrub_entry(entry: Entry) -> Entry:
    entry.req_headers = scrub_headers(entry.req_headers)
    entry.resp_headers = scrub_headers(entry.resp_headers)
    entry.req_body = scrub_body(entry.req_body)
    entry.resp_body = scrub_body(entry.resp_body)
    return entry

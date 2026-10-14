"""Which recording answers this request?

Every request gets three "fingerprints", from strictest to loosest:

  exact  GET /videos/123?page=1   + body
  shape  GET /videos/{id}?page=1  + body      (numbers, UUIDs and long hex strings count as ids)
  loose  GET /videos/{id}                     (query and body ignored)

The mock tries them in that order, so `/videos/456` still gets an answer when only `/videos/123` was recorded,
and the response header `x-tapedeck-match` tells you how sure it was. Values that change on every call
(`?_=1699999`, a `timestamp` field in the body) are ignored when making the fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl

from .models import Entry

LEVELS = ("exact", "shape", "loose")

ID_SEGMENT = re.compile(
    r"^(\d+"  # 123
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # uuid
    r"|[0-9a-fA-F]{16,})$"  # long hex (Mongo ids, hashes)
)
NOISY_QUERY = {"_", "_t", "t", "ts", "timestamp", "cb", "nocache", "cachebust", "cache_bust"}
NOISY_BODY_KEYS = {"timestamp", "ts", "nonce", "request_id", "requestid", "csrf", "_csrf", "client_time"}


def template_path(path: str) -> str:
    return "/".join("{id}" if ID_SEGMENT.match(seg) else seg for seg in path.split("/"))


def normalize_query(query: str) -> str:
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() not in NOISY_QUERY]
    return "&".join(f"{k}={v}" for k, v in sorted(pairs))


def _strip_noise(value):
    if isinstance(value, dict):
        return {k: _strip_noise(v) for k, v in value.items() if str(k).lower() not in NOISY_BODY_KEYS}
    if isinstance(value, list):
        return [_strip_noise(v) for v in value]
    return value


def body_fingerprint(body: bytes) -> str:
    if not body:
        return ""
    try:
        canonical = json.dumps(_strip_noise(json.loads(body)), sort_keys=True, separators=(",", ":")).encode()
    except (ValueError, UnicodeDecodeError):
        canonical = body
    return hashlib.sha1(canonical).hexdigest()[:16]


@dataclass(frozen=True)
class Keys:
    exact: str
    shape: str
    loose: str


def make_keys(method: str, path: str, query: str = "", body: bytes = b"") -> Keys:
    m, q, b = method.upper(), normalize_query(query), body_fingerprint(body)
    tpl = template_path(path)
    return Keys(exact=f"{m} {path}?{q} #{b}", shape=f"{m} {tpl}?{q} #{b}", loose=f"{m} {tpl}")


@dataclass
class Match:
    entry: Entry
    level: str  # exact | shape | loose


class Matcher:
    """Index a tape's entries once, then answer `find()` calls.

    If the same question was recorded several times with different answers (first an empty list, later three
    items) they are returned in order, and the last one is repeated once they run out.
    """

    def __init__(self, entries: list[Entry]):
        self._index: dict[str, dict[str, list[Entry]]] = {level: {} for level in LEVELS}
        self._served: dict[tuple[str, str], int] = {}
        for entry in entries:
            keys = make_keys(entry.method, entry.path, entry.query, entry.req_body)
            for level in LEVELS:
                self._index[level].setdefault(getattr(keys, level), []).append(entry)

    def reset(self) -> None:
        self._served.clear()

    def find(self, method: str, path: str, query: str = "", body: bytes = b"") -> Match | None:
        keys = make_keys(method, path, query, body)
        for level in LEVELS:
            key = getattr(keys, level)
            candidates = self._index[level].get(key)
            if candidates:
                n = self._served.get((level, key), 0)
                self._served[(level, key)] = n + 1
                return Match(candidates[min(n, len(candidates) - 1)], level)
        return None

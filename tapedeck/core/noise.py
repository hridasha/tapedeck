"""Find the values that change on every call (timestamps, random ids), so a replay does not fail because of them.

Two ways to spot them:
  * the same question was recorded twice and the answers differ in some JSON fields -> candidates
  * (optional) a value *looks* like a timestamp or an id even if it was only recorded once

The first way gives *candidates*, not facts: an answer can also change because real data changed (a new item
was added). So a person confirms them (`tapedeck noise NAME --save`). A replay on its own only forgives values
that look like timestamps or UUIDs, see replay/diff.py.

Paths look like `$.items[].created_at`: list positions are written `[]`, so one rule covers every item.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from .matcher import make_keys
from .models import Entry

ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
EPOCH = re.compile(r"^1[5-9]\d{8}(\d{3})?$")  # seconds or milliseconds since 1970 (2017-2033)

_INDEX = re.compile(r"\[\d+\]")


def normalize_path(path: str) -> str:
    """`$.items[3].price` -> `$.items[].price`"""
    return _INDEX.sub("[]", path)


def flatten(value, path: str = "$") -> dict[str, object]:
    """Every leaf of a JSON value, keyed by its path (list positions kept)."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in value.items():
            out.update(flatten(v, f"{path}.{k}"))
        return out or {path: {}}
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(flatten(v, f"{path}[{i}]"))
        return out or {path: []}
    return {path: value}


def looks_noisy(value: object) -> bool:
    if isinstance(value, str):
        return bool(ISO_DATETIME.match(value) or UUID.match(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(EPOCH.match(str(value)))
    return False


def _json(body: bytes):
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def differing_paths(a, b) -> set[str]:
    """Normalized paths where two JSON values differ (changed, added or removed)."""
    fa, fb = flatten(a), flatten(b)
    return {normalize_path(p) for p in fa.keys() | fb.keys() if fa.get(p, object()) != fb.get(p, object())}


def detect_noise(entries: list[Entry], *, by_pattern: bool = False) -> set[str]:
    """Noisy JSON paths for a tape (see module doc)."""
    groups: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        groups[make_keys(e.method, e.path, e.query, e.req_body).exact].append(e)

    noisy: set[str] = set()
    for group in groups.values():
        bodies = [_json(e.resp_body) for e in group]
        bodies = [b for b in bodies if b is not None]
        for earlier, later in zip(bodies, bodies[1:], strict=False):
            noisy |= differing_paths(earlier, later)

    if by_pattern:
        for e in entries:
            body = _json(e.resp_body)
            if body is not None:
                noisy |= {normalize_path(p) for p, v in flatten(body).items() if looks_noisy(v)}
    return noisy

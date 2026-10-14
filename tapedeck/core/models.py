from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entry:
    """One question and its answer, as seen on the wire."""

    method: str
    path: str  # path only, e.g. /items/5
    query: str = ""  # raw query string without the '?'
    req_headers: dict[str, str] = field(default_factory=dict)
    req_body: bytes = b""
    status: int = 200
    resp_headers: dict[str, str] = field(default_factory=dict)
    resp_body: bytes = b""
    duration_ms: float = 0.0  # how long the real server took
    started_at: str = ""
    order: int = 0  # position inside the tape
    id: int | None = None


@dataclass
class Tape:
    """A named recording: a list of entries plus where it came from."""

    id: int
    name: str
    created_at: str
    source: str  # "har" or "record"
    target_url: str = ""
    entry_count: int = 0

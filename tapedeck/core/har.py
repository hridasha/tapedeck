"""Import a HAR file (browser DevTools -> Network -> "Save all as HAR") as a list of entries."""

from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

from .models import Entry
from .scrub import scrub_entry

STATIC_EXT = {
    ".js",
    ".mjs",
    ".css",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".avif",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp4",
    ".webm",
    ".mp3",
}
STATIC_TYPES = {"stylesheet", "script", "image", "font", "media", "manifest", "other-static"}


def _headers(items: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in items or []:
        name = str(h.get("name", "")).lower()
        if not name or name.startswith(":"):  # HTTP/2 pseudo headers
            continue
        value = str(h.get("value", ""))
        out[name] = f"{out[name]}, {value}" if name in out else value
    return out


def _is_static(path: str, resource_type: str) -> bool:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return ext in STATIC_EXT or resource_type in STATIC_TYPES


def parse_har(source: str | bytes | dict, *, keep_static: bool = False, scrub: bool = True) -> list[Entry]:
    data = json.loads(source) if isinstance(source, (str, bytes)) else source
    try:
        har_entries = data["log"]["entries"]
    except (KeyError, TypeError) as exc:
        raise ValueError("this does not look like a HAR file (no log.entries)") from exc

    entries: list[Entry] = []
    for raw in har_entries:
        req, resp = raw.get("request", {}), raw.get("response", {})
        method = str(req.get("method", "GET")).upper()
        status = int(resp.get("status", 0) or 0)
        if method == "OPTIONS" or status == 0:  # CORS preflight, or a blocked / failed request
            continue

        url = urlsplit(req.get("url", ""))
        path = url.path or "/"
        if not keep_static and _is_static(path, str(raw.get("_resourceType", ""))):
            continue

        req_body = (req.get("postData") or {}).get("text") or ""
        content = resp.get("content") or {}
        text = content.get("text") or ""
        resp_body = base64.b64decode(text) if content.get("encoding") == "base64" else text.encode("utf-8")

        entry = Entry(
            method=method,
            path=path,
            query=url.query,
            req_headers=_headers(req.get("headers", [])),
            req_body=req_body.encode("utf-8") if isinstance(req_body, str) else bytes(req_body),
            status=status,
            resp_headers=_headers(resp.get("headers", [])),
            resp_body=resp_body,
            duration_ms=max(0.0, float(raw.get("time", 0) or 0)),
            started_at=str(raw.get("startedDateTime", "")),
            order=len(entries),
        )
        entries.append(scrub_entry(entry) if scrub else entry)
    return entries

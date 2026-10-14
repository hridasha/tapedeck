"""The fake server: answers requests from a tape instead of the real backend."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from tapedeck.core.matcher import Matcher
from tapedeck.core.models import Entry

# Headers that describe how the ORIGINAL bytes travelled, not what they contain
SKIP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-length",
    "content-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
MAX_RECORDED_DELAY_S = 10.0


def create_mock_app(
    entries: list[Entry],
    *,
    latency: str | float = 0,
    fail_rate: float = 0.0,
    cors: bool = True,
    seed: int | None = None,
) -> FastAPI:
    """latency: 0 (none), a number of milliseconds, or "recorded" (wait as long as the real server did)."""
    app = FastAPI(title="tapedeck mock", docs_url=None, redoc_url=None, openapi_url=None)
    matcher = Matcher(entries)
    rng = random.Random(seed)
    log: deque[dict] = deque(maxlen=1000)
    app.state.log = log  # so the web API can show it
    app.state.matcher = matcher

    if cors:  # a mock replaces a backend, so browsers must be allowed to call it
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=r".*",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-tapedeck-match", "x-tapedeck-entry"],
        )

    @app.get("/__tapedeck/health")
    async def health():
        return {"ok": True, "entries": len(entries)}

    @app.get("/__tapedeck/log")
    async def get_log():
        return list(log)

    @app.post("/__tapedeck/reset")
    async def reset():
        matcher.reset()
        log.clear()
        return {"ok": True}

    @app.api_route("/{full_path:path}", methods=METHODS)
    async def handle(request: Request):
        body = await request.body()
        path, query = request.url.path, request.url.query
        match = matcher.find(request.method, path, query, body)

        def note(level: str, status: int) -> None:
            log.append(
                {
                    "time": time.time(),
                    "method": request.method,
                    "path": path,
                    "query": query,
                    "match": level,
                    "status": status,
                }
            )

        if fail_rate and rng.random() < fail_rate:
            note("injected", 503)
            return JSONResponse({"error": "tapedeck injected failure"}, 503, headers={"x-tapedeck-match": "injected"})

        if match is None:
            note("none", 404)
            return JSONResponse(
                {
                    "error": "no recording matches this request",
                    "method": request.method,
                    "path": path,
                    "query": query,
                    "hint": "record this call (or import a HAR that contains it) to add it to the tape",
                },
                404,
                headers={"x-tapedeck-match": "none"},
            )

        if latency == "recorded":
            await asyncio.sleep(min(match.entry.duration_ms / 1000, MAX_RECORDED_DELAY_S))
        elif latency:
            await asyncio.sleep(float(latency) / 1000)

        skip = set(SKIP_RESPONSE_HEADERS)
        if cors:
            skip |= {h for h in match.entry.resp_headers if h.startswith("access-control-")}
        headers = {k: v for k, v in match.entry.resp_headers.items() if k.lower() not in skip}
        headers["x-tapedeck-match"] = match.level
        headers["x-tapedeck-entry"] = str(match.entry.id if match.entry.id is not None else match.entry.order)
        note(match.level, match.entry.status)
        return Response(content=match.entry.resp_body, status_code=match.entry.status, headers=headers)

    return app


def serve_mock(entries: list[Entry], host: str = "127.0.0.1", port: int = 9100, **options) -> None:
    import uvicorn

    uvicorn.run(create_mock_app(entries, **options), host=host, port=port, log_level="warning")

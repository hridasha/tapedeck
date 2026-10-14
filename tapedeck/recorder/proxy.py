"""The recording proxy: sits in front of a real server, forwards everything, and writes it down.

    your app  ->  tapedeck record (:9000)  ->  real API (:8000)

Version 1 buffers each response (so it can store it). Streaming responses (server-sent events) and very large
ones are passed straight through and NOT recorded; they are counted in `/__tapedeck/status`.
"""

from __future__ import annotations

import datetime as dt
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from tapedeck.core.http import HOP_BY_HOP
from tapedeck.core.models import Entry
from tapedeck.core.scrub import scrub_entry
from tapedeck.core.storage import Storage

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
STREAMING_TYPES = ("text/event-stream", "multipart/x-mixed-replace")
# Describe how bytes travelled to us, not what they contain. httpx already decoded the body.
DROP_FROM_RESPONSE = HOP_BY_HOP | {"content-length", "content-encoding"}


async def _decoded_chunks(upstream: httpx.Response):
    """The answer's bytes as they arrive, already decoded (we dropped the Content-Encoding header)."""
    if upstream.is_stream_consumed:  # already buffered (in-process test transports do this)
        yield upstream.content
    else:
        async for chunk in upstream.aiter_bytes():
            yield chunk


def create_recorder_app(
    store: Storage,
    tape_id: int,
    target: str,
    *,
    scrub: bool = True,
    max_body_bytes: int = 5 * 1024 * 1024,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 60.0,
) -> FastAPI:
    app = FastAPI(title="tapedeck recorder", docs_url=None, redoc_url=None, openapi_url=None)
    target = target.rstrip("/")
    client = httpx.AsyncClient(base_url=target, transport=transport, timeout=timeout, follow_redirects=False)
    state = {
        "recorded": 0,
        "skipped_streaming": 0,
        "skipped_large": 0,
        "errors": 0,
        "next_order": store.next_order(tape_id),
    }
    app.state.stats = state
    app.state.tape_id = tape_id

    @app.on_event("shutdown")
    async def _close() -> None:
        await client.aclose()

    @app.get("/__tapedeck/status")
    async def status():
        return {"target": target, "tape_id": tape_id, **state}

    @app.api_route("/{full_path:path}", methods=METHODS)
    async def forward(request: Request):
        body = await request.body()
        path, query = request.url.path, request.url.query
        # Forward as received, minus hop-by-hop headers; ask for an uncompressed answer so we can store it.
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
        }
        started_at = dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")
        t0 = time.perf_counter()
        try:
            upstream_request = client.build_request(
                request.method, f"{path}?{query}" if query else path, headers=fwd_headers, content=body
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            state["errors"] += 1
            return JSONResponse(
                {"error": "tapedeck could not reach the target", "target": target, "detail": str(exc)}, 502
            )

        content_type = upstream.headers.get("content-type", "").lower()
        declared = int(upstream.headers.get("content-length", "0") or 0)
        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in DROP_FROM_RESPONSE}

        if content_type.startswith(STREAMING_TYPES) or declared > max_body_bytes:
            state["skipped_streaming" if content_type.startswith(STREAMING_TYPES) else "skipped_large"] += 1
            return StreamingResponse(
                _decoded_chunks(upstream),
                status_code=upstream.status_code,
                headers=resp_headers,
                background=BackgroundTask(upstream.aclose),
            )

        content = await upstream.aread()
        await upstream.aclose()
        duration_ms = (time.perf_counter() - t0) * 1000

        if request.method != "OPTIONS":  # CORS preflights are the browser's business, not part of the tape
            entry = Entry(
                method=request.method,
                path=path,
                query=query,
                req_headers={k.lower(): v for k, v in request.headers.items()},
                req_body=body,
                status=upstream.status_code,
                resp_headers={k.lower(): v for k, v in resp_headers.items()},
                resp_body=content,
                duration_ms=duration_ms,
                started_at=started_at,
                order=state["next_order"],
            )
            state["next_order"] += 1
            store.add_entries(tape_id, [scrub_entry(entry) if scrub else entry])
            state["recorded"] += 1

        return Response(content=content, status_code=upstream.status_code, headers=resp_headers)

    return app


def serve_recorder(
    store: Storage, tape_id: int, target: str, host: str = "127.0.0.1", port: int = 9000, **options
) -> None:
    import uvicorn

    uvicorn.run(create_recorder_app(store, tape_id, target, **options), host=host, port=port, log_level="warning")

"""Record real calls against an in-process app, the way the recorder would, without starting a server."""

import asyncio
import time

import httpx

from tapedeck.core.models import Entry
from tapedeck.core.scrub import scrub_entry


def record_calls(app, calls, *, scrub=True) -> list[Entry]:
    """calls: [("GET", "/items?page=1"), ("POST", "/items", {"name": "x", "price": 1})]"""

    async def run():
        entries = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for order, call in enumerate(calls):
                method, target, *rest = call
                body = rest[0] if rest else None
                t0 = time.perf_counter()
                resp = await client.request(method, target, json=body)
                path, _, query = target.partition("?")
                entry = Entry(
                    method=method,
                    path=path,
                    query=query,
                    req_headers={"content-type": "application/json"} if body is not None else {},
                    req_body=resp.request.content,
                    status=resp.status_code,
                    resp_headers={k.lower(): v for k, v in resp.headers.items()},
                    resp_body=resp.content,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    order=order,
                )
                entries.append(scrub_entry(entry) if scrub else entry)
        return entries

    return asyncio.run(run())

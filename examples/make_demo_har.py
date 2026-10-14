"""Make a HAR file by calling the sample API for real, the way a browser session would look.

uvicorn examples.sample_api.app:app --port 8000        # in one terminal
python examples/make_demo_har.py                        # writes demo.har
tapedeck import-har demo.har --name demo
"""

import json
import sys
from datetime import UTC, datetime

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "demo.har"

CALLS = [
    ("POST", "/login", {"email": "demo@example.com", "password": "hunter2"}),
    ("GET", "/items?page=1&_=1699990001", None),
    ("GET", "/items/1", None),
    ("GET", "/items/2", None),
    ("POST", "/items", {"name": "monitor", "price": 129.0}),
    ("GET", "/items?page=1&_=1699990002", None),
    ("GET", "/time", None),
    ("GET", "/slow?ms=250", None),
]


def to_har_entry(client: httpx.Client, method: str, path: str, body: dict | None) -> dict:
    started = datetime.now(UTC).isoformat()
    resp = client.request(method, path, json=body, headers={"Authorization": "Bearer demo-token"})
    request = {
        "method": method,
        "url": f"{BASE}{path}",
        "headers": [{"name": k, "value": v} for k, v in resp.request.headers.items()],
    }
    if body is not None:
        request["postData"] = {"mimeType": "application/json", "text": json.dumps(body)}
    return {
        "startedDateTime": started,
        "time": resp.elapsed.total_seconds() * 1000,
        "_resourceType": "fetch",
        "request": request,
        "response": {
            "status": resp.status_code,
            "headers": [{"name": k, "value": v} for k, v in resp.headers.items()],
            "content": {"mimeType": resp.headers.get("content-type", ""), "text": resp.text},
        },
    }


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        entries = [to_har_entry(client, *call) for call in CALLS]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"log": {"version": "1.2", "creator": {"name": "make_demo_har"}, "entries": entries}}, f, indent=1)
    print(f"wrote {len(entries)} calls to {OUT}")


if __name__ == "__main__":
    main()

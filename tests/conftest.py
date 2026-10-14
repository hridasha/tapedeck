import base64
import json

import pytest


def har_entry(
    method,
    url,
    *,
    status=200,
    req_body=None,
    resp_json=None,
    resp_text=None,
    resp_b64=None,
    req_headers=None,
    resp_headers=None,
    time=12.0,
    resource_type="xhr",
):
    """One entry the way Chrome DevTools writes it."""
    content = {"mimeType": "application/json"}
    if resp_json is not None:
        content["text"] = json.dumps(resp_json)
    elif resp_b64 is not None:
        content.update(text=base64.b64encode(resp_b64).decode(), encoding="base64")
    elif resp_text is not None:
        content["text"] = resp_text
    request = {
        "method": method,
        "url": url,
        "headers": [{"name": k, "value": v} for k, v in (req_headers or {}).items()],
    }
    if req_body is not None:
        request["postData"] = {"mimeType": "application/json", "text": json.dumps(req_body)}
    return {
        "startedDateTime": "2025-01-01T10:00:00.000Z",
        "time": time,
        "_resourceType": resource_type,
        "request": request,
        "response": {
            "status": status,
            "headers": [{"name": k, "value": v} for k, v in (resp_headers or {}).items()],
            "content": content,
        },
    }


@pytest.fixture
def sample_har():
    return {
        "log": {
            "entries": [
                har_entry(
                    "POST",
                    "http://localhost:5001/login",
                    req_body={"email": "a@b.c", "password": "hunter2"},
                    resp_json={"access_token": "abc.def", "token_type": "bearer"},
                    req_headers={"Authorization": "Bearer xyz", "Content-Type": "application/json"},
                    resp_headers={
                        "Set-Cookie": "sid=1",
                        "Content-Type": "application/json",
                        "Content-Encoding": "gzip",
                    },
                ),
                har_entry(
                    "GET",
                    "http://localhost:5001/items?page=1&_=1699999",
                    resp_json={"items": [{"id": 1}], "page": 1},
                    time=40,
                ),
                har_entry("GET", "http://localhost:5001/items/1", resp_json={"id": 1, "name": "keyboard"}),
                har_entry("GET", "http://localhost:5001/items/1", resp_json={"id": 1, "name": "keyboard v2"}),
                har_entry("GET", "http://localhost:5001/logo.png", resp_b64=b"\x89PNG", resource_type="image"),
                har_entry("GET", "http://localhost:5001/assets/app.js", resp_text="x", resource_type="script"),
                har_entry("OPTIONS", "http://localhost:5001/items", status=204),
                har_entry("GET", "http://localhost:5001/blocked", status=0),
            ]
        }
    }


# --------------------------------------------------------------------------- shared test helpers
import copy  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import uvicorn  # noqa: E402


@pytest.fixture(autouse=True)
def reset_sample_api():
    """The sample API keeps its items in memory; every test starts from the same two items."""
    from examples.sample_api import app as module

    if not hasattr(module, "_DEFAULT_ITEMS"):
        module._DEFAULT_ITEMS = copy.deepcopy(module.ITEMS)
    module.ITEMS.clear()
    module.ITEMS.update(copy.deepcopy(module._DEFAULT_ITEMS))
    yield


@pytest.fixture
def serve():
    """serve(app) -> 'http://127.0.0.1:PORT' for a real server running in a background thread."""
    running = []

    def start(app) -> str:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        running.append((server, thread))
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=5)

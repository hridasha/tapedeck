"""Fake servers and recorders started from the web page.

Each one is a small uvicorn server running as a task inside the API's own event loop, so it can be started and
stopped without launching new processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI

from tapedeck.core.storage import Storage
from tapedeck.mock.server import create_mock_app
from tapedeck.recorder.proxy import create_recorder_app


class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server that leaves Ctrl+C / signals to the program that started it."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:  # older uvicorn versions
        pass


@dataclass
class ServerHandle:
    id: str
    kind: str  # mock | record
    tape: str
    port: int
    host: str
    app: FastAPI
    server: _EmbeddedServer
    task: asyncio.Task
    started_at: float = field(default_factory=time.time)
    options: dict = field(default_factory=dict)
    store: Storage | None = None  # the recorder keeps its own connection open while it runs

    def info(self) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "tape": self.tape,
            "port": self.port,
            "url": f"http://{'localhost' if self.host in ('0.0.0.0', '127.0.0.1') else self.host}:{self.port}",
            "started_at": self.started_at,
            "options": self.options,
            "running": not self.task.done(),
        }
        if self.kind == "record":
            data["stats"] = dict(self.app.state.stats)
        else:
            data["requests_served"] = len(self.app.state.log)
        return data


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        return s.connect_ex((host, port)) != 0


def default_host() -> str:
    """Fake servers listen on this computer only, unless TAPEDECK_SERVER_HOST says otherwise (Docker sets 0.0.0.0)."""
    return os.getenv("TAPEDECK_SERVER_HOST", "127.0.0.1")


class ServerManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.servers: dict[str, ServerHandle] = {}

    async def _launch(self, app: FastAPI, host: str, port: int) -> tuple[_EmbeddedServer, asyncio.Task]:
        if not port_is_free(port, host):
            raise ValueError(f"port {port} is already in use. Pick another one.")
        server = _EmbeddedServer(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        task = asyncio.create_task(server.serve())
        for _ in range(100):
            if server.started:
                return server, task
            if task.done():
                break
            await asyncio.sleep(0.05)
        server.should_exit = True
        raise ValueError(f"could not start a server on port {port}")

    async def start_mock(
        self, tape: str, port: int, *, latency: str | float = 0, fail_rate: float = 0.0, host: str | None = None
    ) -> ServerHandle:
        host = host or default_host()
        with Storage(self.db_path) as store:
            entries = store.get_entries(store.get_tape(tape).id)
        app = create_mock_app(entries, latency=latency, fail_rate=fail_rate)
        server, task = await self._launch(app, host, port)
        handle = ServerHandle(
            id=f"mock:{port}", kind="mock", tape=tape, port=port, host=host, app=app, server=server, task=task,
            options={"latency": latency, "fail_rate": fail_rate},
        )  # fmt: skip
        self.servers[handle.id] = handle
        return handle

    async def start_recorder(
        self,
        tape: str,
        target: str,
        port: int,
        *,
        append: bool = False,
        scrub: bool = True,
        host: str | None = None,
    ) -> ServerHandle:
        host = host or default_host()
        store = Storage(self.db_path)
        try:
            from tapedeck.core.storage import TapeExistsError

            try:
                record = store.create_tape(tape, source="record", target_url=target)
            except TapeExistsError:
                if not append:
                    raise
                record = store.get_tape(tape)
            app = create_recorder_app(store, record.id, target, scrub=scrub)
            server, task = await self._launch(app, host, port)
        except Exception:
            store.close()
            raise
        handle = ServerHandle(
            id=f"record:{port}", kind="record", tape=tape, port=port, host=host, app=app, server=server, task=task,
            options={"target": target, "scrub": scrub}, store=store,
        )  # fmt: skip
        self.servers[handle.id] = handle
        return handle

    async def stop(self, server_id: str) -> None:
        handle = self.servers.pop(server_id, None)
        if handle is None:
            raise KeyError(server_id)
        handle.server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(handle.task, timeout=5)
        if handle.store:
            handle.store.close()

    async def stop_all(self) -> None:
        for server_id in list(self.servers):
            with contextlib.suppress(Exception):
                await self.stop(server_id)

    def list(self) -> list[dict]:
        for server_id, handle in list(self.servers.items()):
            if handle.task.done():  # it crashed or was stopped from outside
                handle.store and handle.store.close()
                del self.servers[server_id]
        return [h.info() for h in self.servers.values()]

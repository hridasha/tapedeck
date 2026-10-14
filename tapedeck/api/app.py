"""The web API behind the Tapedeck page: tapes, fake servers, recorders, replays and load tests."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tapedeck import __version__
from tapedeck.core.har import parse_har
from tapedeck.core.http import require_allowed_target
from tapedeck.core.noise import detect_noise
from tapedeck.core.storage import Storage, TapeExistsError, TapeNotFoundError
from tapedeck.load import report as load_report
from tapedeck.replay import report as replay_report

from .jobs import JobManager
from .servers import ServerManager

MAX_TEXT = 200_000  # characters of a body sent to the page
MAX_BINARY = 64_000


def find_web_dir() -> Path | None:
    candidates = [Path(os.environ["TAPEDECK_WEB_DIR"])] if os.getenv("TAPEDECK_WEB_DIR") else []
    here = Path(__file__).resolve()
    candidates += [here.parent.parent / "web_dist", here.parents[2] / "web" / "dist"]
    return next((c for c in candidates if (c / "index.html").is_file()), None)


# ---------------------------------------------------------------------------------------------- bodies
class MockRequest(BaseModel):
    tape: str
    port: int = Field(9100, ge=1, le=65535)
    latency: str | float = 0
    fail_rate: float = Field(0.0, ge=0.0, le=1.0)


class RecordRequest(BaseModel):
    tape: str
    target: str
    port: int = Field(9000, ge=1, le=65535)
    append: bool = False
    scrub: bool = True


class ReplayRequest(BaseModel):
    tape: str
    target: str
    headers: dict[str, str] = {}
    ignore: list[str] = []
    allow_writes: bool = False
    allow_remote: bool = False
    learn_noise: bool = False
    fail_on_slow: bool = False


class LoadRequest(BaseModel):
    tape: str
    target: str
    users: int = Field(10, ge=1, le=2000)
    rate: float | None = Field(None, gt=0, le=10000)
    duration: float = Field(30.0, gt=0, le=3600)
    ramp: float = Field(0.0, ge=0)
    warmup: float = Field(0.0, ge=0)
    think_ms: float = Field(0.0, ge=0)
    headers: dict[str, str] = {}
    allow_writes: bool = False
    allow_remote: bool = False


class NoiseRequest(BaseModel):
    paths: list[str]


def _latency(value: str | float) -> str | float:
    if value == "recorded":
        return "recorded"
    try:
        return float(str(value).lower().removesuffix("ms"))
    except ValueError:
        raise HTTPException(400, "latency must be 'recorded' or a number of milliseconds") from None


def body_view(body: bytes) -> dict:
    """A body as the page shows it: text (JSON pretty-printed) or, for binary data, a small base64 sample."""
    if not body:
        return {"size": 0, "binary": False, "text": "", "is_json": False, "truncated": False}
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "size": len(body), "binary": True, "text": None, "is_json": False, "truncated": len(body) > MAX_BINARY,
            "base64": base64.b64encode(body[:MAX_BINARY]).decode(),
        }  # fmt: skip
    is_json = False
    if text.lstrip()[:1] in ("{", "["):
        try:
            text, is_json = json.dumps(json.loads(text), indent=2, ensure_ascii=False), True
        except ValueError:
            pass
    return {
        "size": len(body),
        "binary": False,
        "text": text[:MAX_TEXT],
        "is_json": is_json,
        "truncated": len(text) > MAX_TEXT,
    }


# ------------------------------------------------------------------------------------------------- app
def create_api_app(db_path: str | Path = "tapedeck.db", web_dir: Path | None = None) -> FastAPI:
    db = str(db_path)
    app = FastAPI(title="Tapedeck", version=__version__)
    servers, jobs = ServerManager(db), JobManager(db)
    app.state.servers, app.state.jobs, app.state.db = servers, jobs, db

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # the Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def store() -> Iterator[Storage]:
        s = Storage(db)
        try:
            yield s
        finally:
            s.close()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await jobs.cancel_all()
        await servers.stop_all()

    def tape_or_404(s: Storage, name: str):
        try:
            return s.get_tape(name)
        except TapeNotFoundError:
            raise HTTPException(404, f"no tape called '{name}'") from None

    def tape_json(t, s: Storage) -> dict:
        return {
            "name": t.name, "created_at": t.created_at, "source": t.source, "target_url": t.target_url,
            "entry_count": t.entry_count, "noise": s.get_noise(t.name),
        }  # fmt: skip

    # ------------------------------------------------------------------------------------------ tapes
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "db": db}

    @app.get("/api/tapes")
    def list_tapes(s: Storage = Depends(store)):
        return [tape_json(t, s) for t in s.list_tapes()]

    @app.post("/api/tapes/import-har", status_code=201)
    async def import_har(
        file: UploadFile = File(...),
        name: str | None = Form(None),
        keep_static: bool = Form(False),
        scrub: bool = Form(True),
        s: Storage = Depends(store),
    ):
        try:
            entries = parse_har(await file.read(), keep_static=keep_static, scrub=scrub)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not entries:
            raise HTTPException(400, "that HAR file has no API calls to import (try keeping static files)")
        tape_name = (name or Path(file.filename or "tape").stem).strip() or "tape"
        try:
            tape = s.create_tape(tape_name, source="har")
        except TapeExistsError:
            raise HTTPException(409, f"a tape called '{tape_name}' already exists") from None
        s.add_entries(tape.id, entries)
        return tape_json(s.get_tape(tape_name), s)

    @app.delete("/api/tapes/{name}", status_code=204)
    def delete_tape(name: str, s: Storage = Depends(store)):
        tape_or_404(s, name)
        s.delete_tape(name)

    @app.get("/api/tapes/{name}/entries")
    def entries(name: str, s: Storage = Depends(store)):
        tape = tape_or_404(s, name)
        return [
            {
                "id": e.id, "order": e.order, "method": e.method, "path": e.path, "query": e.query, "status": e.status,
                "duration_ms": round(e.duration_ms, 1), "size": len(e.resp_body),
                "content_type": e.resp_headers.get("content-type", ""),
            }
            for e in s.get_entries(tape.id)
        ]  # fmt: skip

    @app.get("/api/tapes/{name}/entries/{entry_id}")
    def entry_detail(name: str, entry_id: int, s: Storage = Depends(store)):
        tape = tape_or_404(s, name)
        e = s.get_entry(tape.id, entry_id)
        if e is None:
            raise HTTPException(404, "no such request in this tape")
        return {
            "id": e.id, "order": e.order, "method": e.method, "path": e.path, "query": e.query, "status": e.status,
            "duration_ms": round(e.duration_ms, 1), "started_at": e.started_at,
            "request": {"headers": e.req_headers, "body": body_view(e.req_body)},
            "response": {"headers": e.resp_headers, "body": body_view(e.resp_body)},
        }  # fmt: skip

    @app.get("/api/tapes/{name}/noise")
    def get_noise(name: str, patterns: bool = False, s: Storage = Depends(store)):
        tape = tape_or_404(s, name)
        return {
            "saved": s.get_noise(name),
            "candidates": sorted(detect_noise(s.get_entries(tape.id), by_pattern=patterns)),
        }

    @app.put("/api/tapes/{name}/noise")
    def put_noise(name: str, body: NoiseRequest, s: Storage = Depends(store)):
        tape_or_404(s, name)
        s.set_noise(name, body.paths)
        return {"saved": s.get_noise(name)}

    # --------------------------------------------------------------------------------------- servers
    @app.get("/api/servers")
    def list_servers():
        return servers.list()

    @app.post("/api/servers/mock", status_code=201)
    async def start_mock(body: MockRequest):
        try:
            handle = await servers.start_mock(
                body.tape, body.port, latency=_latency(body.latency), fail_rate=body.fail_rate
            )
        except TapeNotFoundError:
            raise HTTPException(404, f"no tape called '{body.tape}'") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return handle.info()

    @app.post("/api/servers/record", status_code=201)
    async def start_record(body: RecordRequest):
        try:
            handle = await servers.start_recorder(
                body.tape, body.target, body.port, append=body.append, scrub=body.scrub
            )
        except TapeExistsError:
            raise HTTPException(
                409, f"a tape called '{body.tape}' already exists (tick 'append' to add to it)"
            ) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return handle.info()

    @app.get("/api/servers/{server_id}/log")
    def server_log(server_id: str):
        handle = servers.servers.get(server_id)
        if handle is None:
            raise HTTPException(404, "no such server")
        return list(handle.app.state.log) if handle.kind == "mock" else dict(handle.app.state.stats)

    @app.delete("/api/servers/{server_id}", status_code=204)
    async def stop_server(server_id: str):
        try:
            await servers.stop(server_id)
        except KeyError:
            raise HTTPException(404, "no such server") from None

    # -------------------------------------------------------------------------------------------- jobs
    def start_job(kind: str, body):
        try:
            require_allowed_target(body.target, body.allow_remote)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        starter = jobs.start_replay if kind == "replay" else jobs.start_load
        try:
            job = starter(body.tape, body.target, **body.model_dump(exclude={"tape", "target"}))
        except TapeNotFoundError:
            raise HTTPException(404, f"no tape called '{body.tape}'") from None
        return job.snapshot()

    # async on purpose: starting a job creates a background task, which needs the running event loop
    @app.post("/api/replays", status_code=201)
    async def start_replay(body: ReplayRequest):
        return start_job("replay", body)

    @app.post("/api/loads", status_code=201)
    async def start_load(body: LoadRequest):
        return start_job("load", body)

    @app.get("/api/jobs")
    def list_jobs():
        return jobs.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            return jobs.get(job_id).snapshot()
        except KeyError:
            raise HTTPException(404, "no such job") from None

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def cancel_job(job_id: str):
        try:
            jobs.cancel(job_id)
        except KeyError:
            raise HTTPException(404, "no such job") from None

    @app.get("/api/jobs/{job_id}/report.html", response_class=HTMLResponse)
    def job_report(job_id: str):
        try:
            job = jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, "no such job") from None
        if job.result_obj is None:
            raise HTTPException(404, "this job has no result yet")
        module = replay_report if job.kind == "replay" else load_report
        return module.render_html(job.result_obj, job.tape)

    @app.websocket("/ws/jobs/{job_id}")
    async def job_socket(ws: WebSocket, job_id: str):
        await ws.accept()
        job = jobs.jobs.get(job_id)
        if job is None:
            await ws.close(code=4404)
            return
        last = None
        try:
            while True:
                payload = json.dumps(job.snapshot())
                if payload != last:
                    await ws.send_text(payload)
                    last = payload
                if job.status != "running":
                    break
                await asyncio.sleep(0.4)
            await ws.close()
        except WebSocketDisconnect:
            return

    # ------------------------------------------------------------------------------------ the page
    web = web_dir or find_web_dir()
    app.state.web_dir = web
    if web:
        if (web / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "ws/")):
                raise HTTPException(404)
            target = (web / path).resolve()
            if path and target.is_file() and web.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(web / "index.html")

    return app

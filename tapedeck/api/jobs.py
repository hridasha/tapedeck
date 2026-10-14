"""Replays and load tests started from the web page. They run in the background and report their progress."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from tapedeck.core.storage import Storage
from tapedeck.load import report as load_report
from tapedeck.load.runner import LoadOptions, run_load
from tapedeck.replay import report as replay_report
from tapedeck.replay.runner import ReplayOptions, replay


@dataclass
class Job:
    id: str
    kind: str  # replay | load
    tape: str
    target: str
    status: str = "running"  # running | done | failed | cancelled
    started: float = field(default_factory=time.time)
    finished: float | None = None
    progress: dict = field(default_factory=dict)
    ticks: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    task: asyncio.Task | None = None
    result_obj: object | None = None  # kept for the HTML report

    def snapshot(self, *, full: bool = True) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "tape": self.tape,
            "target": self.target,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "progress": self.progress,
            "error": self.error,
        }
        if full:
            data["ticks"] = self.ticks
            data["result"] = self.result
        return data


class JobManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.jobs: dict[str, Job] = {}

    def _load(self, tape: str):
        with Storage(self.db_path) as store:
            record = store.get_tape(tape)  # raises TapeNotFoundError
            return store.get_entries(record.id), set(store.get_noise(tape))

    def _register(self, job: Job, coro) -> Job:
        job.task = asyncio.create_task(coro)
        self.jobs[job.id] = job
        return job

    async def _finish(self, job: Job, runner, build) -> None:
        try:
            result = await runner()
            job.result_obj = result
            job.result = build(result)
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except ValueError as exc:  # a refused target, nothing to send...
            job.status, job.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - a job must never take the server down
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    def start_replay(self, tape: str, target: str, **options) -> Job:
        entries, saved_noise = self._load(tape)
        job = Job(id=uuid.uuid4().hex[:10], kind="replay", tape=tape, target=target)
        job.progress = {"done": 0, "total": len(entries)}

        def on_result(_result, total: int) -> None:
            job.progress = {"done": job.progress["done"] + 1, "total": total}

        opts = ReplayOptions(
            target=target, ignore=saved_noise | set(options.pop("ignore", None) or []), on_result=on_result, **options
        )
        return self._register(
            job,
            self._finish(job, lambda: replay(entries, opts), lambda r: replay_report.to_dict(r, tape)),
        )

    def start_load(self, tape: str, target: str, **options) -> Job:
        entries, _ = self._load(tape)
        job = Job(id=uuid.uuid4().hex[:10], kind="load", tape=tape, target=target)
        duration = float(options.get("duration", 30))
        job.progress = {"elapsed": 0, "duration": duration}

        def on_tick(tick) -> None:
            job.ticks.append(
                {
                    "t": tick.t, "requests": tick.requests, "errors": tick.errors, "p50": tick.p50, "p95": tick.p95,
                    "p99": tick.p99, "users": tick.users,
                }
            )  # fmt: skip
            job.progress = {"elapsed": tick.t + 1, "duration": duration}

        opts = LoadOptions(target=target, on_tick=on_tick, **options)
        return self._register(
            job,
            self._finish(job, lambda: run_load(entries, opts), lambda r: load_report.to_dict(r, tape)),
        )

    def get(self, job_id: str) -> Job:
        return self.jobs[job_id]

    def list(self) -> list[dict]:
        return [j.snapshot(full=False) for j in sorted(self.jobs.values(), key=lambda j: -j.started)]

    def cancel(self, job_id: str) -> None:
        job = self.jobs[job_id]
        if job.task and not job.task.done():
            job.task.cancel()

    async def cancel_all(self) -> None:
        for job in self.jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()
        await asyncio.gather(*(j.task for j in self.jobs.values() if j.task), return_exceptions=True)

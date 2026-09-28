"""Minimal in-process job runner: ffmpeg work must not block the request loop."""
from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Job:
    id: str
    kind: str
    state: str = "running"          # running | done | error | cancelled
    stage: str = ""
    progress: float = 0.0
    result: Any = None
    error: Optional[str] = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "stage": self.stage,
            "progress": round(self.progress, 4),
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    def __init__(self, keep: int = 60) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._keep:
                self._jobs.pop(self._order.pop(0), None)

        def runner() -> None:
            try:
                job.result = fn(job)
                job.state = "cancelled" if job.cancelled() else "done"
                job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
                job.state = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()

        threading.Thread(target=runner, name=f"job-{kind}-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.state == "running":
            job._cancel.set()
            return True
        return False


registry = JobRegistry()

"""In-memory audit jobs: one audit runs at a time in a background asyncio task."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from seo_audit.audit import run_audit
from seo_audit.config import Config
from seo_audit.fetcher import make_client
from seo_audit.llm import LLMClient
from seo_audit.schema import AuditReport, Mode

MAX_RETAINED_JOBS = 20

HttpFactory = Callable[[float], httpx.AsyncClient]
LLMFactory = Callable[[str, float], LLMClient]


class JobBusy(Exception):
    """Raised when an audit is requested while another one is queued or running."""


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATES = (JobState.QUEUED, JobState.RUNNING)


@dataclass
class Job:
    url: str
    mode: Mode
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: JobState = JobState.QUEUED
    progress: list[str] = field(default_factory=list)
    report: AuditReport | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def finish(self, state: JobState) -> None:
        self.state = state
        self.finished = time.time()


class JobManager:
    def __init__(
        self,
        http_factory: HttpFactory = make_client,
        llm_factory: LLMFactory = LLMClient,
    ) -> None:
        self._http_factory = http_factory
        self._llm_factory = llm_factory
        self._jobs: dict[str, Job] = {}  # insertion order = creation order

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def start(self, url: str, mode: Mode, config: Config) -> Job:
        if any(j.active for j in self._jobs.values()):
            raise JobBusy("An audit is already running")
        job = Job(url=url, mode=mode)
        self._jobs[job.id] = job
        self._evict()
        job.task = asyncio.create_task(self._run(job, config))
        return job

    def cancel(self, job_id: str) -> Job | None:
        """Cancel an active job; returns the job (unchanged if it already finished) or None if unknown."""
        job = self._jobs.get(job_id)
        if job is not None and job.active:
            job.finish(JobState.CANCELLED)
            if job.task is not None:
                job.task.cancel()
        return job

    def _evict(self) -> None:
        for job_id in [i for i, j in self._jobs.items() if not j.active][: max(0, len(self._jobs) - MAX_RETAINED_JOBS)]:
            del self._jobs[job_id]

    async def _run(self, job: Job, config: Config) -> None:
        job.state = JobState.RUNNING
        try:
            async with self._http_factory(config.crawl.request_timeout) as client:
                llm = self._llm_factory(config.model, config.temperature)
                report = await run_audit(job.url, job.mode, config, client, llm, job.progress.append)
        except asyncio.CancelledError:
            if job.state != JobState.CANCELLED:
                job.finish(JobState.CANCELLED)
            raise
        except Exception as exc:
            job.error = str(exc) or type(exc).__name__
            job.finish(JobState.FAILED)
            return
        job.report = report
        # Even a total failure (see `is_total_failure`) is "done": the report carries the errors for the UI.
        job.finish(JobState.DONE)

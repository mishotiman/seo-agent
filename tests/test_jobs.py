import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from seo_audit.config import Config
from seo_audit.fetcher import make_client
from seo_audit.llm import LLMClient
from seo_audit.schema import Mode
from seo_audit.web.jobs import MAX_RETAINED_JOBS, JobBusy, JobManager, JobState

PAGE = "<html><head><title>Widgets and more widgets for sale</title></head><body><h1>Widgets</h1><p>Hello</p></body></html>"


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/slow":
        raise AssertionError("slow handler must not be called")
    return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})


def http_factory(timeout: float) -> httpx.AsyncClient:
    return make_client(timeout, transport=httpx.MockTransport(handler))


def completion(**kwargs: Any) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"findings": []})))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def llm_factory(model: str, temperature: float) -> LLMClient:
    return LLMClient(model, temperature, completion=completion)


def manager() -> JobManager:
    return JobManager(http_factory, llm_factory)


CONFIG = Config(model="test/model")


def run(coro: Callable[[], Any]) -> Any:
    return asyncio.run(coro())


def test_success_records_progress_and_report() -> None:
    async def scenario() -> None:
        jobs = manager()
        job = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
        assert job.state == JobState.QUEUED
        assert jobs.get(job.id) is job
        assert job.task is not None
        await job.task
        assert job.state == JobState.DONE
        assert job.report is not None and job.report.pages[0].status_code == 200
        assert job.error is None and job.finished is not None
        assert job.progress[0] == "Fetching https://example.com/"
        assert "Running model checks" in job.progress

    run(scenario)


def test_total_failure_is_done_with_report() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async def scenario() -> None:
        jobs = JobManager(lambda t: make_client(t, transport=httpx.MockTransport(refuse)), llm_factory)
        job = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
        assert job.task is not None
        await job.task
        assert job.state == JobState.DONE
        assert job.report is not None and job.report.errors

    run(scenario)


def test_busy_rejection_then_ok_after_finish() -> None:
    async def scenario() -> None:
        jobs = manager()
        first = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
        with pytest.raises(JobBusy, match="already running"):
            jobs.start("https://example.com/other", Mode.PAGE, CONFIG)
        assert first.task is not None
        await first.task
        second = jobs.start("https://example.com/other", Mode.PAGE, CONFIG)
        assert second.task is not None
        await second.task

    run(scenario)


def test_cancel_running_job() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def slow(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.sleep(60)
            return httpx.Response(200)

        jobs = JobManager(lambda t: make_client(t, transport=httpx.MockTransport(slow)), llm_factory)
        job = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
        await started.wait()
        assert job.state == JobState.RUNNING
        assert jobs.cancel(job.id) is job
        assert job.state == JobState.CANCELLED
        assert job.task is not None
        with pytest.raises(asyncio.CancelledError):
            await job.task
        assert job.state == JobState.CANCELLED and job.report is None
        # Cancelling a finished job leaves it unchanged; unknown ids return None.
        assert jobs.cancel(job.id) is job and job.state == JobState.CANCELLED
        assert jobs.cancel("missing") is None

    run(scenario)


def test_cancel_before_task_starts() -> None:
    async def scenario() -> None:
        jobs = manager()
        job = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
        jobs.cancel(job.id)
        assert job.state == JobState.CANCELLED
        await asyncio.gather(job.task, return_exceptions=True)  # type: ignore[type-var]
        assert job.state == JobState.CANCELLED and job.report is None

    run(scenario)


def test_unexpected_exception_fails_job() -> None:
    def bad_llm(model: str, temperature: float) -> LLMClient:
        raise RuntimeError("no api key")

    async def scenario() -> None:
        jobs = JobManager(http_factory, bad_llm)
        job = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
        assert job.task is not None
        await job.task
        assert job.state == JobState.FAILED
        assert job.error == "no api key" and job.report is None

    run(scenario)


def test_only_recent_jobs_retained() -> None:
    async def scenario() -> None:
        jobs = manager()
        ids = []
        for _ in range(MAX_RETAINED_JOBS + 3):
            job = jobs.start("https://example.com/", Mode.PAGE, CONFIG)
            ids.append(job.id)
            assert job.task is not None
            await job.task
        assert jobs.get(ids[0]) is None
        assert jobs.get(ids[2]) is None
        assert jobs.get(ids[3]) is not None
        assert jobs.get(ids[-1]) is not None

    run(scenario)

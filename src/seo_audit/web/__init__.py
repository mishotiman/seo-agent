"""Local web UI: a thin FastAPI layer over `run_audit`."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from seo_audit.audit import InvalidInput, validate_url
from seo_audit.config import Config, ConfigError, load_config
from seo_audit.fetcher import make_client
from seo_audit.llm import LLMClient
from seo_audit.report import render_markdown
from seo_audit.schema import AuditReport, Mode
from seo_audit.web.jobs import HttpFactory, Job, JobBusy, JobManager, JobState, LLMFactory

STATIC_DIR = Path(__file__).parent / "static"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
}
EVENT_POLL_SECONDS = 0.1


def _detail(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status_code)


def _sse(event: str, data: str) -> str:
    lines = "".join(f"data: {line}\n" for line in data.splitlines() or [""])
    return f"event: {event}\n{lines}\n"


async def _job_events(job: Job) -> AsyncIterator[str]:
    """Replay the job's progress messages, follow new ones, then emit the final state."""
    sent = 0
    while True:
        state = job.state  # read first so messages appended before the state change are not lost
        while sent < len(job.progress):
            yield _sse("progress", job.progress[sent])
            sent += 1
        if not job.active_state(state):
            yield _sse("state", state.value)
            return
        await asyncio.sleep(EVENT_POLL_SECONDS)


def _report_filename(url: str, extension: str) -> str:
    host = re.sub(r"[^A-Za-z0-9.-]+", "-", urlsplit(url).hostname or "").strip("-.")
    return f"seo-audit-{host or 'report'}.{extension}"


def _job_json(job: Job) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": job.id,
        "url": job.url,
        "mode": job.mode.value,
        "state": job.state.value,
        "progress": list(job.progress),
    }
    if job.error is not None:
        data["error"] = job.error
    if job.report is not None:
        data["report"] = job.report.model_dump(mode="json")
    return data


def _override_config(config: Config, model: object, max_pages: object) -> Config:
    """Apply the request's `model` / `max_pages` on top of the loaded config, like the CLI does."""
    overrides = config.model_dump()
    if model is not None:
        if not isinstance(model, str):
            raise InvalidInput("model must be a string")
        if model.strip():
            overrides["model"] = model.strip()
    if max_pages is not None:
        limit = config.crawl.max_pages
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= limit:
            raise InvalidInput(f"max_pages must be an integer between 1 and {limit}")
        overrides["crawl"]["max_pages"] = max_pages
    return Config.model_validate(overrides)


def create_app(
    config_path: Path | None = None,
    http_factory: HttpFactory = make_client,
    llm_factory: LLMFactory = LLMClient,
) -> FastAPI:
    app = FastAPI(title="SEO Audit", docs_url=None, redoc_url=None)
    jobs = JobManager(http_factory, llm_factory)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _detail("Invalid request: expected a JSON object", 422)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config", response_model=None)
    async def config() -> dict[str, object] | JSONResponse:
        try:
            cfg = load_config(config_path)
        except ConfigError as exc:
            return _detail(str(exc), 500)
        return {
            "model": cfg.model,
            "max_pages": cfg.crawl.max_pages,
            "max_pages_limit": cfg.crawl.max_pages,
            "modes": ["page", "site"],
        }

    @app.post("/api/audits", status_code=202, response_model=None)
    async def start_audit(payload: dict[str, Any] = Body(...)) -> dict[str, str] | JSONResponse:
        try:
            url = validate_url(str(payload.get("url") or ""))
            try:
                mode = Mode(payload.get("mode", Mode.PAGE.value))
            except ValueError:
                raise InvalidInput("mode must be 'page' or 'site'") from None
            cfg = _override_config(load_config(config_path), payload.get("model"), payload.get("max_pages"))
        except (InvalidInput, ConfigError) as exc:
            return _detail(str(exc), 422)
        try:
            job = jobs.start(url, mode, cfg)
        except JobBusy as exc:
            return _detail(str(exc), 409)
        return {"id": job.id}

    @app.get("/api/audits/{job_id}", response_model=None)
    async def audit_status(job_id: str) -> dict[str, Any] | JSONResponse:
        job = jobs.get(job_id)
        if job is None:
            return _detail("Audit not found", 404)
        return _job_json(job)

    @app.get("/api/audits/{job_id}/events", response_model=None)
    async def audit_events(job_id: str) -> StreamingResponse | JSONResponse:
        job = jobs.get(job_id)
        if job is None:
            return _detail("Audit not found", 404)
        return StreamingResponse(
            _job_events(job),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/audits/{job_id}/cancel", response_model=None)
    async def cancel_audit(job_id: str) -> dict[str, str] | JSONResponse:
        job = jobs.get(job_id)
        if job is None:
            return _detail("Audit not found", 404)
        if not job.active:
            return _detail("Audit has already finished", 409)
        jobs.cancel(job_id)
        return {"state": job.state.value}

    def report_download(job_id: str, extension: str, media_type: str, render: Callable[[AuditReport], str]) -> Response:
        job = jobs.get(job_id)
        if job is None:
            return _detail("Audit not found", 404)
        if job.state != JobState.DONE or job.report is None:
            return _detail("Audit has no report yet", 409)
        return Response(
            render(job.report),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{_report_filename(job.url, extension)}"'},
        )

    @app.get("/api/audits/{job_id}/report.md", response_model=None)
    async def report_markdown(job_id: str) -> Response:
        return report_download(job_id, "md", "text/markdown; charset=utf-8", render_markdown)

    @app.get("/api/audits/{job_id}/report.json", response_model=None)
    async def report_json(job_id: str) -> Response:
        return report_download(
            job_id, "json", "application/json", lambda report: report.model_dump_json(indent=2)
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            media_type="text/html; charset=utf-8",
            headers=SECURITY_HEADERS,
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app

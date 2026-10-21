import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from seo_audit.fetcher import make_client
from seo_audit.llm import LLMClient
from seo_audit.web import create_app, server


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_serves_placeholder_page() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>SEO Audit</title>" in response.text


def test_index_has_security_headers() -> None:
    response = TestClient(create_app()).get("/")
    assert response.headers["content-security-policy"] == "default-src 'self'"
    assert response.headers["x-content-type-options"] == "nosniff"


def _run_serve(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> tuple[str, dict[str, Any]]:
    calls: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        calls.update(kwargs)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    cli = typer.Typer()
    cli.command()(server.serve)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    return result.output, calls


def test_serve_binds_loopback_by_default_without_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    output, calls = _run_serve(monkeypatch, [])
    assert calls["host"] == "127.0.0.1"
    assert "http://127.0.0.1:8000" in output
    assert "no authentication" not in output


def test_serve_warns_when_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    output, calls = _run_serve(monkeypatch, ["--host", "0.0.0.0"])
    assert calls["host"] == "0.0.0.0"
    assert "no authentication" in output
    assert "arbitrary URLs" in output


def test_server_options() -> None:
    app = typer.Typer()
    app.command()(server.serve)
    result = CliRunner().invoke(app, ["--help"])
    assert "--host" in result.output
    assert "--port" in result.output


def test_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SEO_AUDIT_MODEL", "SEO_AUDIT_CRAWL__MAX_PAGES"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("model: openai/gpt-4o\ncrawl:\n  max_pages: 25\n", encoding="utf-8")
    response = TestClient(create_app(path)).get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "model": "openai/gpt-4o",
        "max_pages": 25,
        "max_pages_limit": 25,
        "modes": ["page", "site"],
    }


def test_config_invalid_returns_500(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model: ''\n", encoding="utf-8")
    response = TestClient(create_app(path)).get("/api/config")
    assert response.status_code == 500
    assert "Invalid configuration" in response.json()["detail"]


PAGE = "<html><head><title>Widgets and more widgets for sale</title></head><body><h1>Widgets</h1></body></html>"


def _http_factory(timeout: float) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    return make_client(timeout, transport=httpx.MockTransport(handler))


def _llm_factory(model: str, temperature: float) -> LLMClient:
    def completion(**kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"findings": []})))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    return LLMClient(model, temperature, completion=completion)


@pytest.fixture
def audit_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for name in ("SEO_AUDIT_MODEL", "SEO_AUDIT_CRAWL__MAX_PAGES"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("model: test/model\ncrawl:\n  max_pages: 5\n", encoding="utf-8")
    return TestClient(create_app(path, _http_factory, _llm_factory))


def _wait_finished(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        body: dict[str, Any] = client.get(f"/api/audits/{job_id}").json()
        if body["state"] not in ("queued", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError("audit did not finish")


def test_start_and_fetch_page_audit(audit_client: TestClient) -> None:
    response = audit_client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"})
    assert response.status_code == 202
    job_id = response.json()["id"]
    body = _wait_finished(audit_client, job_id)
    assert body["id"] == job_id
    assert body["url"] == "https://example.com/"
    assert body["mode"] == "page"
    assert body["state"] == "done"
    assert body["progress"][0] == "Fetching https://example.com/"
    assert "error" not in body
    assert body["report"]["pages"][0]["status_code"] == 200
    assert body["report"]["model"] == "test/model"


def test_overrides_are_applied(audit_client: TestClient) -> None:
    response = audit_client.post(
        "/api/audits", json={"url": "https://example.com/", "mode": "page", "model": "other/model", "max_pages": 2}
    )
    assert response.status_code == 202
    body = _wait_finished(audit_client, response.json()["id"])
    assert body["report"]["model"] == "other/model"


def test_invalid_url(audit_client: TestClient) -> None:
    response = audit_client.post("/api/audits", json={"url": "ftp://example.com", "mode": "page"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid URL 'ftp://example.com': expected an absolute http(s) URL"}
    assert audit_client.post("/api/audits", json={"mode": "page"}).status_code == 422


def test_invalid_mode_and_body(audit_client: TestClient) -> None:
    response = audit_client.post("/api/audits", json={"url": "https://example.com/", "mode": "deep"})
    assert response.status_code == 422
    assert response.json() == {"detail": "mode must be 'page' or 'site'"}
    assert audit_client.post("/api/audits", content="nope").status_code == 422


@pytest.mark.parametrize("max_pages", [0, 6, -1, 2.5, "3", True])
def test_bad_max_pages(audit_client: TestClient, max_pages: object) -> None:
    response = audit_client.post(
        "/api/audits", json={"url": "https://example.com/", "mode": "site", "max_pages": max_pages}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "max_pages must be an integer between 1 and 5"}


def test_invalid_config_is_422(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model: ''\n", encoding="utf-8")
    client = TestClient(create_app(path, _http_factory, _llm_factory))
    response = client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"})
    assert response.status_code == 422
    assert "Invalid configuration" in response.json()["detail"]


def test_busy_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEO_AUDIT_MODEL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("model: test/model\n", encoding="utf-8")

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    def factory(timeout: float) -> httpx.AsyncClient:
        return make_client(timeout, transport=httpx.MockTransport(hang))

    # The context manager keeps one event loop alive across requests, so the first audit keeps running.
    with TestClient(create_app(path, factory, _llm_factory)) as client:
        first = client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"})
        assert first.status_code == 202
        second = client.post("/api/audits", json={"url": "https://example.com/other", "mode": "page"})
        assert second.status_code == 409
        assert second.json() == {"detail": "An audit is already running"}


def test_unknown_audit_id(audit_client: TestClient) -> None:
    response = audit_client.get("/api/audits/nope")
    assert response.status_code == 404
    assert "detail" in response.json()


def _events(client: TestClient, job_id: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    with client.stream("GET", f"/api/audits/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for block in response.read().decode().strip().split("\n\n"):
            lines = block.split("\n")
            events.append((lines[0].removeprefix("event: "), "\n".join(x.removeprefix("data: ") for x in lines[1:])))
    return events


def test_events_replay_finished_job(audit_client: TestClient) -> None:
    job_id = audit_client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"}).json()["id"]
    body = _wait_finished(audit_client, job_id)
    events = _events(audit_client, job_id)
    assert [data for kind, data in events if kind == "progress"] == body["progress"]
    assert events[0] == ("progress", "Fetching https://example.com/")
    assert events[-1] == ("state", "done")
    assert [kind for kind, _ in events].count("state") == 1


def test_events_follow_running_job(audit_client: TestClient) -> None:
    job_id = audit_client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"}).json()["id"]
    events = _events(audit_client, job_id)  # connects while the job may still be running
    assert events[0] == ("progress", "Fetching https://example.com/")
    assert events[-1] == ("state", "done")
    assert _wait_finished(audit_client, job_id)["progress"] == [d for k, d in events if k == "progress"]


def test_events_unknown_id(audit_client: TestClient) -> None:
    assert audit_client.get("/api/audits/nope/events").status_code == 404


def test_cancel_running_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEO_AUDIT_MODEL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("model: test/model\n", encoding="utf-8")

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    def factory(timeout: float) -> httpx.AsyncClient:
        return make_client(timeout, transport=httpx.MockTransport(hang))

    with TestClient(create_app(path, factory, _llm_factory)) as client:
        job_id = client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"}).json()["id"]
        response = client.post(f"/api/audits/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json() == {"state": "cancelled"}
        assert client.get(f"/api/audits/{job_id}").json()["state"] == "cancelled"
        assert _events(client, job_id)[-1] == ("state", "cancelled")
        again = client.post(f"/api/audits/{job_id}/cancel")
        assert again.status_code == 409
        assert "detail" in again.json()


def test_cancel_finished_and_unknown(audit_client: TestClient) -> None:
    job_id = audit_client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"}).json()["id"]
    _wait_finished(audit_client, job_id)
    assert audit_client.post(f"/api/audits/{job_id}/cancel").status_code == 409
    assert audit_client.post("/api/audits/nope/cancel").status_code == 404


def test_report_downloads(audit_client: TestClient) -> None:
    job_id = audit_client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"}).json()["id"]
    body = _wait_finished(audit_client, job_id)

    md = audit_client.get(f"/api/audits/{job_id}/report.md")
    assert md.status_code == 200
    assert md.headers["content-type"] == "text/markdown; charset=utf-8"
    assert md.headers["content-disposition"] == 'attachment; filename="seo-audit-example.com.md"'
    assert md.text.startswith("# SEO audit report: https://example.com/")

    js = audit_client.get(f"/api/audits/{job_id}/report.json")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("application/json")
    assert js.headers["content-disposition"] == 'attachment; filename="seo-audit-example.com.json"'
    assert js.json() == body["report"]
    assert "\n  " in js.text  # indented


def test_report_filename_is_sanitized() -> None:
    from seo_audit.web import _report_filename

    assert _report_filename("https://exa mple.com:8080/a?b=c", "md") == "seo-audit-exa-mple.com.md"
    assert _report_filename("https://[::1]/", "json") == "seo-audit-1.json"
    assert _report_filename("not a url", "md") == "seo-audit-report.md"


def test_report_unknown_and_unfinished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEO_AUDIT_MODEL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("model: test/model\n", encoding="utf-8")

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    def factory(timeout: float) -> httpx.AsyncClient:
        return make_client(timeout, transport=httpx.MockTransport(hang))

    with TestClient(create_app(path, factory, _llm_factory)) as client:
        for ext in ("md", "json"):
            missing = client.get(f"/api/audits/nope/report.{ext}")
            assert missing.status_code == 404
            assert "detail" in missing.json()
        job_id = client.post("/api/audits", json={"url": "https://example.com/", "mode": "page"}).json()["id"]
        for ext in ("md", "json"):
            running = client.get(f"/api/audits/{job_id}/report.{ext}")
            assert running.status_code == 409
            assert "detail" in running.json()
        client.post(f"/api/audits/{job_id}/cancel")
        assert client.get(f"/api/audits/{job_id}/report.md").status_code == 409


def test_static_assets_are_served_and_linked() -> None:
    client = TestClient(create_app())
    page = client.get("/").text
    assert "/static/styles.css" in page
    assert "/static/app.js" in page
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "prefers-color-scheme: dark" in css.text
    assert "--sev-critical-fg" in css.text
    assert client.get("/static/app.js").status_code == 200
    assert "http://" not in css.text and "https://" not in css.text


def test_page_has_no_inline_script_or_style() -> None:
    page = TestClient(create_app()).get("/").text
    assert "<style" not in page
    assert "style=" not in page
    assert "<script>" not in page


def test_form_markup_has_all_controls() -> None:
    page = TestClient(create_app()).get("/").text
    assert 'placeholder="https://example.com"' in page
    assert "autofocus" in page
    assert 'name="mode" value="page" checked' in page
    assert 'name="mode" value="site"' in page
    assert 'id="max-pages-field" hidden' in page
    assert "Any LiteLLM model, e.g. openai/gpt-4o" in page
    assert ">Run audit</button>" in page
    for field in ("url", "max-pages", "model", "mode-page", "mode-site"):
        assert f'<label for="{field}"' in page


def test_run_ui_assets_cover_progress_stream_and_fallback() -> None:
    client = TestClient(create_app())
    js = client.get("/static/app.js").text
    assert "/events" in js and "EventSource" in js
    assert "/cancel" in js
    assert "POLL_MS = 2000" in js
    assert "Finished in " in js
    assert "innerHTML" not in js
    css = client.get("/static/styles.css").text
    assert "prefers-reduced-motion: reduce" in css
    assert "indicator-spin" in css


def test_results_summary_assets() -> None:
    client = TestClient(create_app())
    assert "/static/report.js" in client.get("/").text
    js = client.get("/static/report.js")
    assert js.status_code == 200
    text = js.text
    for expected in ("audit:finished", "What to fix first", "pages_crawled", "pages_skipped", "limit_hit",
                     "dropped_model_findings", "total_tokens", "site_summary", "could not be verified"):
        assert expected in text
    for severity in ("critical", "high", "medium", "low", "info"):
        assert f'"{severity}"' in text
    # report text comes from audited sites: never inject it as HTML
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "outerHTML" not in text
    css = client.get("/static/styles.css").text
    for selector in (".stat-tile", ".severity-row", ".notice", ".fix-list"):
        assert selector in css


def test_findings_list_assets() -> None:
    client = TestClient(create_app())
    text = client.get("/static/report.js").text
    for expected in ("Site-wide", "site_findings", "status_code", "aria-expanded", "aria-pressed", "Confidence",
                     "No findings match", "MAX_OPEN_PAGES", "Filter by severity", "Filter by source", "recommendation"):
        assert expected in text
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "outerHTML" not in text
    css = client.get("/static/styles.css").text
    for selector in (".chip", ".finding-group", ".finding-head", ".group-head", ".badge-source"):
        assert selector in css


def test_downloads_errors_and_empty_state_assets() -> None:
    client = TestClient(create_app())
    text = client.get("/static/report.js").text
    for expected in ("Download Markdown", "Download JSON", "/report.", "Issues during the audit", "isTotalFailure",
                     "No page was fetched successfully", "job.error", "No findings", "New audit", "audit:reset"):
        assert expected in text
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "outerHTML" not in text
    js = client.get("/static/app.js").text
    assert "audit:reset" in js
    assert 'state === "failed"' in js
    css = client.get("/static/styles.css").text
    for selector in (".report-head", ".error-panel", ".issue-list", ".issue-stage"):
        assert selector in css

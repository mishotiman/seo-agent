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

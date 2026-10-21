from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

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

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

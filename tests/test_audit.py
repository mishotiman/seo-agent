import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jsonschema
import pytest
from typer.testing import CliRunner

from seo_audit import cli
from seo_audit.fetcher import make_client
from seo_audit.llm import LLMClient
from seo_audit.schema import SCHEMA_PATH

runner = CliRunner()

ROBOTS = "User-agent: *\nAllow: /\n"


def html(title: str, body: str) -> str:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1><p>{body}</p></body></html>"
    )


PAGES = {
    "/": html("Home page of the example widget store", 'Welcome to widgets. <a href="/a">A</a> <a href="/gone">G</a>'),
    "/a": html("Home page of the example widget store", "Widgets are great. We sell many kinds."),
}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS)
    if path in PAGES:
        return httpx.Response(200, text=PAGES[path], headers={"content-type": "text/html"})
    return httpx.Response(404, text="nope")


class FakeModel:
    """Answers page prompts with one grounded + one ungrounded finding, synthesis with actions."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def __call__(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        system = kwargs["messages"][0]["content"]
        if "top_actions" in system:
            content: dict[str, Any] = {"top_actions": ["Fix duplicate titles"]}
        else:
            content = {"findings": [
                {"id": "intro-thin", "category": "content", "severity": "low",
                 "evidence_quote": "Widgets", "recommendation": "Expand.", "confidence": 0.6},
                {"id": "made-up", "category": "content", "severity": "low",
                 "evidence_quote": "not on the page at all", "recommendation": "n/a", "confidence": 0.9},
            ]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    model = FakeModel()
    monkeypatch.setattr(cli, "_make_client", lambda timeout: make_client(timeout, httpx.MockTransport(handler)))
    monkeypatch.setattr(cli, "_make_llm", lambda name, temp: LLMClient(name, temp, completion=model))
    return model


def run(*args: str) -> Any:
    return runner.invoke(cli.app, ["--config", "config.yaml", *args])


def test_site_mode_end_to_end(fake: FakeModel) -> None:
    result = run("https://example.com/", "--mode", "site", "--model", "fake/model")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    jsonschema.validate(report, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert report["mode"] == "site" and report["model"] == "fake/model"
    assert report["crawl"]["pages_crawled"] == 3  # /, /a and the 404 /gone
    sources = {f["source"] for p in report["pages"] for f in p["findings"]}
    assert sources == {"code", "model"}
    assert report["dropped_model_findings"] == 2  # one ungrounded finding on each of the 2 OK pages
    assert any(f["id"] == "duplicate-titles" for f in report["site_findings"])
    assert any(f["id"] == "broken-internal-links" for f in report["site_findings"])
    assert report["site_summary"] == ["Fix duplicate titles"]
    assert report["token_usage"]["total_tokens"] > 0
    assert "Crawling" in result.stderr


def test_page_mode_audits_only_given_url(fake: FakeModel) -> None:
    result = run("https://example.com/", "--mode", "page")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    assert [p["url"] for p in report["pages"]] == ["https://example.com/"]
    assert report["site_findings"] == [] and report["site_summary"] == []
    assert report["model"] == "anthropic/claude-sonnet-5-5"  # from config.yaml


def test_out_file_and_max_pages(fake: FakeModel, tmp_path: Path) -> None:
    target = tmp_path / "r.json"
    result = run("https://example.com/", "--mode", "site", "--max-pages", "1", "--out", str(target))
    assert result.exit_code == 0 and result.stdout == ""
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["crawl"]["pages_crawled"] == 1 and report["crawl"]["limit_hit"] == "max_pages"


def test_model_failure_keeps_code_findings(monkeypatch: pytest.MonkeyPatch, fake: FakeModel) -> None:
    fake.fail = True
    result = run("https://example.com/", "--mode", "site")
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["site_summary"] == []
    stages = {e["stage"] for e in report["errors"]}
    assert {"model_page_checks", "site_synthesis"} <= stages
    assert any(f["source"] == "code" for p in report["pages"] for f in p["findings"])


@pytest.mark.parametrize("args", [["not-a-url"], ["ftp://example.com/"], ["https://example.com/", "--mode", "bogus"],
                                  ["https://example.com/", "--max-pages", "0"],
                                  ["https://example.com/", "--format", "html"]])
def test_invalid_input_exits_nonzero(fake: FakeModel, args: list[str]) -> None:
    result = run(*args)
    assert result.exit_code != 0
    assert fake.calls == 0


def test_invalid_config_exits_2(fake: FakeModel, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("model: x\ncrawl:\n  max_pages: 0\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["https://example.com/", "--config", str(bad)])
    assert result.exit_code == 2 and "max_pages" in result.stderr


def test_total_failure_exits_1(monkeypatch: pytest.MonkeyPatch, fake: FakeModel) -> None:
    monkeypatch.setattr(cli, "_make_client", lambda timeout: make_client(
        timeout, httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))))
    result = run("https://example.com/", "--mode", "page")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["errors"][0]["stage"] == "fetch"


def test_markdown_format(fake: FakeModel) -> None:
    result = run("https://example.com/", "--mode", "site", "--format", "md")
    assert result.exit_code == 0
    assert result.stdout.startswith("# SEO audit report: https://example.com/")
    assert "[code]" in result.stdout and "[model, confidence 0.60]" in result.stdout
    assert "1. Fix duplicate titles" in result.stdout

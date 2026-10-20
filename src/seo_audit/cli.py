"""Command line entry point: `seo-audit <url> --mode page|site`."""

from __future__ import annotations

import asyncio
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import httpx
import typer

from seo_audit.audit import InvalidInput, is_total_failure, run_audit, validate_url
from seo_audit.config import Config, ConfigError, load_config
from seo_audit.fetcher import make_client
from seo_audit.llm import LLMClient
from seo_audit.report import render_markdown
from seo_audit.schema import AuditReport, Mode

app = typer.Typer(add_completion=False, help="SEO audit agent")


class OutputFormat(StrEnum):
    JSON = "json"
    MD = "md"


def _make_client(timeout: float) -> httpx.AsyncClient:
    return make_client(timeout)


def _make_llm(model: str, temperature: float) -> LLMClient:
    return LLMClient(model, temperature)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _fail(message: str, code: int = 2) -> typer.Exit:
    _log(f"error: {message}")
    return typer.Exit(code)


def _render(report: AuditReport, fmt: OutputFormat) -> str:
    if fmt == OutputFormat.JSON:
        return report.model_dump_json(indent=2) + "\n"
    return render_markdown(report)


def _load(config_path: Path | None, model: str | None, max_pages: int | None) -> Config:
    config = load_config(config_path)
    overrides = config.model_dump()
    if model:
        overrides["model"] = model
    if max_pages is not None:
        overrides["crawl"]["max_pages"] = max_pages
    return Config.model_validate(overrides)


@app.command()
def audit(
    url: Annotated[str, typer.Argument(help="Page or site URL to audit")],
    mode: Annotated[Mode, typer.Option(help="page: audit one URL; site: bounded crawl")] = Mode.PAGE,
    config: Annotated[Path | None, typer.Option("--config", help="Path to config.yaml")] = None,
    model: Annotated[str | None, typer.Option("--model", help="LiteLLM model name; overrides config")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1, help="Override crawl.max_pages")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Write the report here instead of stdout")] = None,
    fmt: Annotated[OutputFormat, typer.Option("--format", help="Report format")] = OutputFormat.JSON,
) -> None:
    """Audit URL and write the report to stdout (or --out); progress goes to stderr."""
    try:
        validate_url(url)
        cfg = _load(config, model, max_pages)
    except (InvalidInput, ConfigError) as exc:
        raise _fail(str(exc)) from exc

    async def go() -> AuditReport:
        async with _make_client(cfg.crawl.request_timeout) as client:
            return await run_audit(url, mode, cfg, client, _make_llm(cfg.model, cfg.temperature), _log)

    try:
        report = asyncio.run(go())
    except InvalidInput as exc:
        raise _fail(str(exc)) from exc
    text = _render(report, fmt)
    if out is not None:
        try:
            out.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise _fail(f"cannot write {out}: {exc}") from exc
        _log(f"Report written to {out}")
    else:
        sys.stdout.write(text)
    if is_total_failure(report):
        _log("error: audit failed: no page could be fetched successfully")
        raise typer.Exit(1)

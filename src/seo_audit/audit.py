"""Audit orchestrator: ties fetch/crawl, code checks, model checks and synthesis into a report."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from seo_audit.checks import PageContext, run_page_checks
from seo_audit.config import Config
from seo_audit.crawler import CrawledPage, crawl
from seo_audit.extractor import extract_facts
from seo_audit.fetcher import fetch_page
from seo_audit.llm import LLMClient
from seo_audit.model_checks import ModelCheckResult, run_model_checks
from seo_audit.schema import AuditReport, CrawlSummary, Finding, Mode, PageResult, ReportError
from seo_audit.site_checks import SiteContext, SiteFile, probe_site_files, run_site_checks
from seo_audit.synthesis import synthesize_site
from seo_audit.urls import normalize_url

Progress = Callable[[str], None]


class InvalidInput(ValueError):
    """Raised for a start URL that is not an absolute http(s) URL."""


def validate_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidInput(f"Invalid URL {url!r}: expected an absolute http(s) URL")
    return url.strip()


async def _fetch_single(client: httpx.AsyncClient, url: str, config: Config) -> CrawledPage:
    result = await fetch_page(client, normalize_url(url))
    facts = extract_facts(result.body, result.final_url, config.max_text_chars) if result.body else None
    return CrawledPage(url=result.url, depth=0, fetch=result, facts=facts)


def _fetch_error(page: CrawledPage) -> ReportError | None:
    fetch = page.fetch
    if fetch.error:
        return ReportError(stage="fetch", message=f"{fetch.error}: {fetch.error_detail or ''}".strip(" :"), url=page.url)
    if fetch.status_code is not None and fetch.status_code >= 400:
        return ReportError(stage="fetch", message=f"HTTP {fetch.status_code}", url=page.url)
    return None


async def run_audit(
    url: str,
    mode: Mode,
    config: Config,
    client: httpx.AsyncClient,
    llm: LLMClient,
    progress: Progress = lambda _msg: None,
) -> AuditReport:
    """Audit `url`: one page (page mode) or a bounded crawl plus site checks and synthesis."""
    url = validate_url(url)
    errors: list[ReportError] = []

    robots = sitemap = SiteFile()
    if mode == Mode.SITE:
        progress(f"Crawling {url} (max {config.crawl.max_pages} pages, depth {config.crawl.max_depth})")
        crawled = await crawl(client, url, config.crawl, max_text_chars=config.max_text_chars)
        pages, summary = crawled.pages, crawled.summary
        progress(f"Crawled {summary.pages_crawled} pages, skipped {summary.pages_skipped}")
        robots, sitemap = await probe_site_files(client, url)
    else:
        progress(f"Fetching {url}")
        pages = [await _fetch_single(client, url, config)]
        summary = CrawlSummary(pages_crawled=1)

    results: list[PageResult] = []
    for page in pages:
        findings: list[Finding] = []
        if page.facts is not None and page.fetch.ok:
            findings = run_page_checks(PageContext(facts=page.facts, fetch=page.fetch))
        elif (err := _fetch_error(page)) is not None and (mode == Mode.PAGE or page.fetch.error not in (None, "not_html")):
            errors.append(err)  # site mode: HTTP 4xx/5xx are reported by the broken-link check
        results.append(
            PageResult(
                url=page.url,
                final_url=page.fetch.final_url,
                status_code=page.fetch.status_code,
                findings=findings,
            )
        )

    progress("Running model checks")
    semaphore = asyncio.Semaphore(config.crawl.concurrency)

    async def judge(page: CrawledPage) -> ModelCheckResult | None:
        if page.facts is None or not page.fetch.ok:
            return None
        async with semaphore:
            return await asyncio.to_thread(run_model_checks, llm, page.facts)

    dropped = 0
    outcomes = await asyncio.gather(*(judge(p) for p in pages))
    for result, outcome in zip(results, outcomes, strict=True):
        if outcome is None:
            continue
        result.findings.extend(outcome.findings)
        dropped += outcome.dropped
        if outcome.error:
            errors.append(outcome.error)

    site_findings: list[Finding] = []
    site_summary: list[str] = []
    if mode == Mode.SITE:
        site_findings = run_site_checks(SiteContext(pages=pages, robots=robots, sitemap=sitemap))
        progress("Synthesizing site summary")
        synthesis = await asyncio.to_thread(synthesize_site, llm, mode, results, site_findings, summary)
        site_summary = synthesis.site_summary
        if synthesis.error:
            errors.append(synthesis.error)

    return AuditReport(
        url=url,
        mode=mode,
        model=llm.model,
        crawl=summary,
        pages=results,
        site_findings=site_findings,
        site_summary=site_summary,
        dropped_model_findings=dropped,
        errors=errors,
        token_usage=llm.usage,
    )


def is_total_failure(report: AuditReport) -> bool:
    """True when no page was fetched successfully (page mode: the one page failed or isn't HTML)."""
    if report.mode == Mode.PAGE and any(e.stage == "fetch" for e in report.errors):
        return True
    return not any(p.status_code is not None and p.status_code < 400 for p in report.pages)

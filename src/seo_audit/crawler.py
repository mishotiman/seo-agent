"""Breadth-first crawler bounded by page count, depth, total time and concurrency."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from urllib.parse import urldefrag, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

from seo_audit.config import CrawlConfig
from seo_audit.extractor import PageFacts, extract_facts
from seo_audit.fetcher import DEFAULT_MAX_BYTES, FetchResult, fetch_page
from seo_audit.schema import CrawlSummary


class CrawledPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    depth: int
    fetch: FetchResult
    facts: PageFacts | None = None


class CrawlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[CrawledPage]
    summary: CrawlSummary


def _base_host(host: str) -> str:
    return host.removeprefix("www.")


def host_allowed(url: str, start_host: str, include_subdomains: bool) -> bool:
    """Same host (www-insensitive); subdomains of the start host only when configured."""
    host = (urlsplit(url).hostname or "").lower()
    if host == start_host:
        return True
    if not include_subdomains:
        return False
    base = _base_host(start_host)
    return host == base or host.endswith("." + base)


async def crawl(
    client: httpx.AsyncClient,
    start_url: str,
    config: CrawlConfig,
    max_text_chars: int = 8000,
    max_bytes: int = DEFAULT_MAX_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> CrawlResult:
    """BFS from `start_url`; stops at max_pages, max_depth or total_time_budget.

    `limit_hit` records the first limit that caused URLs to be skipped; skipped URLs are listed.
    """
    start_url = urldefrag(start_url)[0]
    start_host = (urlsplit(start_url).hostname or "").lower()
    deadline = clock() + config.total_time_budget
    semaphore = asyncio.Semaphore(config.concurrency)

    pages: list[CrawledPage] = []
    skipped: list[str] = []
    limit_hit: str | None = None
    seen = {start_url}

    def record_limit(name: str) -> None:
        nonlocal limit_hit
        if limit_hit is None:
            limit_hit = name

    async def fetch_one(url: str, depth: int) -> CrawledPage | None:
        async with semaphore:
            remaining = deadline - clock()
            if remaining <= 0:
                return None
            try:
                result = await asyncio.wait_for(fetch_page(client, url, max_bytes), remaining)
            except asyncio.TimeoutError:
                return None
        facts = extract_facts(result.body, result.final_url, max_text_chars) if result.body else None
        return CrawledPage(url=url, depth=depth, fetch=result, facts=facts)

    level = [start_url]
    depth = 0
    while level:
        room = config.max_pages - len(pages)
        batch, overflow = level[:room], level[room:]
        if overflow:
            record_limit("max_pages")
            skipped.extend(overflow)
        outcomes = await asyncio.gather(*(fetch_one(u, depth) for u in batch))
        next_level: list[str] = []
        for url, page in zip(batch, outcomes, strict=True):
            if page is None:
                record_limit("time_budget")
                skipped.append(url)
                continue
            pages.append(page)
            if page.facts is None:
                continue
            for link in (*page.facts.internal_links, *page.facts.external_links):
                target = urldefrag(link.url)[0]
                if target in seen or not host_allowed(target, start_host, config.include_subdomains):
                    continue
                seen.add(target)
                if depth >= config.max_depth:
                    record_limit("max_depth")
                    skipped.append(target)
                else:
                    next_level.append(target)
        level = next_level
        depth += 1
        if level and clock() >= deadline:
            record_limit("time_budget")
            skipped.extend(level)
            break

    summary = CrawlSummary(
        pages_crawled=len(pages),
        pages_skipped=len(skipped),
        limit_hit=limit_hit,
        skipped_urls=skipped,
    )
    return CrawlResult(pages=pages, summary=summary)

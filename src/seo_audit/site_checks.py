"""Deterministic site-level (cross-page) checks, collected through their own registry.

Add a check by writing a function `(SiteContext) -> list[Finding]` and decorating it with
`@site_check`; `run_site_checks` picks it up without any change to the agent loop.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from seo_audit.checks import _finding, _sample
from seo_audit.crawler import CrawledPage
from seo_audit.schema import Finding, Severity
from seo_audit.urls import normalize_url


@dataclass(frozen=True)
class SiteFile:
    """Outcome of fetching robots.txt or sitemap.xml: `status` is None on connection errors."""

    status: int | None = None
    text: str = ""

    @property
    def present(self) -> bool:
        return self.status == 200


@dataclass(frozen=True)
class SiteContext:
    pages: list[CrawledPage]
    robots: SiteFile
    sitemap: SiteFile


SiteCheck = Callable[[SiteContext], list[Finding]]
SITE_CHECKS: list[SiteCheck] = []


def site_check(fn: SiteCheck) -> SiteCheck:
    """Register `fn` in the site check registry."""
    SITE_CHECKS.append(fn)
    return fn


def run_site_checks(ctx: SiteContext, checks: list[SiteCheck] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for check in SITE_CHECKS if checks is None else checks:
        findings.extend(check(ctx))
    return findings


async def _probe(client: httpx.AsyncClient, url: str) -> SiteFile:
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return SiteFile()
    return SiteFile(response.status_code, response.text if response.status_code == 200 else "")


async def probe_site_files(client: httpx.AsyncClient, start_url: str) -> tuple[SiteFile, SiteFile]:
    """Fetch (robots.txt, sitemap.xml) from the origin of `start_url`."""
    parts = urlsplit(start_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return await _probe(client, f"{origin}/robots.txt"), await _probe(client, f"{origin}/sitemap.xml")


def _duplicates(pages: list[CrawledPage], value: Callable[[CrawledPage], str | None]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        text = value(page)
        if text and text.strip():
            groups[" ".join(text.split()).lower()].append(page.url)
    return {text: urls for text, urls in groups.items() if len(urls) > 1}


def _describe(groups: dict[str, list[str]]) -> str:
    return "; ".join(f"{text!r} on {len(urls)} pages: {_sample(urls)}" for text, urls in groups.items())


@site_check
def check_duplicate_titles(ctx: SiteContext) -> list[Finding]:
    groups = _duplicates(ctx.pages, lambda p: p.facts.title if p.facts else None)
    if not groups:
        return []
    return [_finding("duplicate-titles", "metadata", Severity.MEDIUM, _describe(groups),
                     "Give every page a unique <title> that reflects its specific content.")]


@site_check
def check_duplicate_meta_descriptions(ctx: SiteContext) -> list[Finding]:
    groups = _duplicates(ctx.pages, lambda p: p.facts.meta_description if p.facts else None)
    if not groups:
        return []
    return [_finding("duplicate-meta-descriptions", "metadata", Severity.LOW, _describe(groups),
                     "Write a unique meta description for each page.")]


@site_check
def check_broken_internal_links(ctx: SiteContext) -> list[Finding]:
    broken = {
        p.url: p.fetch.status_code
        for p in ctx.pages
        if p.fetch.status_code is not None and p.fetch.status_code >= 400
    }
    if not broken:
        return []
    linked_from: dict[str, list[str]] = defaultdict(list)
    for page in ctx.pages:
        if page.facts is None:
            continue
        for link in page.facts.internal_links:
            target = normalize_url(link.url)
            if target in broken and page.url not in linked_from[target]:
                linked_from[target].append(page.url)
    listed = [f"{url} (HTTP {status}) linked from {_sample(linked_from[url]) or 'start URL'}"
              for url, status in broken.items()]
    return [_finding("broken-internal-links", "links", Severity.HIGH,
                     f"{len(broken)} internal URL(s) return 4xx/5xx: " + "; ".join(listed[:5])
                     + (f" (+{len(listed) - 5} more)" if len(listed) > 5 else ""),
                     "Fix or remove the links, or restore/redirect the missing pages.")]


@site_check
def check_robots_txt(ctx: SiteContext) -> list[Finding]:
    if ctx.robots.present:
        if not ctx.robots.text.strip():
            return [_finding("robots-txt-empty", "crawlability", Severity.LOW, "robots.txt is empty.",
                             "Add directives such as 'User-agent: *' and a Sitemap line.")]
        return []
    shown = "unreachable" if ctx.robots.status is None else f"HTTP {ctx.robots.status}"
    return [_finding("robots-txt-missing", "crawlability", Severity.LOW, f"/robots.txt is {shown}.",
                     "Publish a robots.txt at the site root that references your sitemap.")]


@site_check
def check_sitemap(ctx: SiteContext) -> list[Finding]:
    if not ctx.sitemap.present:
        shown = "unreachable" if ctx.sitemap.status is None else f"HTTP {ctx.sitemap.status}"
        return [_finding("sitemap-missing", "crawlability", Severity.MEDIUM, f"/sitemap.xml is {shown}.",
                         "Publish an XML sitemap at /sitemap.xml and reference it from robots.txt.")]
    try:
        root = ET.fromstring(ctx.sitemap.text)
    except ET.ParseError as exc:
        return [_finding("sitemap-invalid", "crawlability", Severity.MEDIUM,
                         f"/sitemap.xml is not well-formed XML: {exc}",
                         "Fix the XML so search engines can read the sitemap.")]
    tag = root.tag.rsplit("}", 1)[-1]
    if tag not in ("urlset", "sitemapindex"):
        return [_finding("sitemap-invalid", "crawlability", Severity.MEDIUM,
                         f"/sitemap.xml root element is <{tag}>, expected <urlset> or <sitemapindex>.",
                         "Use the sitemaps.org format with a <urlset> or <sitemapindex> root.")]
    if not any(el.tag.rsplit("}", 1)[-1] == "loc" for el in root.iter()):
        return [_finding("sitemap-empty", "crawlability", Severity.LOW, "/sitemap.xml lists no <loc> URLs.",
                         "List the canonical, indexable URLs of the site in the sitemap.")]
    return []

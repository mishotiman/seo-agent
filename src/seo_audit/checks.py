"""Deterministic page-level checks, run in code and collected through a registry.

Add a check by writing a function `(PageContext) -> list[Finding]` and decorating it with
`@page_check`; `run_page_checks` picks it up without any change to the agent loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from seo_audit.extractor import PageFacts
from seo_audit.fetcher import FetchResult
from seo_audit.schema import Finding, Severity, Source

TITLE_MIN, TITLE_MAX = 30, 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 70, 160
SLOW_RESPONSE_SECONDS = 2.0
REQUIRED_OG_TAGS = ("og:title", "og:description", "og:image", "og:url")
MAX_LISTED = 5


@dataclass(frozen=True)
class PageContext:
    facts: PageFacts
    fetch: FetchResult
    slow_response_seconds: float = SLOW_RESPONSE_SECONDS


PageCheck = Callable[[PageContext], list[Finding]]
PAGE_CHECKS: list[PageCheck] = []


def page_check(fn: PageCheck) -> PageCheck:
    """Register `fn` in the page check registry."""
    PAGE_CHECKS.append(fn)
    return fn


def run_page_checks(ctx: PageContext, checks: list[PageCheck] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for check in PAGE_CHECKS if checks is None else checks:
        findings.extend(check(ctx))
    return findings


def _finding(id: str, category: str, severity: Severity, evidence: str, recommendation: str) -> Finding:
    return Finding(
        id=id,
        category=category,
        severity=severity,
        source=Source.CODE,
        evidence=evidence,
        recommendation=recommendation,
    )


def _sample(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f" (+{len(items) - MAX_LISTED} more)" if len(items) > MAX_LISTED else "")


@page_check
def check_title(ctx: PageContext) -> list[Finding]:
    title = ctx.facts.title
    if not title:
        return [_finding("title-missing", "metadata", Severity.HIGH, "No <title> element or it is empty.",
                         "Add a unique, descriptive <title> of 30-60 characters.")]
    if len(title) < TITLE_MIN:
        return [_finding("title-short", "metadata", Severity.MEDIUM,
                         f"Title is {len(title)} characters (min {TITLE_MIN}): {title!r}",
                         "Expand the title to 30-60 characters using descriptive keywords.")]
    if len(title) > TITLE_MAX:
        return [_finding("title-long", "metadata", Severity.LOW,
                         f"Title is {len(title)} characters (max {TITLE_MAX}): {title!r}",
                         "Shorten the title to 60 characters or fewer so it is not truncated in results.")]
    return []


@page_check
def check_meta_description(ctx: PageContext) -> list[Finding]:
    desc = ctx.facts.meta_description
    if not desc:
        return [_finding("meta-description-missing", "metadata", Severity.MEDIUM,
                         "No meta description found or it is empty.",
                         "Add a meta description of 70-160 characters summarizing the page.")]
    if len(desc) < DESCRIPTION_MIN:
        return [_finding("meta-description-short", "metadata", Severity.LOW,
                         f"Meta description is {len(desc)} characters (min {DESCRIPTION_MIN}).",
                         "Expand the meta description to 70-160 characters.")]
    if len(desc) > DESCRIPTION_MAX:
        return [_finding("meta-description-long", "metadata", Severity.LOW,
                         f"Meta description is {len(desc)} characters (max {DESCRIPTION_MAX}).",
                         "Shorten the meta description to 160 characters or fewer.")]
    return []


@page_check
def check_h1_count(ctx: PageContext) -> list[Finding]:
    h1s = [h.text for h in ctx.facts.headings if h.level == 1]
    if not h1s:
        return [_finding("h1-missing", "headings", Severity.MEDIUM, "No <h1> found on the page.",
                         "Add exactly one <h1> that describes the main topic of the page.")]
    if len(h1s) > 1:
        return [_finding("h1-multiple", "headings", Severity.LOW,
                         f"{len(h1s)} <h1> elements found: {_sample([repr(t) for t in h1s])}",
                         "Use a single <h1> and demote the others to <h2> or lower.")]
    return []


@page_check
def check_heading_hierarchy(ctx: PageContext) -> list[Finding]:
    skips: list[str] = []
    previous = 0
    for heading in ctx.facts.headings:
        if previous and heading.level > previous + 1:
            skips.append(f"h{previous} -> h{heading.level} ({heading.text!r})")
        previous = heading.level
    if not skips:
        return []
    return [_finding("heading-skip", "headings", Severity.LOW,
                     f"Heading levels skipped: {_sample(skips)}",
                     "Keep heading levels sequential (h1, h2, h3) without skipping levels.")]


@page_check
def check_canonical(ctx: PageContext) -> list[Finding]:
    canonical = ctx.facts.canonical
    if not canonical:
        return [_finding("canonical-missing", "indexability", Severity.MEDIUM,
                         "No <link rel=\"canonical\"> found.",
                         "Add a self-referencing canonical link to avoid duplicate-content ambiguity.")]
    page_host = (urlsplit(ctx.fetch.final_url).hostname or "").lower()
    canonical_host = (urlsplit(canonical).hostname or "").lower()
    if canonical_host != page_host:
        return [_finding("canonical-off-host", "indexability", Severity.HIGH,
                         f"Canonical {canonical} points to host {canonical_host!r}, page host is {page_host!r}.",
                         "Point the canonical at this site unless the content is intentionally syndicated.")]
    return []


@page_check
def check_noindex(ctx: PageContext) -> list[Finding]:
    robots = ctx.facts.robots_meta or ""
    if "noindex" in [d.strip().lower() for d in robots.split(",")]:
        return [_finding("noindex", "indexability", Severity.CRITICAL,
                         f"Robots meta tag is {robots!r}; the page will not be indexed.",
                         "Remove noindex if the page should appear in search results.")]
    return []


@page_check
def check_viewport(ctx: PageContext) -> list[Finding]:
    if not ctx.facts.viewport:
        return [_finding("viewport-missing", "mobile", Severity.MEDIUM, "No viewport meta tag found.",
                         "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.")]
    return []


@page_check
def check_image_alt(ctx: PageContext) -> list[Finding]:
    missing = [i.src for i in ctx.facts.images if i.alt is None]
    if not missing:
        return []
    return [_finding("image-alt-missing", "accessibility", Severity.MEDIUM,
                     f"{len(missing)} of {len(ctx.facts.images)} images lack an alt attribute: {_sample(missing)}",
                     "Add descriptive alt text (or alt=\"\" for purely decorative images).")]


@page_check
def check_https(ctx: PageContext) -> list[Finding]:
    if urlsplit(ctx.fetch.final_url).scheme != "https":
        return [_finding("non-https", "security", Severity.HIGH,
                         f"Page is served over HTTP: {ctx.fetch.final_url}",
                         "Serve the page over HTTPS and redirect HTTP to HTTPS.")]
    return []


@page_check
def check_redirect_chain(ctx: PageContext) -> list[Finding]:
    hops = len(ctx.fetch.redirect_chain)
    if hops <= 1:
        return []
    chain = " -> ".join([h.url for h in ctx.fetch.redirect_chain] + [ctx.fetch.final_url])
    return [_finding("redirect-chain", "performance", Severity.MEDIUM,
                     f"{hops} redirect hops: {chain}",
                     "Redirect directly to the final URL in a single hop.")]


@page_check
def check_slow_response(ctx: PageContext) -> list[Finding]:
    if ctx.fetch.response_time > ctx.slow_response_seconds:
        return [_finding("slow-response", "performance", Severity.MEDIUM,
                         f"Response took {ctx.fetch.response_time:.2f}s (threshold {ctx.slow_response_seconds:.1f}s).",
                         "Improve server response time with caching, a CDN or faster backend work.")]
    return []


@page_check
def check_json_ld(ctx: PageContext) -> list[Finding]:
    bad = [b for b in ctx.facts.json_ld if not b.parsed_ok]
    if not bad:
        return []
    return [_finding("json-ld-invalid", "structured-data", Severity.MEDIUM,
                     f"{len(bad)} JSON-LD block(s) failed to parse: {_sample([b.raw[:60] for b in bad])}",
                     "Fix the JSON syntax of the structured data blocks and validate them.")]


@page_check
def check_open_graph(ctx: PageContext) -> list[Finding]:
    missing = [t for t in REQUIRED_OG_TAGS if t not in ctx.facts.open_graph]
    if not missing:
        return []
    return [_finding("og-missing", "social", Severity.LOW,
                     f"Missing Open Graph tags: {', '.join(missing)}",
                     "Add the missing Open Graph tags so shared links render rich previews.")]

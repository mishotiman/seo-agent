"""Site-level synthesis: a model-written, prioritized list of top actions.

The model sees aggregated findings (counts per check plus example URLs), never page text.
Only runs in site mode.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from seo_audit.llm import LLMClient, LLMError
from seo_audit.schema import CrawlSummary, Finding, Mode, PageResult, ReportError

MAX_EXAMPLE_URLS = 3
MAX_ACTIONS = 10

SYSTEM_PROMPT = (
    "You are a senior SEO consultant. You receive aggregated audit results for a website: for "
    "each check, how many pages are affected, its severity, and a few example URLs. Identify "
    "cross-page patterns and return the most important actions, highest priority first, each one "
    "a single concrete sentence. Weigh severity and breadth (pages affected). Return "
    f"{{\"top_actions\": [...]}} with at most {MAX_ACTIONS} items."
)


class SiteSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_actions: list[str] = Field(default_factory=list, description="Prioritized actions, most important first")


@dataclass
class SynthesisResult:
    site_summary: list[str] = field(default_factory=list)
    skipped: bool = False
    error: ReportError | None = None


def aggregate_findings(pages: list[PageResult], site_findings: list[Finding]) -> list[dict[str, object]]:
    """Counts per check id with example URLs; sorted by severity then breadth."""
    order = ["critical", "high", "medium", "low", "info"]
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for page in pages:
        for f in page.findings:
            groups[(f.id, f.severity.value, f.source.value)].append(page.url)
    rows: list[dict[str, object]] = [
        {"check": check, "severity": severity, "source": source, "pages_affected": len(urls),
         "example_urls": urls[:MAX_EXAMPLE_URLS]}
        for (check, severity, source), urls in groups.items()
    ]
    for f in site_findings:
        rows.append({"check": f.id, "severity": f.severity.value, "source": f.source.value,
                     "scope": "site-wide", "detail": f.evidence[:300]})
    rows.sort(key=lambda r: (order.index(str(r["severity"])), -int(str(r.get("pages_affected", 0)))))
    return rows


def build_prompt(pages: list[PageResult], site_findings: list[Finding], crawl: CrawlSummary) -> str:
    payload = {
        "pages_crawled": crawl.pages_crawled,
        "pages_skipped": crawl.pages_skipped,
        "crawl_limit_hit": crawl.limit_hit,
        "checks": aggregate_findings(pages, site_findings),
    }
    return f"Aggregated audit results:\n{json.dumps(payload, indent=2)}"


def synthesize_site(
    llm: LLMClient,
    mode: Mode,
    pages: list[PageResult],
    site_findings: list[Finding],
    crawl: CrawlSummary,
) -> SynthesisResult:
    """Produce site_summary; skipped in page mode, never raises on model failure."""
    if mode != Mode.SITE:
        return SynthesisResult(skipped=True)
    try:
        response = llm.complete(SYSTEM_PROMPT, build_prompt(pages, site_findings, crawl), SiteSynthesis)
    except LLMError as exc:
        return SynthesisResult(error=ReportError(stage="site_synthesis", message=str(exc)))
    actions = [a.strip() for a in response.top_actions if a.strip()]
    return SynthesisResult(site_summary=actions[:MAX_ACTIONS])

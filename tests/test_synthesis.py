import json
from types import SimpleNamespace
from typing import Any

from seo_audit.llm import LLMClient
from seo_audit.schema import CrawlSummary, Finding, Mode, PageResult, Severity, Source
from seo_audit.synthesis import aggregate_findings, build_prompt, synthesize_site


def _f(id: str, severity: Severity = Severity.MEDIUM, evidence: str = "e") -> Finding:
    return Finding(id=id, category="c", severity=severity, source=Source.CODE, evidence=evidence, recommendation="r")


PAGES = [
    PageResult(url=f"https://example.com/p{i}", findings=[_f("title-missing", Severity.HIGH), _f("og-missing", Severity.LOW)])
    for i in range(5)
]
SITE = [_f("sitemap-missing", Severity.MEDIUM, "no sitemap")]
CRAWL = CrawlSummary(pages_crawled=5, pages_skipped=2, limit_hit="max_pages")


class Fake:
    def __init__(self, content: str | None = None, raises: bool = False) -> None:
        self.content, self.raises = content, raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("provider down")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))], usage=None
        )


def _llm(fake: Fake) -> LLMClient:
    return LLMClient("fake/model", completion=fake)


def test_populates_site_summary_in_order() -> None:
    fake = Fake(json.dumps({"top_actions": ["Add titles everywhere", " Publish a sitemap ", "  "]}))
    result = synthesize_site(_llm(fake), Mode.SITE, PAGES, SITE, CRAWL)
    assert result.error is None and not result.skipped
    assert result.site_summary == ["Add titles everywhere", "Publish a sitemap"]


def test_skipped_in_page_mode() -> None:
    fake = Fake(json.dumps({"top_actions": ["x"]}))
    result = synthesize_site(_llm(fake), Mode.PAGE, PAGES, SITE, CRAWL)
    assert result.skipped and result.site_summary == [] and result.error is None
    assert fake.calls == []


def test_model_failure_leaves_summary_empty_with_error() -> None:
    result = synthesize_site(_llm(Fake(raises=True)), Mode.SITE, PAGES, SITE, CRAWL)
    assert result.site_summary == []
    assert result.error is not None and result.error.stage == "site_synthesis"


def test_invalid_output_leaves_summary_empty_with_error() -> None:
    result = synthesize_site(_llm(Fake("not json")), Mode.SITE, PAGES, SITE, CRAWL)
    assert result.site_summary == [] and result.error is not None


def test_aggregation_counts_and_examples() -> None:
    rows = aggregate_findings(PAGES, SITE)
    title = next(r for r in rows if r["check"] == "title-missing")
    assert title["pages_affected"] == 5
    assert title["example_urls"] == [f"https://example.com/p{i}" for i in range(3)]
    assert rows[0]["check"] == "title-missing"  # high severity sorts first


def test_prompt_is_aggregated_not_page_text() -> None:
    prompt = build_prompt(PAGES, SITE, CRAWL)
    assert "title-missing" in prompt and "pages_affected" in prompt and "no sitemap" in prompt
    assert "max_pages" in prompt
    assert "https://example.com/p4" not in prompt  # only first 3 examples

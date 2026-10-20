import os
from pathlib import Path

from seo_audit.report import render_markdown
from seo_audit.schema import (
    AuditReport,
    CrawlSummary,
    Finding,
    Mode,
    PageResult,
    ReportError,
    Severity,
    Source,
    TokenUsage,
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "report.md"


def finding(id: str, severity: Severity, source: Source = Source.CODE, **kw: object) -> Finding:
    data: dict[str, object] = {"id": id, "category": "content" if source == Source.MODEL else "meta",
                               "severity": severity, "source": source, "evidence": f"Evidence for {id}",
                               "recommendation": f"Fix {id}."}
    data.update(kw)
    return Finding.model_validate(data)


def sample_report() -> AuditReport:
    return AuditReport(
        url="https://example.com/",
        mode=Mode.SITE,
        model="openai/gpt-4o-mini",
        crawl=CrawlSummary(pages_crawled=2, pages_skipped=3, limit_hit="max_pages",
                           skipped_urls=["https://example.com/c"]),
        pages=[
            PageResult(url="https://example.com/", status_code=200, findings=[
                finding("title-missing", Severity.HIGH),
                finding("intro-thin", Severity.LOW, Source.MODEL, confidence=0.6,
                        evidence="Quote:\n  \"Widgets\""),
            ]),
            PageResult(url="https://example.com/a", status_code=200, findings=[
                finding("meta-description-missing", Severity.MEDIUM),
                finding("title-weak", Severity.HIGH, Source.MODEL, confidence=0.8),
            ]),
        ],
        site_findings=[finding("broken-internal-links", Severity.HIGH, category="links")],
        site_summary=["Add titles to every page", "Fix broken internal links"],
        dropped_model_findings=1,
        errors=[ReportError(stage="model_page_checks", message="provider down", url="https://example.com/b")],
        token_usage=TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
    )


def test_snapshot() -> None:
    rendered = render_markdown(sample_report())
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(rendered, encoding="utf-8", newline="\n")
    assert rendered == SNAPSHOT.read_text(encoding="utf-8")


def test_grouped_by_severity_then_page() -> None:
    text = render_markdown(sample_report())
    order = [text.index(s) for s in ("### High (3)", "#### Site-wide", "#### https://example.com/\n",
                                     "#### https://example.com/a", "### Medium (1)", "### Low (1)")]
    assert order == sorted(order)
    assert "### Critical" not in text and "### Info" not in text


def test_empty_report() -> None:
    text = render_markdown(AuditReport(url="https://example.com/", mode=Mode.PAGE))
    assert "No findings." in text and "- Limit hit: none" in text and "- Model: none" in text
    assert "## Top actions" not in text and "## Errors" not in text

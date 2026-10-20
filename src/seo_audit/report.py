"""Markdown rendering of an AuditReport: summary, crawl limits, findings by severity then page."""

from __future__ import annotations

from seo_audit.schema import AuditReport, Finding, Severity, Source

SITE_WIDE = "Site-wide"


def _inline(text: str) -> str:
    return " ".join(text.split())


def _finding_line(finding: Finding) -> str:
    label = "code" if finding.source == Source.CODE else f"model, confidence {finding.confidence:.2f}"
    line = f"- **{finding.id}** [{label}] ({finding.category}): {_inline(finding.evidence)}"
    if finding.recommendation.strip():
        line += f"\n  - Fix: {_inline(finding.recommendation)}"
    return line


def _grouped(report: AuditReport) -> dict[Severity, dict[str, list[Finding]]]:
    groups: dict[Severity, dict[str, list[Finding]]] = {s: {} for s in Severity}
    for finding in report.site_findings:
        groups[finding.severity].setdefault(SITE_WIDE, []).append(finding)
    for page in report.pages:
        for finding in page.findings:
            groups[finding.severity].setdefault(page.url, []).append(finding)
    return groups


def render_markdown(report: AuditReport) -> str:
    groups = _grouped(report)
    all_findings = [f for by_page in groups.values() for fs in by_page.values() for f in fs]
    code_count = sum(f.source == Source.CODE for f in all_findings)

    lines = [f"# SEO audit report: {report.url}", ""]
    lines += [
        "## Summary",
        "",
        f"- Mode: {report.mode.value}",
        f"- Model: {report.model or 'none'}",
        f"- Pages audited: {len(report.pages)}",
        f"- Findings: {len(all_findings)} ({code_count} code, {len(all_findings) - code_count} model)",
    ]
    counts = ", ".join(f"{s.value} {sum(len(fs) for fs in groups[s].values())}" for s in Severity)
    lines.append(f"- By severity: {counts}")
    if report.dropped_model_findings:
        lines.append(f"- Ungrounded model findings dropped: {report.dropped_model_findings}")
    usage = report.token_usage
    lines.append(f"- Tokens: {usage.total_tokens} ({usage.prompt_tokens} prompt, {usage.completion_tokens} completion)")

    crawl = report.crawl
    lines += [
        "",
        "## Crawl",
        "",
        f"- Pages crawled: {crawl.pages_crawled}",
        f"- Pages skipped: {crawl.pages_skipped}",
        f"- Limit hit: {crawl.limit_hit or 'none'}",
    ]

    if report.site_summary:
        lines += ["", "## Top actions", ""]
        lines += [f"{i}. {_inline(action)}" for i, action in enumerate(report.site_summary, 1)]

    lines += ["", "## Findings"]
    if not all_findings:
        lines += ["", "No findings."]
    for severity in Severity:
        by_page = groups[severity]
        if not by_page:
            continue
        total = sum(len(fs) for fs in by_page.values())
        lines += ["", f"### {severity.value.capitalize()} ({total})"]
        for page, findings in by_page.items():
            lines += ["", f"#### {page}", ""]
            lines += [_finding_line(f) for f in findings]

    if report.errors:
        lines += ["", "## Errors", ""]
        for error in report.errors:
            where = f" ({error.url})" if error.url else ""
            lines.append(f"- {error.stage}{where}: {_inline(error.message)}")

    return "\n".join(lines) + "\n"

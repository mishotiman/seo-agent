"""Model-judged page checks: subjective assessments, grounded by verified evidence quotes.

The model sees extracted facts and truncated visible text, never raw HTML. Every finding must
carry a verbatim evidence quote; findings whose quote is not found in the page are dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from seo_audit.extractor import PageFacts
from seo_audit.llm import LLMClient, LLMError
from seo_audit.schema import Finding, ReportError, Severity, Source

SYSTEM_PROMPT = (
    "You are an SEO reviewer. Judge title and meta description quality, content quality, and "
    "whether the page matches a clear search intent. Report only real problems. For every "
    "finding, `evidence_quote` must be copied verbatim from the page title, meta description or "
    "page text provided. Use severity one of critical, high, medium, low, info. `confidence` is "
    "your certainty from 0 to 1. Return {\"findings\": [...]}; use an empty list if all is fine."
)


class ModelFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="Short kebab-case id, e.g. 'title-vague'")
    category: str = Field(min_length=1)
    severity: Severity
    evidence_quote: str = Field(min_length=1, description="Verbatim quote from the page")
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class ModelFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[ModelFinding] = []


@dataclass
class ModelCheckResult:
    findings: list[Finding] = field(default_factory=list)
    dropped: int = 0
    error: ReportError | None = None


def build_prompt(facts: PageFacts) -> str:
    """Prompt from extracted facts and truncated text only; no raw HTML."""
    summary = {
        "url": facts.url,
        "title": facts.title,
        "meta_description": facts.meta_description,
        "headings": [{"level": h.level, "text": h.text} for h in facts.headings],
        "canonical": facts.canonical,
        "image_count": len(facts.images),
        "internal_link_count": len(facts.internal_links),
        "external_link_count": len(facts.external_links),
    }
    note = " (truncated)" if facts.text_truncated else ""
    return f"Page facts:\n{json.dumps(summary, indent=2)}\n\nPage text{note}:\n{facts.text}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_in_page(quote: str, facts: PageFacts) -> bool:
    """True if the quote appears (whitespace/case-insensitive) in title, description or text."""
    q = _norm(quote)
    if not q:
        return False
    corpus = " ".join(_norm(part) for part in (facts.title, facts.meta_description, facts.text) if part)
    return q in corpus


def run_model_checks(llm: LLMClient, facts: PageFacts) -> ModelCheckResult:
    """Run the model-judged checks for one page; never raises on model failure."""
    try:
        response = llm.complete(SYSTEM_PROMPT, build_prompt(facts), ModelFindings)
    except LLMError as exc:
        return ModelCheckResult(error=ReportError(stage="model_page_checks", message=str(exc), url=facts.url))
    result = ModelCheckResult()
    for item in response.findings:
        if not quote_in_page(item.evidence_quote, facts):
            result.dropped += 1
            continue
        result.findings.append(
            Finding(
                id=item.id,
                category=item.category,
                severity=item.severity,
                source=Source.MODEL,
                evidence=item.evidence_quote,
                recommendation=item.recommendation,
                confidence=item.confidence,
            )
        )
    return result

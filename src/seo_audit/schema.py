"""Output schema: pydantic models for the audit report and JSON Schema export."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema" / "audit-report.schema.json"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Source(StrEnum):
    CODE = "code"
    MODEL = "model"


class Mode(StrEnum):
    PAGE = "page"
    SITE = "site"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="Stable check identifier, e.g. 'title-missing'")
    category: str = Field(min_length=1)
    severity: Severity
    source: Source
    evidence: str
    recommendation: str
    confidence: float = Field(
        1.0, ge=0, le=1, description="1.0 for deterministic code checks; model-estimated otherwise"
    )


class PageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    final_url: str | None = None
    status_code: int | None = None
    findings: list[Finding] = []


class CrawlSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages_crawled: int = Field(0, ge=0)
    pages_skipped: int = Field(0, ge=0)
    limit_hit: str | None = Field(
        None, description="Which limit stopped the crawl: max_pages, max_depth or time_budget"
    )
    skipped_urls: list[str] = []


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)


class ReportError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    message: str
    url: str | None = None


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    mode: Mode
    model: str | None = None
    crawl: CrawlSummary = CrawlSummary()
    pages: list[PageResult] = []
    site_findings: list[Finding] = []
    site_summary: list[str] = []
    dropped_model_findings: int = Field(0, ge=0)
    errors: list[ReportError] = []
    token_usage: TokenUsage = TokenUsage()


def export_json_schema() -> dict[str, Any]:
    return AuditReport.model_json_schema()


def write_json_schema(path: Path = SCHEMA_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(export_json_schema(), indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"wrote {write_json_schema()}")

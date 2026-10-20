import json

import jsonschema
import pytest
from pydantic import ValidationError

from seo_audit.schema import (
    SCHEMA_PATH,
    AuditReport,
    CrawlSummary,
    Finding,
    PageResult,
    export_json_schema,
)


def sample_report() -> AuditReport:
    return AuditReport.model_validate(
        {
            "url": "https://example.com/",
            "mode": "site",
            "model": "anthropic/claude-sonnet-5-5",
            "crawl": CrawlSummary(pages_crawled=1, pages_skipped=2, limit_hit="max_pages"),
            "pages": [
                PageResult(
                    url="https://example.com/",
                    final_url="https://example.com/",
                    status_code=200,
                    findings=[
                        Finding(
                            id="title-missing",
                            category="metadata",
                            severity="high",
                            source="code",
                            evidence="No <title> element found",
                            recommendation="Add a descriptive title",
                        ),
                        Finding(
                            id="content-thin",
                            category="content",
                            severity="medium",
                            source="model",
                            evidence="Welcome to our site",
                            recommendation="Expand the page content",
                            confidence=0.7,
                        ),
                    ],
                )
            ],
            "site_summary": ["Fix titles first"],
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )


def test_sample_report_validates_against_exported_schema() -> None:
    data = json.loads(sample_report().model_dump_json())
    jsonschema.validate(data, export_json_schema())


def test_committed_schema_file_is_up_to_date() -> None:
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == export_json_schema()
    jsonschema.validate(json.loads(sample_report().model_dump_json()), on_disk)


def test_schema_rejects_invalid_severity() -> None:
    data = json.loads(sample_report().model_dump_json())
    data["pages"][0]["findings"][0]["severity"] = "urgent"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, export_json_schema())


def test_finding_rejects_bad_source_and_confidence() -> None:
    base = {
        "id": "x",
        "category": "c",
        "severity": "low",
        "evidence": "e",
        "recommendation": "r",
    }
    with pytest.raises(ValidationError):
        Finding.model_validate({**base, "source": "human"})
    with pytest.raises(ValidationError):
        Finding.model_validate({**base, "source": "code", "confidence": 1.5})

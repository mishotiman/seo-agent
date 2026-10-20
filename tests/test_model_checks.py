import json
from types import SimpleNamespace
from typing import Any

from seo_audit.extractor import extract_facts
from seo_audit.llm import LLMClient
from seo_audit.model_checks import build_prompt, run_model_checks
from seo_audit.schema import Source

HTML = (
    "<html><head><title>Widgets</title><meta name='description' content='All about widgets'>"
    "<script>var secret_raw = 1;</script></head>"
    "<body><h1>Widgets</h1><p>We sell widgets of many kinds to everyone.</p></body></html>"
)
FACTS = extract_facts(HTML, "https://example.com/", 8000)


def _finding(quote: str, **kw: Any) -> dict[str, Any]:
    base = {"id": "title-vague", "category": "content", "severity": "medium",
            "evidence_quote": quote, "recommendation": "Be specific.", "confidence": 0.7}
    return {**base, **kw}


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


def test_valid_findings_are_model_sourced() -> None:
    fake = Fake(json.dumps({"findings": [_finding("we sell WIDGETS of many  kinds")]}))
    result = run_model_checks(_llm(fake), FACTS)
    assert result.error is None and result.dropped == 0
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.source == Source.MODEL and f.confidence == 0.7
    assert f.evidence == "we sell WIDGETS of many  kinds"


def test_title_quote_is_accepted() -> None:
    fake = Fake(json.dumps({"findings": [_finding("All about widgets")]}))
    assert len(run_model_checks(_llm(fake), FACTS).findings) == 1


def test_unverifiable_evidence_dropped_and_counted() -> None:
    payload = {"findings": [_finding("this text is invented"), _finding("Widgets of many kinds".replace("Widgets of", "widgets of"))]}
    result = run_model_checks(_llm(Fake(json.dumps(payload))), FACTS)
    assert result.dropped == 1
    assert len(result.findings) == 1


def test_model_failure_returns_error_entry() -> None:
    result = run_model_checks(_llm(Fake(raises=True)), FACTS)
    assert result.findings == [] and result.dropped == 0
    assert result.error is not None
    assert result.error.stage == "model_page_checks" and result.error.url == FACTS.url


def test_invalid_output_returns_error_entry() -> None:
    result = run_model_checks(_llm(Fake("not json")), FACTS)
    assert result.error is not None


def test_prompt_has_facts_not_raw_html() -> None:
    prompt = build_prompt(FACTS)
    assert "We sell widgets" in prompt and "All about widgets" in prompt
    assert "<html" not in prompt and "<script" not in prompt and "secret_raw" not in prompt

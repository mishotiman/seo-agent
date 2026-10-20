from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from seo_audit.llm import LLMClient, LLMError


class Answer(BaseModel):
    verdict: str
    score: int


def _resp(content: str, prompt: int = 10, completion: int = 5) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        ),
    )


class FakeModel:
    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _resp(self.contents.pop(0))


def test_valid_response() -> None:
    fake = FakeModel('{"verdict": "ok", "score": 3}')
    client = LLMClient("openai/gpt-4o", 0.2, completion=fake)
    result = client.complete("sys", "hi", Answer)
    assert result == Answer(verdict="ok", score=3)
    assert fake.calls[0]["model"] == "openai/gpt-4o"
    assert fake.calls[0]["temperature"] == 0.2
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert client.usage.total_tokens == 15


def test_fenced_json_accepted() -> None:
    client = LLMClient("m", completion=FakeModel('```json\n{"verdict": "a", "score": 1}\n```'))
    assert client.complete("s", "p", Answer).score == 1


def test_malformed_then_valid_retries_once_and_sums_usage() -> None:
    fake = FakeModel("not json", '{"verdict": "fixed", "score": 7}')
    client = LLMClient("m", completion=fake)
    assert client.complete("s", "p", Answer).verdict == "fixed"
    assert len(fake.calls) == 2
    assert fake.calls[1]["messages"][-1]["role"] == "user"
    assert client.usage.prompt_tokens == 20
    assert client.usage.completion_tokens == 10


def test_persistent_failure_raises() -> None:
    fake = FakeModel("nope", '{"verdict": "x"}')
    client = LLMClient("m", completion=fake)
    with pytest.raises(LLMError):
        client.complete("s", "p", Answer)
    assert len(fake.calls) == 2


def test_provider_error_wrapped() -> None:
    def boom(**_: Any) -> Any:
        raise RuntimeError("rate limited")

    with pytest.raises(LLMError, match="rate limited"):
        LLMClient("m", completion=boom).complete("s", "p", Answer)

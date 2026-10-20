"""Single model interface: every LLM call goes through `LLMClient.complete` (LiteLLM)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .schema import TokenUsage

T = TypeVar("T", bound=BaseModel)

# A completion function takes LiteLLM-style kwargs and returns a LiteLLM-style response.
CompletionFn = Callable[..., Any]

MAX_ATTEMPTS = 2  # first try plus one retry on malformed output


class LLMError(Exception):
    """Raised when the model call fails or output stays invalid after the retry."""


def _litellm_completion(**kwargs: Any) -> Any:
    import litellm

    return litellm.completion(**kwargs)


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


class LLMClient:
    def __init__(
        self,
        model: str,
        temperature: float = 0.0,
        completion: CompletionFn | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._completion = completion or _litellm_completion
        self._usage = TokenUsage()
        self._lock = threading.Lock()  # complete() may run in worker threads

    @property
    def usage(self) -> TokenUsage:
        return self._usage.model_copy()

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or 0) or prompt + completion
        with self._lock:
            self._usage = TokenUsage(
                prompt_tokens=self._usage.prompt_tokens + prompt,
                completion_tokens=self._usage.completion_tokens + completion,
                total_tokens=self._usage.total_tokens + total,
            )

    def complete(self, system: str, prompt: str, response_model: type[T]) -> T:
        """Ask for JSON, validate against `response_model`, retry once if malformed."""
        schema = json.dumps(response_model.model_json_schema())
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": f"{system}\n\nRespond with a single JSON object matching this "
                f"JSON Schema, and nothing else:\n{schema}",
            },
            {"role": "user", "content": prompt},
        ]
        last_error = ""
        for _ in range(MAX_ATTEMPTS):
            try:
                response = self._completion(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                )
            except Exception as exc:  # provider/network errors are not retried here
                raise LLMError(f"Model call failed: {exc}") from exc
            self._record_usage(response)
            content = response.choices[0].message.content or ""
            try:
                return response_model.model_validate_json(_strip_fence(content))
            except ValidationError as exc:
                last_error = str(exc)
                messages = messages[:2] + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "That was not valid JSON for the schema "
                        f"({last_error[:500]}). Reply again with only the corrected JSON object.",
                    },
                ]
        raise LLMError(f"Model returned invalid output after retry: {last_error[:500]}")

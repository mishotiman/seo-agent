"""Page fetcher: one bounded HTTP GET that always returns a result, never raises."""

from __future__ import annotations

import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

USER_AGENT = "seo-audit-agent/0.1 (+https://github.com/seo-audit; static HTML audit bot)"
DEFAULT_MAX_BYTES = 2_000_000
MAX_REDIRECTS = 10
HTML_TYPES = ("text/html", "application/xhtml+xml")

FetchError = Literal["timeout", "connection", "too_many_redirects", "too_large", "not_html", "other"]


class RedirectHop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    status_code: int


class FetchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    final_url: str
    status_code: int | None = None
    redirect_chain: list[RedirectHop] = Field(default_factory=list)
    response_time: float = 0.0
    content_type: str | None = None
    body: str | None = None
    is_html: bool = False
    error: FetchError | None = None
    error_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and self.status_code < 400


def make_client(timeout: float, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """Client with the audit User-Agent, bounded redirects and the configured timeout."""
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        timeout=timeout,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        transport=transport,
    )


async def fetch_page(
    client: httpx.AsyncClient, url: str, max_bytes: int = DEFAULT_MAX_BYTES
) -> FetchResult:
    """GET `url`. Failures (timeouts, connection errors, non-HTML, oversize) are returned in the result."""
    result = FetchResult(url=url, final_url=url)
    start = time.monotonic()
    try:
        async with client.stream("GET", url) as response:
            result.response_time = time.monotonic() - start
            result.final_url = str(response.url)
            result.status_code = response.status_code
            result.redirect_chain = [
                RedirectHop(url=str(r.url), status_code=r.status_code) for r in response.history
            ]
            content_type = response.headers.get("content-type")
            result.content_type = content_type.split(";")[0].strip().lower() if content_type else None
            result.is_html = result.content_type in HTML_TYPES
            if not result.is_html:
                result.error = "not_html"
                result.error_detail = f"Content-Type is {result.content_type or 'missing'}"
                return result
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    result.error = "too_large"
                    result.error_detail = f"Response exceeds {max_bytes} bytes"
                    return result
                chunks.append(chunk)
            result.body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    except httpx.TimeoutException as exc:
        result.error, result.error_detail = "timeout", str(exc) or type(exc).__name__
    except httpx.TooManyRedirects as exc:
        result.error, result.error_detail = "too_many_redirects", str(exc)
    except httpx.TransportError as exc:
        result.error, result.error_detail = "connection", str(exc) or type(exc).__name__
    except httpx.HTTPError as exc:
        result.error, result.error_detail = "other", str(exc) or type(exc).__name__
    result.response_time = time.monotonic() - start if result.status_code is None else result.response_time
    return result

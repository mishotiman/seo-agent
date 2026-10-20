import asyncio
from collections.abc import Callable

import httpx

from seo_audit.fetcher import USER_AGENT, FetchResult, fetch_page, make_client

HTML = "<html><head><title>Hi</title></head><body>ok</body></html>"


def run(handler: Callable[[httpx.Request], httpx.Response], url: str, **kw: int) -> FetchResult:
    async def go() -> FetchResult:
        async with make_client(5, httpx.MockTransport(handler)) as client:
            return await fetch_page(client, url, **kw)

    return asyncio.run(go())


def test_success_sends_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})

    r = run(handler, "https://example.com/")
    assert seen == [USER_AGENT]
    assert r.ok and r.is_html and r.status_code == 200
    assert r.body == HTML
    assert r.final_url == "https://example.com/"
    assert r.content_type == "text/html"
    assert r.redirect_chain == [] and r.response_time >= 0


def test_redirect_chain_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        if request.url.path == "/b":
            return httpx.Response(302, headers={"location": "/c"})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    r = run(handler, "https://example.com/a")
    assert r.final_url == "https://example.com/c"
    assert [(h.url, h.status_code) for h in r.redirect_chain] == [
        ("https://example.com/a", 301),
        ("https://example.com/b", 302),
    ]


def test_404_is_a_result() -> None:
    r = run(lambda req: httpx.Response(404, text=HTML, headers={"content-type": "text/html"}),
            "https://example.com/missing")
    assert r.status_code == 404 and not r.ok and r.error is None


def test_timeout_returned_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    r = run(handler, "https://example.com/")
    assert r.error == "timeout" and r.status_code is None and not r.ok


def test_connection_error_returned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    r = run(handler, "https://example.com/")
    assert r.error == "connection"


def test_non_html_returned() -> None:
    r = run(lambda req: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
            "https://example.com/f.pdf")
    assert r.error == "not_html" and not r.is_html and r.body is None
    assert r.status_code == 200 and r.content_type == "application/pdf"


def test_max_size_enforced() -> None:
    r = run(lambda req: httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/html"}),
            "https://example.com/", max_bytes=1000)
    assert r.error == "too_large" and r.body is None


def test_redirect_loop() -> None:
    r = run(lambda req: httpx.Response(302, headers={"location": "/"}), "https://example.com/")
    assert r.error == "too_many_redirects"

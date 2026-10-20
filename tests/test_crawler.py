import asyncio

import httpx

from seo_audit.config import CrawlConfig
from seo_audit.crawler import CrawlResult, crawl
from seo_audit.fetcher import make_client


def page(*links: str) -> httpx.Response:
    body = "".join(f'<a href="{href}">x</a>' for href in links)
    return httpx.Response(
        200, text=f"<html><head><title>t</title></head><body>{body}</body></html>",
        headers={"content-type": "text/html"},
    )


def run(
    site: dict[str, tuple[str, ...]], start: str = "https://example.com/", **cfg: float | bool | int
) -> CrawlResult:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        return page(*site[key]) if key in site else httpx.Response(404, text="nope")

    async def go() -> CrawlResult:
        async with make_client(5, httpx.MockTransport(handler)) as client:
            return await crawl(client, start, CrawlConfig(**cfg))  # type: ignore[arg-type]

    return asyncio.run(go())


SITE = {
    "https://example.com/": ("/a", "/b", "https://other.com/x", "https://blog.example.com/"),
    "https://example.com/a": ("/c", "/"),
    "https://example.com/b": (),
    "https://example.com/c": ("/d",),
    "https://example.com/d": (),
    "https://blog.example.com/": (),
}


def urls(result: CrawlResult) -> list[str]:
    return [p.url for p in result.pages]


def test_bfs_order_same_host_only() -> None:
    r = run(SITE)
    assert urls(r) == [f"https://example.com/{p}" for p in ("", "a", "b", "c", "d")]
    assert r.summary.pages_crawled == 5 and r.summary.limit_hit is None
    assert [p.depth for p in r.pages] == [0, 1, 1, 2, 3]


def test_max_pages_limit() -> None:
    r = run(SITE, max_pages=2)
    assert urls(r) == ["https://example.com/", "https://example.com/a"]
    assert r.summary.limit_hit == "max_pages"
    assert "https://example.com/b" in r.summary.skipped_urls
    assert r.summary.pages_skipped == len(r.summary.skipped_urls)


def test_max_depth_limit() -> None:
    r = run(SITE, max_depth=1)
    assert urls(r) == ["https://example.com/", "https://example.com/a", "https://example.com/b"]
    assert r.summary.limit_hit == "max_depth"
    assert r.summary.skipped_urls == ["https://example.com/c"]


def test_time_budget_limit() -> None:
    ticks = iter(range(0, 1000, 10))
    async def go() -> CrawlResult:
        async with make_client(5, httpx.MockTransport(lambda r: page("/a", "/b"))) as client:
            return await crawl(
                client, "https://example.com/", CrawlConfig(total_time_budget=25),
                clock=lambda: float(next(ticks)),
            )

    r = asyncio.run(go())
    assert r.summary.limit_hit == "time_budget"
    assert r.summary.pages_crawled < 3 and r.summary.pages_skipped >= 1


def test_subdomains_only_when_configured() -> None:
    assert "https://blog.example.com/" not in urls(run(SITE))
    assert "https://blog.example.com/" in urls(run(SITE, include_subdomains=True))
    assert "https://other.com/x" not in urls(run(SITE, include_subdomains=True))


def test_concurrency_is_capped() -> None:
    active = peak = 0

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            if request.url.path == "/":
                return page(*[f"/p{i}" for i in range(8)])
            return page()

    async def go() -> CrawlResult:
        async with make_client(5, SlowTransport()) as client:
            return await crawl(client, "https://example.com/", CrawlConfig(concurrency=2))

    r = asyncio.run(go())
    assert r.summary.pages_crawled == 9
    assert peak == 2


def test_error_pages_recorded_not_followed() -> None:
    r = run({"https://example.com/": ("/missing",)})
    assert [p.fetch.status_code for p in r.pages] == [200, 404]
    assert r.pages[1].facts is None

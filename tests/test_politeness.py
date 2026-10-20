import asyncio

import httpx
import pytest

from seo_audit.config import CrawlConfig
from seo_audit.crawler import CrawlResult, crawl
from seo_audit.fetcher import make_client
from seo_audit.urls import normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/a#frag", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("https://example.com/a?utm_source=x&b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a?gclid=1&fbclid=2", "https://example.com/a"),
        ("https://example.com/a?UTM_Medium=x", "https://example.com/a"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def html(*links: str) -> httpx.Response:
    body = "".join(f'<a href="{h}">x</a>' for h in links)
    return httpx.Response(
        200, text=f"<html><head><title>t</title></head><body>{body}</body></html>",
        headers={"content-type": "text/html"},
    )


def run(
    routes: dict[str, httpx.Response], sleeps: list[float] | None = None, **cfg: float | bool | int
) -> tuple[CrawlResult, list[str]]:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return routes.get(request.url.path, httpx.Response(404, text="nope"))

    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    async def go() -> CrawlResult:
        async with make_client(5, httpx.MockTransport(handler)) as client:
            return await crawl(client, "https://example.com/", CrawlConfig(**cfg), sleep=fake_sleep)  # type: ignore[arg-type]

    return asyncio.run(go()), requested


def text(body: str) -> httpx.Response:
    return httpx.Response(200, text=body)


def test_robots_disallow_skips_and_records() -> None:
    r, requested = run({
        "/robots.txt": text("User-agent: *\nDisallow: /private"),
        "/": html("/private/x", "/ok"),
        "/ok": html(),
        "/private/x": html(),
    })
    assert [p.url for p in r.pages] == ["https://example.com/", "https://example.com/ok"]
    assert r.summary.skipped_urls == ["https://example.com/private/x"]
    assert "/private/x" not in requested


def test_robots_can_be_disabled() -> None:
    r, _ = run({
        "/robots.txt": text("User-agent: *\nDisallow: /"),
        "/": html(),
    }, respect_robots=False)
    assert r.summary.pages_crawled == 1


def test_crawl_delay_honored() -> None:
    sleeps: list[float] = []
    run({
        "/robots.txt": text("User-agent: *\nCrawl-delay: 2"),
        "/": html("/a", "/b"), "/a": html(), "/b": html(),
    }, sleeps, concurrency=1)
    # fake sleep doesn't advance the clock, so each request waits one more delay slot
    assert len(sleeps) == 2 and sleeps[0] == pytest.approx(2, abs=0.1) and sleeps[1] == pytest.approx(4, abs=0.1)


def test_no_crawl_delay_means_no_sleep() -> None:
    sleeps: list[float] = []
    run({"/": html("/a"), "/a": html()}, sleeps)
    assert sleeps == []


def test_duplicate_urls_collapse() -> None:
    r, requested = run({
        "/": html("/a/", "/a?utm_source=x", "/a#top", "/A".lower()),
        "/a": html(),
    })
    assert [p.url for p in r.pages] == ["https://example.com/", "https://example.com/a"]
    assert requested.count("/a") == 1


def test_sitemap_seeds_urls() -> None:
    r, _ = run({
        "/sitemap.xml": text(
            "<urlset><url><loc>https://example.com/hidden</loc></url>"
            "<url><loc>https://other.com/x</loc></url></urlset>"
        ),
        "/": html(), "/hidden": html(),
    })
    assert [p.url for p in r.pages] == ["https://example.com/", "https://example.com/hidden"]


def test_sitemap_index_followed_and_robots_declared() -> None:
    r, _ = run({
        "/robots.txt": text("Sitemap: https://example.com/index.xml"),
        "/index.xml": text("<sitemapindex><sitemap><loc>https://example.com/s1.xml</loc></sitemap></sitemapindex>"),
        "/s1.xml": text("<urlset><url><loc>https://example.com/p</loc></url></urlset>"),
        "/": html(), "/p": html(),
    })
    assert "https://example.com/p" in [p.url for p in r.pages]


def test_sitemap_disabled() -> None:
    r, _ = run({
        "/sitemap.xml": text("<urlset><url><loc>https://example.com/hidden</loc></url></urlset>"),
        "/": html(),
    }, use_sitemap=False)
    assert r.summary.pages_crawled == 1

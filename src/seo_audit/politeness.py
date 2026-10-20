"""robots.txt rules and sitemap.xml seeding."""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from seo_audit.fetcher import USER_AGENT

_LOC = re.compile(r"<(?:\w+:)?loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</(?:\w+:)?loc>", re.S | re.I)
MAX_SITEMAPS = 5
MAX_SITEMAP_URLS = 1000


class Robots:
    """Parsed robots.txt; a missing or unreadable file allows everything."""

    def __init__(self, text: str | None = None) -> None:
        self._parser = RobotFileParser()
        self._parser.parse((text or "").splitlines())
        self.sitemaps: list[str] = list(self._parser.site_maps() or [])

    def allowed(self, url: str) -> bool:
        return self._parser.can_fetch(USER_AGENT, url)

    def crawl_delay(self) -> float:
        return float(self._parser.crawl_delay(USER_AGENT) or 0)


async def _get_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return None
    return response.text if response.status_code == 200 else None


async def load_robots(client: httpx.AsyncClient, start_url: str) -> Robots:
    parts = urlsplit(start_url)
    return Robots(await _get_text(client, f"{parts.scheme}://{parts.netloc}/robots.txt"))


async def load_sitemap_urls(client: httpx.AsyncClient, start_url: str, robots: Robots) -> list[str]:
    """URLs listed in sitemap.xml (or robots-declared sitemaps), following sitemap indexes."""
    parts = urlsplit(start_url)
    queue = robots.sitemaps or [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
    urls: list[str] = []
    fetched = 0
    while queue and fetched < MAX_SITEMAPS and len(urls) < MAX_SITEMAP_URLS:
        sitemap_url = queue.pop(0)
        fetched += 1
        text = await _get_text(client, sitemap_url)
        if text is None:
            continue
        locs = _LOC.findall(text)
        if re.search(r"<(?:\w+:)?sitemapindex", text, re.I):
            queue.extend(locs)
        else:
            urls.extend(locs)
    return urls[:MAX_SITEMAP_URLS]

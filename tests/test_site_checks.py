import asyncio

import httpx

from seo_audit.crawler import CrawledPage
from seo_audit.extractor import extract_facts
from seo_audit.fetcher import FetchResult
from seo_audit.schema import Source
from seo_audit.site_checks import SITE_CHECKS, SiteContext, SiteFile, probe_site_files, run_site_checks, site_check

ROBOTS = SiteFile(200, "User-agent: *\nAllow: /\n")
SITEMAP = SiteFile(200, '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://e.com/</loc></url></urlset>')


def mk(path: str, title: str = "", desc: str = "", links: tuple[str, ...] = (), status: int = 200) -> CrawledPage:
    url = f"https://e.com{path}"
    head = (f"<title>{title}</title>" if title else "") + (f'<meta name="description" content="{desc}">' if desc else "")
    html = f"<html><head>{head}</head><body>{''.join(f'<a href=\"{h}\">x</a>' for h in links)}</body></html>"
    fetch = FetchResult(url=url, final_url=url, status_code=status, body=html, is_html=True)
    return CrawledPage(url=url, depth=0, fetch=fetch, facts=extract_facts(html, url, 1000))


def run(pages: list[CrawledPage], robots: SiteFile = ROBOTS, sitemap: SiteFile = SITEMAP) -> dict[str, str]:
    findings = run_site_checks(SiteContext(pages, robots, sitemap))
    for f in findings:
        assert f.source == Source.CODE and f.evidence and f.recommendation
    return {f.id: f.evidence for f in findings}


def test_clean_site_has_no_findings() -> None:
    assert run([mk("/", "Home", "d1"), mk("/a", "About", "d2", ("/",))]) == {}


def test_duplicate_titles_lists_urls() -> None:
    found = run([mk("/a", "Same", "d1"), mk("/b", "same ", "d2"), mk("/c", "Other", "d3")])
    assert set(found) == {"duplicate-titles"}
    assert "https://e.com/a" in found["duplicate-titles"] and "https://e.com/b" in found["duplicate-titles"]


def test_duplicate_meta_descriptions() -> None:
    found = run([mk("/a", "A", "dup"), mk("/b", "B", "dup")])
    assert set(found) == {"duplicate-meta-descriptions"}
    assert "https://e.com/b" in found["duplicate-meta-descriptions"]


def test_broken_internal_links() -> None:
    found = run([mk("/", "H", "d", ("/gone",)), mk("/gone", "G", "e", status=404)])
    assert set(found) == {"broken-internal-links"}
    assert "https://e.com/gone (HTTP 404) linked from https://e.com/" in found["broken-internal-links"]


def test_robots_missing_and_empty() -> None:
    assert set(run([mk("/", "H", "d")], robots=SiteFile(404))) == {"robots-txt-missing"}
    assert set(run([mk("/", "H", "d")], robots=SiteFile())) == {"robots-txt-missing"}
    assert set(run([mk("/", "H", "d")], robots=SiteFile(200, "  "))) == {"robots-txt-empty"}


def test_sitemap_missing_invalid_empty() -> None:
    pages = [mk("/", "H", "d")]
    assert set(run(pages, sitemap=SiteFile(404))) == {"sitemap-missing"}
    assert set(run(pages, sitemap=SiteFile(200, "<urlset><url>"))) == {"sitemap-invalid"}
    assert set(run(pages, sitemap=SiteFile(200, "<html></html>"))) == {"sitemap-invalid"}
    assert set(run(pages, sitemap=SiteFile(200, "<urlset></urlset>"))) == {"sitemap-empty"}


def test_sitemap_index_is_valid() -> None:
    index = SiteFile(200, "<sitemapindex><sitemap><loc>https://e.com/s1.xml</loc></sitemap></sitemapindex>")
    assert run([mk("/", "H", "d")], sitemap=index) == {}


def test_probe_site_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *") if request.url.path == "/robots.txt" else httpx.Response(404)

    async def go() -> tuple[SiteFile, SiteFile]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe_site_files(client, "https://e.com/x")

    robots, sitemap = asyncio.run(go())
    assert robots.present and robots.text == "User-agent: *"
    assert not sitemap.present and sitemap.status == 404


def test_registry_accepts_new_checks() -> None:
    before = len(SITE_CHECKS)

    @site_check
    def extra(ctx: SiteContext) -> list:  # type: ignore[type-arg]
        return []

    assert len(SITE_CHECKS) == before + 1
    SITE_CHECKS.remove(extra)

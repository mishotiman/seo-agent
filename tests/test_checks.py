from seo_audit.checks import PAGE_CHECKS, PageContext, page_check, run_page_checks
from seo_audit.extractor import extract_facts
from seo_audit.fetcher import FetchResult, RedirectHop
from seo_audit.schema import Finding, Severity, Source

URL = "https://example.com/page"
TITLE = "A Perfectly Reasonable Page Title Here"  # 38 chars
DESC = "A meta description that is comfortably within the recommended length range for search results."
OG = "".join(f'<meta property="og:{t}" content="x">' for t in ("title", "description", "image", "url"))


def page(
    title: str | None = TITLE,
    desc: str | None = DESC,
    body: str = "<h1>Main</h1><h2>Sub</h2><img src='/a.png' alt='a'>",
    canonical: str | None = URL,
    robots: str | None = None,
    viewport: bool = True,
    og: str = OG,
    extra_head: str = "",
) -> str:
    head = ""
    if title is not None:
        head += f"<title>{title}</title>"
    if desc is not None:
        head += f'<meta name="description" content="{desc}">'
    if canonical:
        head += f'<link rel="canonical" href="{canonical}">'
    if robots:
        head += f'<meta name="robots" content="{robots}">'
    if viewport:
        head += '<meta name="viewport" content="width=device-width">'
    return f"<html><head>{head}{og}{extra_head}</head><body>{body}</body></html>"


def run(html: str, **fetch_kwargs: object) -> set[str]:
    fetch = FetchResult(url=URL, final_url=URL, status_code=200, response_time=0.1, **fetch_kwargs)  # type: ignore[arg-type]
    findings = run_page_checks(PageContext(extract_facts(html, URL), fetch))
    for f in findings:
        assert f.source == Source.CODE
        assert f.severity in Severity
        assert f.evidence and f.recommendation
    return {f.id for f in findings}


def test_clean_page_has_no_findings() -> None:
    assert run(page()) == set()


def test_title_missing() -> None:
    assert run(page(title=None)) == {"title-missing"}


def test_title_short() -> None:
    assert run(page(title="Short")) == {"title-short"}


def test_title_long() -> None:
    assert run(page(title="T" * 80)) == {"title-long"}


def test_meta_description_missing() -> None:
    assert run(page(desc=None)) == {"meta-description-missing"}


def test_meta_description_short() -> None:
    assert run(page(desc="Too short.")) == {"meta-description-short"}


def test_meta_description_long() -> None:
    assert run(page(desc="d" * 200)) == {"meta-description-long"}


def test_h1_missing() -> None:
    assert run(page(body="<h2>Only sub</h2>")) == {"h1-missing"}


def test_h1_multiple() -> None:
    assert run(page(body="<h1>A</h1><h1>B</h1>")) == {"h1-multiple"}


def test_heading_skip() -> None:
    assert run(page(body="<h1>A</h1><h3>C</h3>")) == {"heading-skip"}


def test_canonical_missing() -> None:
    assert run(page(canonical=None)) == {"canonical-missing"}


def test_canonical_off_host() -> None:
    assert run(page(canonical="https://other.org/page")) == {"canonical-off-host"}


def test_noindex() -> None:
    assert run(page(robots="noindex, follow")) == {"noindex"}


def test_nofollow_alone_is_not_noindex() -> None:
    assert run(page(robots="nofollow")) == set()


def test_viewport_missing() -> None:
    assert run(page(viewport=False)) == {"viewport-missing"}


def test_image_alt_missing_ignores_empty_alt() -> None:
    body = "<h1>A</h1><img src='/a.png'><img src='/b.png' alt=''>"
    findings = run_page_checks(
        PageContext(
            extract_facts(page(body=body), URL),
            FetchResult(url=URL, final_url=URL, status_code=200, response_time=0.1),
        )
    )
    assert [f.id for f in findings] == ["image-alt-missing"]
    assert "1 of 2" in findings[0].evidence


def test_non_https() -> None:
    http = "http://example.com/page"
    fetch = FetchResult(url=http, final_url=http, status_code=200, response_time=0.1)
    html = page(canonical=http)
    ids = {f.id for f in run_page_checks(PageContext(extract_facts(html, http), fetch))}
    assert ids == {"non-https"}


def test_redirect_chain_over_one_hop() -> None:
    chain = [RedirectHop(url="http://example.com/a", status_code=301), RedirectHop(url="https://example.com/b", status_code=302)]
    assert run(page(), redirect_chain=chain) == {"redirect-chain"}


def test_single_redirect_is_fine() -> None:
    chain = [RedirectHop(url="http://example.com/a", status_code=301)]
    assert run(page(), redirect_chain=chain) == set()


def test_slow_response() -> None:
    fetch = FetchResult(url=URL, final_url=URL, status_code=200, response_time=3.5)
    ids = {f.id for f in run_page_checks(PageContext(extract_facts(page(), URL), fetch))}
    assert ids == {"slow-response"}


def test_json_ld_invalid() -> None:
    head = '<script type="application/ld+json">{not json</script>'
    assert run(page(extra_head=head)) == {"json-ld-invalid"}


def test_open_graph_missing() -> None:
    assert run(page(og='<meta property="og:title" content="x">')) == {"og-missing"}


def test_registry_accepts_new_checks_without_loop_changes() -> None:
    @page_check
    def check_custom(ctx: PageContext) -> list[Finding]:
        return [Finding(id="custom", category="x", severity=Severity.INFO, source=Source.CODE,
                        evidence="e", recommendation="r")]

    try:
        assert "custom" in run(page())
    finally:
        PAGE_CHECKS.remove(check_custom)
    assert run(page()) == set()

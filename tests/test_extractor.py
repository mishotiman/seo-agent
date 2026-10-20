from seo_audit.extractor import extract_facts

URL = "https://example.com/blog/post"

HTML = """<!doctype html>
<html lang="en"><head>
<title>  My   Title </title>
<meta name="description" content="A description.">
<meta name="robots" content="noindex, nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="/blog/canonical">
<link rel="alternate" hreflang="de" href="/de/post">
<link rel="alternate" hreflang="x-default" href="https://example.com/post">
<link rel="stylesheet" href="/s.css">
<meta property="og:title" content="OG Title">
<meta property="og:image" content="https://example.com/i.png">
<meta name="twitter:card" content="summary">
<script type="application/ld+json">{"@type": "Article"}</script>
<script type="application/ld+json">{not json</script>
<style>.x{color:red}</style>
</head><body>
<h1>Main heading</h1>
<h3>Skipped level</h3>
<p>Hello <b>world</b>.</p>
<script>var hidden = 1;</script>
<noscript>enable js</noscript>
<img src="/a.png" alt="An image"><img src="b.png" alt=""><img src="c.png">
<a href="/about#team">About</a>
<a href="/about">About again</a>
<a href="https://example.com/x" rel="nofollow">X</a>
<a href="https://other.org/page">Other</a>
<a href="mailto:a@b.c">mail</a>
<a href="javascript:void(0)">js</a>
<a href="#top">top</a>
</body></html>"""


def test_head_fields() -> None:
    f = extract_facts(HTML, URL)
    assert f.title == "My Title"
    assert f.meta_description == "A description."
    assert f.robots_meta == "noindex, nofollow"
    assert f.viewport == "width=device-width, initial-scale=1"
    assert f.canonical == "https://example.com/blog/canonical"


def test_missing_head_fields_are_none() -> None:
    f = extract_facts("<html><body><p>hi</p></body></html>", URL)
    assert f.title is None
    assert f.meta_description is None
    assert f.canonical is None
    assert f.robots_meta is None
    assert f.viewport is None
    assert f.headings == []


def test_headings_with_levels() -> None:
    f = extract_facts(HTML, URL)
    assert [(h.level, h.text) for h in f.headings] == [(1, "Main heading"), (3, "Skipped level")]


def test_hreflang() -> None:
    f = extract_facts(HTML, URL)
    assert [(h.lang, h.href) for h in f.hreflang] == [
        ("de", "https://example.com/de/post"),
        ("x-default", "https://example.com/post"),
    ]


def test_open_graph_and_twitter() -> None:
    f = extract_facts(HTML, URL)
    assert f.open_graph == {"og:title": "OG Title", "og:image": "https://example.com/i.png"}
    assert f.twitter == {"twitter:card": "summary"}


def test_json_ld_parse_flag() -> None:
    f = extract_facts(HTML, URL)
    assert [b.parsed_ok for b in f.json_ld] == [True, False]
    assert f.json_ld[0].raw == '{"@type": "Article"}'


def test_images_alt() -> None:
    f = extract_facts(HTML, URL)
    assert [(i.src, i.alt) for i in f.images] == [
        ("https://example.com/a.png", "An image"),
        ("https://example.com/blog/b.png", ""),
        ("https://example.com/blog/c.png", None),
    ]


def test_links_split_internal_external() -> None:
    f = extract_facts(HTML, URL)
    assert [link.url for link in f.internal_links] == [
        "https://example.com/about",
        "https://example.com/x",
    ]
    assert f.internal_links[1].nofollow is True
    assert [link.url for link in f.external_links] == ["https://other.org/page"]
    assert f.internal_links[0].text == "About"


def test_visible_text_excludes_scripts_and_head() -> None:
    f = extract_facts(HTML, URL)
    assert "Hello" in f.text and "world" in f.text
    assert "hidden" not in f.text
    assert "enable js" not in f.text
    assert "My Title" not in f.text
    assert f.text_truncated is False


def test_visible_text_truncated_to_cap() -> None:
    html = "<html><body><p>" + "word " * 1000 + "</p></body></html>"
    f = extract_facts(html, URL, max_text_chars=100)
    assert len(f.text) == 100
    assert f.text_truncated is True


def test_malformed_html_does_not_raise() -> None:
    f = extract_facts("<html><h1>Unclosed <p>text", URL)
    assert f.headings[0].text.startswith("Unclosed")

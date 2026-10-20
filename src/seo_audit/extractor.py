"""Page fact extraction: turn HTML into structured facts for code checks and the model."""

from __future__ import annotations

import json
import re
from typing import cast
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_TEXT_CHARS = 8000
NON_HTTP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "sms:")
HIDDEN_TAGS = ("script", "style", "noscript", "template", "head", "svg")


class Heading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int
    text: str


class HreflangLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: str
    href: str


class JsonLdBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: str
    parsed_ok: bool


class ImageFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: str
    alt: str | None = None  # None = attribute missing; "" = present but empty


class LinkFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    text: str = ""
    nofollow: bool = False


class PageFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    meta_description: str | None = None
    headings: list[Heading] = Field(default_factory=list)
    canonical: str | None = None
    robots_meta: str | None = None
    hreflang: list[HreflangLink] = Field(default_factory=list)
    viewport: str | None = None
    open_graph: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    json_ld: list[JsonLdBlock] = Field(default_factory=list)
    images: list[ImageFact] = Field(default_factory=list)
    internal_links: list[LinkFact] = Field(default_factory=list)
    external_links: list[LinkFact] = Field(default_factory=list)
    text: str = ""
    text_truncated: bool = False


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else " ".join(value)


def _meta_content(soup: BeautifulSoup, attr: str, name: str) -> str | None:
    tag = soup.find("meta", attrs={attr: re.compile(f"^{re.escape(name)}$", re.I)})
    if isinstance(tag, Tag):
        content = _attr(tag, "content")
        return _clean(content) if content is not None else None
    return None


def _prefixed_meta(soup: BeautifulSoup, attr: str, prefix: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = _attr(tag, attr)
        content = _attr(tag, "content")
        if key and content is not None and key.lower().startswith(prefix):
            found.setdefault(key.lower(), _clean(content))
    return found


def _same_site(host: str, base_host: str) -> bool:
    return host == base_host


def extract_facts(
    html: str, url: str, max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
) -> PageFacts:
    """Parse `html` (fetched from `url`) into PageFacts. Never raises on malformed markup."""
    soup = BeautifulSoup(html, "lxml")
    facts = PageFacts(url=url)

    if soup.title is not None:
        facts.title = _clean(soup.title.get_text())
    facts.meta_description = _meta_content(soup, "name", "description")
    facts.robots_meta = _meta_content(soup, "name", "robots")
    facts.viewport = _meta_content(soup, "name", "viewport")
    facts.open_graph = _prefixed_meta(soup, "property", "og:")
    facts.twitter = _prefixed_meta(soup, "name", "twitter:")

    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        facts.headings.append(Heading(level=int(tag.name[1]), text=_clean(tag.get_text())))

    for link in soup.find_all("link"):
        rels = [r.lower() for r in (link.get("rel") or [])]
        href = _attr(link, "href")
        if not href:
            continue
        if "canonical" in rels and facts.canonical is None:
            facts.canonical = urljoin(url, href.strip())
        elif "alternate" in rels and _attr(link, "hreflang"):
            facts.hreflang.append(
                HreflangLink(lang=cast(str, _attr(link, "hreflang")), href=urljoin(url, href.strip()))
            )

    for script in soup.find_all("script", attrs={"type": re.compile(r"^application/ld\+json$", re.I)}):
        raw = script.string if script.string is not None else script.get_text()
        try:
            json.loads(raw)
            ok = True
        except ValueError:
            ok = False
        facts.json_ld.append(JsonLdBlock(raw=raw.strip(), parsed_ok=ok))

    for img in soup.find_all("img"):
        src = _attr(img, "src")
        if src:
            facts.images.append(ImageFact(src=urljoin(url, src.strip()), alt=_attr(img, "alt")))

    base_host = (urlsplit(url).hostname or "").lower()
    seen: set[str] = set()
    for a in soup.find_all("a"):
        href = _attr(a, "href")
        if not href or href.strip().startswith("#") or href.strip().lower().startswith(NON_HTTP_SCHEMES):
            continue
        target, _ = urldefrag(urljoin(url, href.strip()))
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not target or target in seen:
            continue
        seen.add(target)
        rel_attr: str | list[str] = a.get("rel") or []
        rels = [rel_attr] if isinstance(rel_attr, str) else [str(r) for r in rel_attr]
        fact = LinkFact(url=target, text=_clean(a.get_text()), nofollow="nofollow" in [r.lower() for r in rels])
        if _same_site((parts.hostname or "").lower(), base_host):
            facts.internal_links.append(fact)
        else:
            facts.external_links.append(fact)

    body = soup.body if soup.body is not None else soup
    for hidden in body.find_all(HIDDEN_TAGS):
        hidden.decompose()
    text = _clean(body.get_text(" "))
    if len(text) > max_text_chars:
        text, facts.text_truncated = text[:max_text_chars], True
    facts.text = text
    return facts

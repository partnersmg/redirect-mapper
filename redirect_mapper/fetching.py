"""Network layer: sitemap retrieval/parsing and concurrent page-title fetching."""

from __future__ import annotations

import gzip
import html
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from urllib.parse import urldefrag, urljoin, urlsplit

import requests

USER_AGENT = "redirect-mapper/0.1 (+sitemap URL migration mapping)"
DEFAULT_TIMEOUT = 20
MAX_SITEMAP_DEPTH = 3
_MAX_HTML_BYTES = 300_000

_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_TITLE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_NON_PAGE_SUFFIX = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|ico|css|js|pdf|zip|mp3|mp4|wav|docx?|xlsx?|pptx?)$",
    re.IGNORECASE,
)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _get_bytes(session: requests.Session, url: str, timeout: int) -> bytes:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    body = response.content
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def links_from_html_sitemap(body: str, page_url: str) -> list[str]:
    """Page links from an HTML sitemap. Same host only, no assets, no fragments."""
    host = urlsplit(page_url).netloc.lower()
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF.findall(body):
        href = html.unescape(href.strip())
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urldefrag(urljoin(page_url, href)).url
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or parts.netloc.lower() != host:
            continue
        if _NON_PAGE_SUFFIX.search(parts.path) or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
    return found


def fetch_sitemap_urls(
    sitemap_url: str,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    _depth: int = 0,
    _seen: set[str] | None = None,
) -> list[str]:
    """All page URLs behind a sitemap.

    Follows sitemap-index files recursively. If the URL serves an HTML page
    rather than XML -- a plugin-generated /sitemap/ page, say -- its same-host
    links are used instead.
    """
    session = session or make_session()
    seen = _seen if _seen is not None else set()
    if sitemap_url in seen or _depth > MAX_SITEMAP_DEPTH:
        return []
    seen.add(sitemap_url)

    body = _get_bytes(session, sitemap_url, timeout)
    try:
        root = ET.fromstring(body)
        kind = _localname(root.tag)
    except ET.ParseError:
        root, kind = None, "html"

    # A well-formed HTML sitemap parses as XML perfectly happily and yields no
    # <loc> elements, so trust the root tag rather than a parse failure.
    if kind not in ("urlset", "sitemapindex"):
        links = links_from_html_sitemap(
            body.decode("utf-8", errors="replace"), sitemap_url
        )
        if not links:
            raise ValueError(
                f"{sitemap_url} is neither a sitemap nor a page with links to follow"
            )
        return links

    locs = [
        (element.text or "").strip()
        for element in root.iter()
        if _localname(element.tag) == "loc" and (element.text or "").strip()
    ]

    if kind == "sitemapindex":
        collected: list[str] = []
        for child in locs:
            collected.extend(
                fetch_sitemap_urls(child, session, timeout, _depth + 1, seen)
            )
        return collected
    return locs


def _extract_title(body: str) -> str | None:
    for pattern in (_OG_TITLE, _TITLE_TAG, _H1):
        found = pattern.search(body)
        if not found:
            continue
        text = html.unescape(_TAGS.sub(" ", found.group(1)))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text
    return None


def _strip_common_suffix(titles: dict[str, str]) -> dict[str, str]:
    """Drop the site-name tail (" | Some Podcast") when most titles share it."""
    if len(titles) < 5:
        return titles
    tails = Counter()
    for title in titles.values():
        for separator in (" | ", " - ", " – ", " — ", " :: "):
            if separator in title:
                tails[separator + title.rsplit(separator, 1)[1].strip()] += 1
    if not tails:
        return titles
    tail, count = tails.most_common(1)[0]
    if count < len(titles) * 0.5:
        return titles
    return {
        url: (title[: -len(tail)].strip() or title) if title.endswith(tail) else title
        for url, title in titles.items()
    }


def fetch_titles(
    urls: list[str],
    workers: int = 16,
    timeout: int = DEFAULT_TIMEOUT,
    progress=None,
) -> dict[str, str]:
    """Fetch page titles concurrently. URLs that fail are simply absent from the result."""
    if not urls:
        return {}

    session = make_session()

    def one(url: str) -> tuple[str, str | None]:
        try:
            response = session.get(url, timeout=timeout, stream=True)
            response.raise_for_status()
            chunk = response.raw.read(_MAX_HTML_BYTES, decode_content=True)
            response.close()
            return url, _extract_title(chunk.decode("utf-8", errors="replace"))
        except Exception:
            return url, None

    titles: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (url, title) in enumerate(pool.map(one, urls), start=1):
            if title:
                titles[url] = title
            if progress:
                progress(done, len(urls))
    return _strip_common_suffix(titles)

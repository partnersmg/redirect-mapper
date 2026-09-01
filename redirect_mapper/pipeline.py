"""Orchestration: sitemaps in, mapping out. This is the entry point a web UI would call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fetching import fetch_sitemap_urls, fetch_titles, make_session
from .matching import (
    MappingResult,
    MatchConfig,
    Page,
    extract_identifier,
    match_pages,
)


@dataclass
class RunOptions:
    include: str | None = None
    exclude: str | None = None
    use_titles: str = "auto"  # auto | always | never
    workers: int = 16
    timeout: int = 20
    match_config: MatchConfig = field(default_factory=MatchConfig)


@dataclass
class RunReport:
    result: MappingResult
    old_count: int
    new_count: int
    titles_fetched: int


def _filter_urls(urls: list[str], options: RunOptions) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    include = re.compile(options.include) if options.include else None
    exclude = re.compile(options.exclude) if options.exclude else None
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        if include and not include.search(url):
            continue
        if exclude and exclude.search(url):
            continue
        kept.append(url)
    return kept


def run_mapping(
    old_sitemap_url: str,
    new_sitemap_url: str,
    options: RunOptions | None = None,
    progress=None,
) -> RunReport:
    options = options or RunOptions()
    say = progress or (lambda message: None)
    session = make_session()

    say("Fetching old sitemap...")
    old_urls = _filter_urls(
        fetch_sitemap_urls(old_sitemap_url, session, options.timeout), options
    )
    say(f"  {len(old_urls)} old URLs")

    say("Fetching new sitemap...")
    new_urls = _filter_urls(
        fetch_sitemap_urls(new_sitemap_url, session, options.timeout), options
    )
    say(f"  {len(new_urls)} new URLs")

    old_pages = [Page(url) for url in old_urls]
    new_pages = [Page(url) for url in new_urls]

    titles_fetched = 0
    if options.use_titles == "never":
        result = match_pages(old_pages, new_pages, options.match_config)
    else:
        result = match_pages(old_pages, new_pages, options.match_config)
        needs_help = options.use_titles == "always" or _weak_or_missing(result)
        if needs_help:
            resolved_old = {m.old_url for m in result.matches if m.confidence == 5}
            resolved_new = {m.new_url for m in result.matches if m.confidence == 5}
            targets = [p.url for p in old_pages if p.url not in resolved_old]
            targets += [p.url for p in new_pages if p.url not in resolved_new]
            say(f"Fetching {len(targets)} page titles ({options.workers} at a time)...")
            titles = fetch_titles(targets, options.workers, options.timeout)
            titles_fetched = len(titles)
            say(f"  {titles_fetched} titles retrieved")
            for page in old_pages + new_pages:
                page.title = titles.get(page.url)
                if page.identifier is None and page.title:
                    page.identifier = extract_identifier(page.url, page.title)
            result = match_pages(old_pages, new_pages, options.match_config)

    return RunReport(result, len(old_urls), len(new_urls), titles_fetched)


def _weak_or_missing(result: MappingResult) -> bool:
    return bool(result.unmatched) or any(m.confidence < 4 for m in result.matches)

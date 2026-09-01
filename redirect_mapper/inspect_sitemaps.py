"""Recon before matching: what is actually in these two sitemaps?

Usage: python3 -m redirect_mapper.inspect_sitemaps <old_sitemap> <new_sitemap>
"""

from __future__ import annotations

import sys
from collections import Counter
from urllib.parse import urlsplit

from .fetching import fetch_sitemap_urls, make_session


def _section(url: str) -> str:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return "/" + segments[0] + "/" if segments else "/ (homepage)"


def summarize(label: str, sitemap_url: str) -> None:
    print(f"\n=== {label}: {sitemap_url}")
    try:
        urls = fetch_sitemap_urls(sitemap_url, make_session())
    except Exception as error:  # noqa: BLE001 - recon should report, not crash
        print(f"  FAILED: {type(error).__name__}: {error}")
        return

    print(f"  {len(urls)} URLs total")
    print("  by top-level section:")
    for section, count in Counter(_section(u) for u in urls).most_common(25):
        print(f"    {count:5}  {section}")
    print("  samples:")
    for url in urls[:5]:
        print(f"    {url}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__.strip())
        return 2
    summarize("OLD", args[0])
    summarize("NEW", args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

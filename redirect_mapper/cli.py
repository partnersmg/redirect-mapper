"""CLI wrapper. All real work lives in pipeline.py / matching.py."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from .matching import MatchConfig, matches_to_rows, unmatched_to_rows
from .pipeline import RunOptions, run_mapping


def _write_csv(path: str, rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map old-site URLs to new-site URLs for 301 redirects, using XML sitemaps."
    )
    parser.add_argument("old_sitemap", help="XML sitemap (or sitemap index) of the old site")
    parser.add_argument("new_sitemap", help="XML sitemap (or sitemap index) of the new site")
    parser.add_argument("-o", "--out", default="redirects.csv")
    parser.add_argument("-u", "--unmatched", default="unmatched.csv")
    parser.add_argument("--include", help="regex; keep only URLs matching it")
    parser.add_argument("--exclude", help="regex; drop URLs matching it")
    parser.add_argument(
        "--titles",
        choices=("auto", "always", "never"),
        default="auto",
        help="fetch page titles as an extra matching signal (default: auto)",
    )
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument(
        "--floor",
        type=float,
        default=MatchConfig.band_1,
        help="fuzzy score below which an old URL is flagged instead of matched",
    )
    args = parser.parse_args(argv)

    config = MatchConfig(band_1=args.floor)
    options = RunOptions(
        include=args.include,
        exclude=args.exclude,
        use_titles=args.titles,
        workers=args.workers,
        timeout=args.timeout,
        match_config=config,
    )

    report = run_mapping(
        args.old_sitemap,
        args.new_sitemap,
        options,
        progress=lambda message: print(message, file=sys.stderr),
    )

    _write_csv(args.out, matches_to_rows(report.result.matches))
    _write_csv(args.unmatched, unmatched_to_rows(report.result.unmatched))

    bands = Counter(m.confidence for m in report.result.matches)
    print(f"\nOld URLs: {report.old_count}   New URLs: {report.new_count}", file=sys.stderr)
    for band in (5, 4, 3, 2, 1):
        if bands[band]:
            print(f"  confidence {band}: {bands[band]}", file=sys.stderr)
    print(f"  flagged for review: {len(report.result.unmatched)}", file=sys.stderr)
    print(f"\nWrote {args.out} and {args.unmatched}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

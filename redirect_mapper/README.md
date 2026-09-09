# Redirect Mapper (POC)

Maps old-site URLs to new-site URLs for 301 redirects, using two XML sitemaps as input.

```bash
pip install -r redirect_mapper/requirements.txt

python -m redirect_mapper.cli \
  https://old.example.com/sitemap_index.xml \
  https://new.example.com/sitemap_index.xml \
  --out redirects.csv --unmatched unmatched.csv
```

Outputs:

- `redirects.csv` — `Old URL, New URL, Match Confidence (1-5)`
- `unmatched.csv` — old URLs with no confident match, each with its best rejected
  candidate and score, so a human can accept or reject quickly

## How matching works

Four tiers, applied in order. Once an old URL is matched it leaves the pool, and a
new URL is never assigned to two old URLs.

| Tier | Signal | Confidence |
|---|---|---|
| 1 | Same episode identifier (`ep-101`, `episode 101`, `#101`), unambiguous on both sides | 5 |
| 2 | Identical normalized slug, section-aware (`/podcasts/x` prefers `/podcasts/x` over `/x`) | 5 |
| 3 | Strong fuzzy score **and** a shared number in both URLs | 5 |
| 3 | Fuzzy score ≥ 92 / ≥ 82 / ≥ 70 / ≥ 60 | 4 / 3 / 2 / 1 |
| 4 | Fuzzy score < 60 | flagged, not written to `redirects.csv` |

Fuzzy scoring uses `rapidfuzz.fuzz.WRatio` over normalized slugs, and over page titles
when titles are available. Normalization lowercases, splits on non-alphanumerics, and
drops noise words (`the`, `episode`, `podcast`, …) so `/podcast/ep-101-foo/` and
`/episodes/101-foo/` compare as the same string.

A year (1900–2100) is never treated as an episode identifier, so dated permalinks like
`/2019/07/14/slug/` don't collide. A bare trailing number (e.g. a WordPress post ID)
is *not* trusted as an identifier on its own — it only promotes a match that already
scores well on text similarity.

Nothing is ever forced. If the best candidate scores below the floor (default 60,
`--floor`), the old URL goes to the review list instead of getting a wrong redirect.

## Titles

`--titles auto` (the default) runs a slug-only pass first, and only fetches page titles
if anything is unmatched or weakly matched. It then fetches titles for just the
unresolved URLs, concurrently (`--workers`, default 16), reading at most 300 KB per
page and taking `og:title`, then `<title>`, then `<h1>`. A site-name tail shared by most
titles (`… | The Show`) is stripped before comparison. Failed fetches are skipped rather
than aborting the run.

This is the path that rescues opaque URLs — `/?p=4471`, `/podcast/12345/` — which carry
no text signal at all. Use `--titles always` to force it, `--titles never` to stay offline
of the page bodies.

## Filtering

Both sitemaps are read in full, including sitemap-index files (recursively, depth 3,
with a visited set so a self-referencing index can't loop). `.xml.gz` children are
handled. If the URL serves an HTML page instead of XML — a plugin-generated `/sitemap/`
page — its same-host links are used instead, dropping assets, fragments and off-site
links. Prefer the real `sitemap_index.xml` where one exists: an HTML sitemap also picks
up whatever the page's own nav and footer link to.

If you want to restrict to episodes:

```bash
--include '/podcast/|/episodes/'      # keep only these, on both sites
--exclude '/(tag|category|author)/'   # drop these, on both sites
```

Sections are usually renamed in a redesign, so the two sides often need different
patterns. `--include-old` / `--include-new` (and the `--exclude-` pair) override one
side. Matching an old `/articles/` section against pieces that now live at the site
root:

```bash
--include-old '/articles/' --exclude-new '/(wp-content|category|author|tag|podcasts)/'
```

Filtering both sides with a section name the new site doesn't use will silently strip
the new-site pool to nothing, so the run warns when the new pool comes back less than
half the size of the old one.

## Structure

The matching logic is a pure function with no network or CLI dependency, so the eventual
web app can import it directly:

```python
from redirect_mapper import run_mapping, RunOptions, matches_to_rows

report = run_mapping(old_sitemap_url, new_sitemap_url, RunOptions(workers=24))
rows = matches_to_rows(report.result.matches)   # ready for csv.writer
```

- `matching.py` — `Page`, `match_pages()`, scoring/banding, CSV row builders. No I/O.
- `fetching.py` — sitemap retrieval + parsing, concurrent title fetching.
- `pipeline.py` — `run_mapping()`, the one call a web UI needs.
- `cli.py` — argument parsing and file writing only.
- `test_matching.py` — fixture tests: `python -m redirect_mapper.test_matching`

## Scope each run to comparable content

Run one section at a time (`--include '/podcasts/'`) rather than the whole site at once.
A larger candidate pool doesn't help an old URL whose counterpart doesn't exist — it just
gives the fuzzy tier more chances to clear the floor with an unrelated page. In testing,
two episodes with no counterpart on the new site were correctly flagged in a
podcasts-only run, and in the whole-site run were matched at confidence 3 to unrelated
service pages instead.

## Reviewing the output

CSVs are written with RFC 4180 line endings (`\r\n`), which Excel and Sheets want but
which breaks naive `grep ',5$'`. Use `awk`, which coerces the trailing `\r` away:

```bash
awk -F, 'NR>1 && $3+0 < 5' redirects.csv    # everything needing review
awk -F, 'NR>1 && $3+0 == 5' redirects.csv   # the safe tier
```

## Known limits (it's a POC)

- Short slugs inflate fuzzy scores (`/about-us/` → `/about/` scores 90). Review anything
  below confidence 4 before shipping the redirects.
- Assignment is greedy best-first, not globally optimal. For a genuinely ambiguous
  cluster of near-identical titles it can pick a worse overall pairing than a Hungarian
  solver would.
- Query strings are dropped when deriving slugs, so `?p=` URLs depend entirely on titles.
- No robots.txt check or rate limiting beyond the concurrency cap. Point it at sites you
  control.

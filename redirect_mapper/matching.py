"""Pure matching logic: no network, no CLI. Safe to import from a web app."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from rapidfuzz import fuzz

# Words that carry no distinguishing signal between an old and a new podcast URL.
_STOPWORDS = {
    "a", "an", "and", "the", "of", "to", "in", "on", "for", "with", "at", "by",
    "from", "or", "is", "it", "episode", "episodes", "ep", "eps", "podcast",
    "podcasts", "show", "shows", "part", "pt", "html", "php", "index", "www",
}

_EPISODE_PATTERNS = (
    re.compile(r"(?:^|[/_\-])(?:ep|eps|epi|episode|show)[\s_\-]?(\d{1,4})(?:[/_\-]|$)"),
    re.compile(r"(?:^|\s)#\s?(\d{1,4})(?:\b|$)"),
    re.compile(r"(?:^|\s)episode\s+(\d{1,4})(?:\b|$)"),
)

_TRAILING_NUMBER = re.compile(r"(?:^|[/_\-])(\d{1,4})(?:[/_\-]|$)")


@dataclass
class Page:
    url: str
    slug: str = ""
    title: str | None = None
    identifier: str | None = None

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slug_from_url(self.url)
        if self.identifier is None:
            self.identifier = extract_identifier(self.url, self.title)


@dataclass
class Match:
    old_url: str
    new_url: str
    confidence: int
    score: float
    basis: str


@dataclass
class Unmatched:
    old_url: str
    best_candidate: str | None
    best_score: float
    reason: str


@dataclass
class MatchConfig:
    """Score cut-offs, in rapidfuzz 0-100 terms, mapped to the 1-5 confidence bands."""

    band_4: float = 92.0
    band_3: float = 82.0
    band_2: float = 70.0
    band_1: float = 60.0
    # Below band_1 nothing is emitted; the old URL is flagged for manual review.

    def confidence_for(self, score: float) -> int | None:
        if score >= self.band_4:
            return 4
        if score >= self.band_3:
            return 3
        if score >= self.band_2:
            return 2
        if score >= self.band_1:
            return 1
        return None


@dataclass
class MappingResult:
    matches: list[Match] = field(default_factory=list)
    unmatched: list[Unmatched] = field(default_factory=list)

    @property
    def needs_titles(self) -> bool:
        return bool(self.unmatched)


def slug_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path)
    segments = [s for s in path.split("/") if s]
    if not segments:
        return urlsplit(url).netloc
    last = segments[-1]
    return re.sub(r"\.(html?|php|aspx?)$", "", last, flags=re.IGNORECASE)


def normalize(text: str | None) -> str:
    if not text:
        return ""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    kept = [t for t in tokens if t and t not in _STOPWORDS]
    return " ".join(kept) if kept else " ".join(t for t in tokens if t)


def extract_identifier(url: str, title: str | None = None) -> str | None:
    """A stable episode identifier, or None. Years and dates are never identifiers."""
    path = unquote(urlsplit(url).path).lower()
    for pattern in _EPISODE_PATTERNS:
        for haystack in (path, (title or "").lower()):
            found = pattern.search(haystack)
            if found:
                return f"ep:{int(found.group(1))}"
    for found in _TRAILING_NUMBER.finditer(slug_from_url(url).lower()):
        value = int(found.group(1))
        if not _looks_like_year(value):
            return f"num:{value}"
    return None


def _looks_like_year(value: int) -> bool:
    return 1900 <= value <= 2100


def _section_segment(url: str) -> str:
    """The path segment above the slug: /podcasts/x/ -> 'podcasts', /x/ -> ''."""
    segments = [s for s in unquote(urlsplit(url).path).split("/") if s]
    return segments[-2].lower() if len(segments) >= 2 else ""


def _numbers_agree(old_page: Page, new_page: Page) -> bool:
    if not old_page.identifier or not new_page.identifier:
        return False
    return old_page.identifier.split(":")[1] == new_page.identifier.split(":")[1]


def _unique_identifier_index(pages: list[Page]) -> dict[str, Page]:
    """Only identifiers that appear exactly once are usable as an exact key."""
    counts: dict[str, list[Page]] = {}
    for page in pages:
        if page.identifier:
            counts.setdefault(page.identifier, []).append(page)
    return {key: group[0] for key, group in counts.items() if len(group) == 1}


def _greedy_assign(
    old_pages: list[Page],
    new_pages: list[Page],
    scores,
    floor: float,
) -> list[tuple[int, int, float]]:
    """Best-first one-to-one assignment. No new URL is ever claimed twice."""
    candidates = []
    for i, row in enumerate(scores):
        for j, score in enumerate(row):
            if score >= floor:
                candidates.append((float(score), i, j))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    taken_old: set[int] = set()
    taken_new: set[int] = set()
    assigned = []
    for score, i, j in candidates:
        if i in taken_old or j in taken_new:
            continue
        taken_old.add(i)
        taken_new.add(j)
        assigned.append((i, j, score))
    return assigned


def _score_matrix(old_keys: list[str], new_keys: list[str]) -> list[list[float]]:
    return [[fuzz.WRatio(old, new) for new in new_keys] for old in old_keys]


def _best_row(scores, i) -> tuple[int, float]:
    row = scores[i]
    if len(row) == 0:
        return -1, 0.0
    best_j = max(range(len(row)), key=lambda j: row[j])
    return best_j, float(row[best_j])


def match_pages(
    old_pages: list[Page],
    new_pages: list[Page],
    config: MatchConfig | None = None,
) -> MappingResult:
    """Match old pages to new pages. Titles are used when present, ignored when not."""
    config = config or MatchConfig()
    result = MappingResult()
    if not old_pages:
        return result
    if not new_pages:
        result.unmatched = [
            Unmatched(p.url, None, 0.0, "no URLs in the new sitemap") for p in old_pages
        ]
        return result

    remaining_old = list(old_pages)
    remaining_new = list(new_pages)

    # Tier 1: unambiguous identifier on both sides.
    new_by_id = _unique_identifier_index(remaining_new)
    old_by_id = _unique_identifier_index(remaining_old)
    claimed_old: set[str] = set()
    claimed_new: set[str] = set()
    for identifier, old_page in old_by_id.items():
        new_page = new_by_id.get(identifier)
        if new_page is None:
            continue
        result.matches.append(
            Match(old_page.url, new_page.url, 5, 100.0, f"identifier {identifier}")
        )
        claimed_old.add(old_page.url)
        claimed_new.add(new_page.url)

    remaining_old = [p for p in remaining_old if p.url not in claimed_old]
    remaining_new = [p for p in remaining_new if p.url not in claimed_new]

    # Tier 2: identical normalized slug.
    new_by_slug: dict[str, list[Page]] = {}
    for page in remaining_new:
        new_by_slug.setdefault(normalize(page.slug), []).append(page)
    claimed_old.clear()
    claimed_new.clear()
    # Two passes. Pages that agree on slug *and* section are matched first, so
    # /podcasts/x claims /podcasts/x before /articles/x has to choose between
    # /x and /podcasts/x. Without this, a slug published in two sections is
    # resolved by greedy tie-breaking, which is to say by luck.
    for require_same_section in (True, False):
        for old_page in remaining_old:
            if old_page.url in claimed_old:
                continue
            group = new_by_slug.get(normalize(old_page.slug), [])
            available = [p for p in group if p.url not in claimed_new]
            if require_same_section:
                section = _section_segment(old_page.url)
                available = [p for p in available if _section_segment(p.url) == section]
            if len(available) != 1:
                continue
            basis = "exact slug + section" if require_same_section else "exact slug"
            result.matches.append(
                Match(old_page.url, available[0].url, 5, 100.0, basis)
            )
            claimed_old.add(old_page.url)
            claimed_new.add(available[0].url)

    remaining_old = [p for p in remaining_old if p.url not in claimed_old]
    remaining_new = [p for p in remaining_new if p.url not in claimed_new]
    if not remaining_old:
        return result
    if not remaining_new:
        result.unmatched = [
            Unmatched(p.url, None, 0.0, "every new URL already claimed")
            for p in remaining_old
        ]
        return result

    # Tier 3: fuzzy. Titles when we have them on both sides, slugs otherwise.
    old_slugs = [normalize(p.slug) for p in remaining_old]
    new_slugs = [normalize(p.slug) for p in remaining_new]
    slug_scores = _score_matrix(old_slugs, new_slugs)

    have_titles = any(p.title for p in remaining_old) and any(
        p.title for p in remaining_new
    )
    if have_titles:
        old_titles = [normalize(p.title) or old_slugs[i] for i, p in enumerate(remaining_old)]
        new_titles = [normalize(p.title) or new_slugs[j] for j, p in enumerate(remaining_new)]
        title_scores = _score_matrix(old_titles, new_titles)
        combined = [
            [max(slug_scores[i][j], title_scores[i][j]) for j in range(len(remaining_new))]
            for i in range(len(remaining_old))
        ]
    else:
        combined = slug_scores

    basis = "fuzzy title/slug" if have_titles else "fuzzy slug"
    assigned = _greedy_assign(remaining_old, remaining_new, combined, config.band_1)
    matched_old_idx = set()
    for i, j, score in assigned:
        confidence = config.confidence_for(score)
        if confidence is None:
            continue
        old_page, new_page = remaining_old[i], remaining_new[j]
        match_basis = basis
        # A shared number plus a strong fuzzy score is as good as an exact identifier;
        # a shared number on its own is not (it may be a post ID, not an episode number).
        if score >= config.band_3 and _numbers_agree(old_page, new_page):
            confidence = 5
            match_basis = f"{basis} + shared number"
        matched_old_idx.add(i)
        result.matches.append(
            Match(old_page.url, new_page.url, confidence, score, match_basis)
        )

    for i, old_page in enumerate(remaining_old):
        if i in matched_old_idx:
            continue
        best_j, best_score = _best_row(combined, i)
        result.unmatched.append(
            Unmatched(
                old_page.url,
                remaining_new[best_j].url if best_j >= 0 else None,
                round(best_score, 1),
                "below confidence floor",
            )
        )

    result.matches.sort(key=lambda m: (-m.confidence, m.old_url))
    result.unmatched.sort(key=lambda u: -u.best_score)
    return result


def matches_to_rows(matches: list[Match]) -> list[list[str]]:
    """CSV rows for the deliverable, header first."""
    rows = [["Old URL", "New URL", "Match Confidence (1-5)"]]
    rows.extend([m.old_url, m.new_url, str(m.confidence)] for m in matches)
    return rows


def unmatched_to_rows(unmatched: list[Unmatched]) -> list[list[str]]:
    rows = [["Old URL", "Best Candidate (unconfirmed)", "Score", "Reason"]]
    rows.extend(
        [u.old_url, u.best_candidate or "", str(u.best_score), u.reason]
        for u in unmatched
    )
    return rows

"""Fixture tests for the matching layer. Run: python -m redirect_mapper.test_matching"""

from __future__ import annotations

from .matching import Page, extract_identifier, match_pages, normalize

OLD = [
    "https://old.example.com/podcast/ep-101-scaling-kubernetes-at-the-edge/",
    "https://old.example.com/podcast/ep-102-zero-trust-for-small-teams/",
    "https://old.example.com/podcast/2019/07/14/the-cost-of-technical-debt/",
    "https://old.example.com/podcast/?p=4471",
    "https://old.example.com/podcast/ep-999-a-show-that-never-moved/",
    "https://old.example.com/about-us/",
]
NEW = [
    "https://new.example.com/episodes/101-scaling-kubernetes-at-the-edge/",
    "https://new.example.com/episodes/zero-trust-for-small-teams-episode-102/",
    "https://new.example.com/episodes/the-true-cost-of-technical-debt/",
    "https://new.example.com/episodes/observability-on-a-budget/",
    "https://new.example.com/about/",
]


def test_identifiers() -> None:
    assert extract_identifier("https://x.com/podcast/ep-101-foo/") == "ep:101"
    assert extract_identifier("https://x.com/e/episode-7-bar/") == "ep:7"
    assert extract_identifier("https://x.com/blog/hello-world/") is None
    # A year is never an identifier.
    assert extract_identifier("https://x.com/podcast/2019/07/14/debt/") is None
    assert extract_identifier("https://x.com/p/", "Ep. 12 | #55 Something") == "ep:55"


def test_normalize_drops_noise_words() -> None:
    assert normalize("Episode 12: The Cost of Technical Debt") == "12 cost technical debt"
    assert normalize("") == ""


def test_slug_pass_matches_and_flags() -> None:
    result = match_pages([Page(u) for u in OLD], [Page(u) for u in NEW])
    by_old = {m.old_url: m for m in result.matches}

    assert by_old[OLD[0]].confidence == 5
    assert by_old[OLD[0]].new_url == NEW[0]
    assert by_old[OLD[1]].confidence == 5  # ep:102 on both sides
    assert by_old[OLD[1]].new_url == NEW[1]
    assert by_old[OLD[2]].confidence >= 3  # retitled, so fuzzy

    flagged = {u.old_url for u in result.unmatched}
    assert OLD[3] in flagged  # opaque ?p= URL: no signal without titles
    assert OLD[4] in flagged  # genuinely has no counterpart

    claimed = [m.new_url for m in result.matches]
    assert len(claimed) == len(set(claimed)), "a new URL was assigned twice"


def test_titles_rescue_opaque_urls() -> None:
    old_pages = [Page(u) for u in OLD]
    new_pages = [Page(u) for u in NEW]
    titles = {
        OLD[3]: "Observability on a Budget",
        NEW[3]: "Observability on a Budget",
    }
    for page in old_pages + new_pages:
        page.title = titles.get(page.url)

    result = match_pages(old_pages, new_pages)
    by_old = {m.old_url: m for m in result.matches}
    assert by_old[OLD[3]].new_url == NEW[3]
    assert by_old[OLD[3]].confidence >= 4
    assert OLD[4] in {u.old_url for u in result.unmatched}


def test_shared_number_promotes_strong_fuzzy() -> None:
    old_pages = [Page("https://old.com/podcast/ep-77-chaos-testing-in-prod/")]
    new_pages = [Page("https://new.com/episodes/chaos-testing-in-prod-77/")]
    match = match_pages(old_pages, new_pages).matches[0]
    assert match.confidence == 5


def test_never_forces_a_match() -> None:
    old_pages = [Page("https://old.com/podcast/ep-1-widgets-and-gadgets/")]
    new_pages = [Page("https://new.com/contact/")]
    result = match_pages(old_pages, new_pages)
    assert not result.matches
    assert result.unmatched[0].old_url == old_pages[0].url


def test_empty_new_sitemap_flags_everything() -> None:
    result = match_pages([Page(OLD[0])], [])
    assert not result.matches
    assert len(result.unmatched) == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all tests passed")

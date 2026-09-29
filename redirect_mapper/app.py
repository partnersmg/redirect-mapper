"""Streamlit UI for the redirect mapper.

Run from the repository root:

    python3 -m streamlit run redirect_mapper/app.py

Streamlit executes this file as a script rather than importing it as part of the
package, so relative imports are not available and the repo root is put on the
path explicitly.
"""

from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redirect_mapper.fetching import fetch_sitemap_urls, make_session  # noqa: E402
from redirect_mapper.matching import MatchConfig  # noqa: E402
from redirect_mapper.pipeline import RunOptions, map_url_lists  # noqa: E402

DEFAULT_EXCLUDE_NEW = r"/(wp-content|category|author|tag)/"

st.set_page_config(page_title="Redirect Mapper", page_icon="🔀", layout="wide")


@st.cache_data(ttl=900, show_spinner=False)
def load_sitemap(url: str, timeout: int) -> list[str]:
    """Cached for 15 minutes so several section passes cost one fetch each."""
    return fetch_sitemap_urls(url, make_session(), timeout)


def cell(row, key: str) -> str:
    """A grid cell as text. Rows the user adds arrive with NaN, not empty string."""
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def as_pattern(text: str) -> str | None:
    """Treat a typed section as a literal path fragment, not a regex."""
    return re.escape(text) if text else None


def to_csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8")


st.title("Redirect Mapper")
st.caption(
    "Map an old site's URLs to a new site's URLs for 301 redirects, from two XML "
    "sitemaps. Review what the matcher is unsure about, then export."
)

with st.form("inputs"):
    left, right = st.columns(2)
    old_sitemap = left.text_input(
        "Old site sitemap", placeholder="https://oldsite.com/sitemap_index.xml"
    )
    new_sitemap = right.text_input(
        "New site sitemap", placeholder="https://newsite.com/sitemap_index.xml"
    )

    st.markdown("**Sections to map**")
    st.caption(
        "One row per section. Sections are usually renamed in a redesign, so the old "
        "and new paths often differ (`/blog/` becomes `/articles/`). Leave both blank "
        "to match the whole site at once — worth avoiding, because a bigger candidate "
        "pool gives the fuzzy matcher more chances to pair unrelated pages."
    )
    sections = st.data_editor(
        pd.DataFrame(
            [
                {"Name": "All pages", "Old site path": "", "New site path": ""},
            ]
        ),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="sections",
    )

    with st.expander("Advanced"):
        col1, col2, col3 = st.columns(3)
        exclude_new = col1.text_input(
            "Exclude from new site (regex)",
            value=DEFAULT_EXCLUDE_NEW,
            help="Media files and taxonomy pages are not redirect targets.",
        )
        exclude_old = col2.text_input("Exclude from old site (regex)", value="")
        titles_mode = col3.selectbox(
            "Fetch page titles",
            options=("auto", "always", "never"),
            help="Titles rescue URLs whose slug carries no signal, such as /?p=4471. "
            "'auto' fetches them only when the slug pass leaves something unresolved.",
        )
        col4, col5 = st.columns(2)
        workers = col4.slider("Concurrent requests", 4, 32, 16)
        floor = col5.slider(
            "Confidence floor",
            40,
            80,
            int(MatchConfig.band_1),
            help="Below this fuzzy score an old URL is flagged for review rather than "
            "matched. Lower it to force more guesses; raise it to be stricter.",
        )

    submitted = st.form_submit_button("Run", type="primary")

if submitted:
    if not old_sitemap.strip() or not new_sitemap.strip():
        st.error("Both sitemap URLs are required.")
        st.stop()

    status = st.status("Running...", expanded=True)
    try:
        with status:
            st.write("Fetching sitemaps...")
            old_all = load_sitemap(old_sitemap.strip(), 20)
            new_all = load_sitemap(new_sitemap.strip(), 20)
            st.write(f"{len(old_all)} URLs on the old site, {len(new_all)} on the new.")

            match_rows: list[dict] = []
            review_rows: list[dict] = []
            rows = [r for _, r in sections.iterrows()]
            bar = st.progress(0.0)

            for index, row in enumerate(rows):
                name = cell(row, "Name") or f"Section {index + 1}"
                st.write(f"Mapping **{name}**...")
                options = RunOptions(
                    include_old=as_pattern(cell(row, "Old site path")),
                    include_new=as_pattern(cell(row, "New site path")),
                    exclude_old=exclude_old.strip() or None,
                    exclude_new=exclude_new.strip() or None,
                    use_titles=titles_mode,
                    workers=workers,
                    match_config=MatchConfig(band_1=float(floor)),
                    title_progress=lambda done, total: bar.progress(
                        min(1.0, done / max(total, 1))
                    ),
                )
                report = map_url_lists(old_all, new_all, options, progress=st.write)

                for match in report.result.matches:
                    match_rows.append(
                        {
                            "Keep": match.confidence >= 4,
                            "Section": name,
                            "Old URL": match.old_url,
                            "New URL": match.new_url,
                            "Confidence": match.confidence,
                            "Why": match.basis,
                        }
                    )
                for miss in report.result.unmatched:
                    review_rows.append(
                        {
                            "Section": name,
                            "Old URL": miss.old_url,
                            "Best candidate (rejected)": miss.best_candidate or "",
                            "Score": miss.best_score,
                        }
                    )
                bar.progress((index + 1) / max(len(rows), 1))

        st.session_state["matches"] = pd.DataFrame(match_rows)
        st.session_state["unmatched"] = pd.DataFrame(review_rows)
        status.update(label="Done", state="complete", expanded=False)
    except Exception as error:  # noqa: BLE001 - surface the failure in the UI
        status.update(label="Failed", state="error")
        st.exception(error)
        st.stop()

matches: pd.DataFrame | None = st.session_state.get("matches")
if matches is None:
    st.stop()

if matches.empty:
    st.warning(
        "Nothing matched. Check that the section paths exist on both sites — a path "
        "the new site renamed will filter its candidate pool down to nothing."
    )
    st.stop()

one, two, three = st.columns(3)
one.metric("Confident (5)", int((matches["Confidence"] == 5).sum()))
two.metric("Needs review (1-4)", int((matches["Confidence"] < 5).sum()))
three.metric("No match found", len(st.session_state.get("unmatched", [])))

link = st.column_config.LinkColumn(width="large")
editor_columns = {"Old URL": link, "New URL": link}
locked = ["Section", "Old URL", "New URL", "Confidence", "Why"]

uncertain_rows = matches[matches["Confidence"] < 5].sort_values("Confidence")
if not uncertain_rows.empty:
    st.subheader(f"Needs review ({len(uncertain_rows)})")
    st.caption(
        "Open both URLs and decide. Confidence 3 and below is roughly a coin flip — "
        "in testing, some were exactly right and others pointed at unrelated pages. "
        "Untick anything you don't want in the export."
    )
    reviewed = st.data_editor(
        uncertain_rows,
        column_config=editor_columns,
        disabled=locked,
        hide_index=True,
        use_container_width=True,
        key="review_editor",
    )
else:
    reviewed = uncertain_rows

confident_rows = matches[matches["Confidence"] == 5]
with st.expander(f"Confident matches ({len(confident_rows)})"):
    st.caption(
        "Exact identifier or exact slug, section-aware. These are safe by construction."
    )
    confirmed = st.data_editor(
        confident_rows,
        column_config=editor_columns,
        disabled=locked,
        hide_index=True,
        use_container_width=True,
        key="confident_editor",
    )

unmatched: pd.DataFrame = st.session_state.get("unmatched", pd.DataFrame())
if not unmatched.empty:
    with st.expander(f"No confident match ({len(unmatched)})"):
        st.caption(
            "Nothing on the new site scored above the floor. Often the page genuinely "
            "wasn't migrated, in which case decide on a fallback target rather than "
            "letting it 404. The rejected candidate is shown for context only."
        )
        st.dataframe(
            unmatched,
            column_config={
                "Old URL": link,
                "Best candidate (rejected)": link,
            },
            hide_index=True,
            use_container_width=True,
        )

kept = pd.concat([confirmed, reviewed]) if not reviewed.empty else confirmed
kept = kept[kept["Keep"]]

st.subheader("Export")
export_rows = [["Old URL", "New URL", "Match Confidence (1-5)"]]
export_rows += [
    [row["Old URL"], row["New URL"], str(row["Confidence"])]
    for _, row in kept.iterrows()
]

one, two = st.columns(2)
one.download_button(
    f"Download {len(kept)} redirects (CSV)",
    data=to_csv(export_rows),
    file_name="redirects.csv",
    mime="text/csv",
    type="primary",
    disabled=kept.empty,
)
if not unmatched.empty:
    two.download_button(
        f"Download {len(unmatched)} unmatched (CSV)",
        data=to_csv(
            [["Old URL", "Best candidate (rejected)", "Score"]]
            + [
                [r["Old URL"], r["Best candidate (rejected)"], str(r["Score"])]
                for _, r in unmatched.iterrows()
            ]
        ),
        file_name="unmatched.csv",
        mime="text/csv",
    )

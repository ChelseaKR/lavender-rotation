"""Streamlit dashboard: enter a username, get explainable, values-aware picks.

Run with ``make dev`` (``streamlit run app/dashboard.py``). This surface is the
**demo world**, always: it renders the fixture catalog so it works with no API
key and no account. Live data has a home since FIX-01, but it is the CLI's
(``lavender ingest --user`` then ``lavender recommend --user``), not this one's — a
Streamlit script re-runs top to bottom on every interaction, and holding the
cache connection a live source needs across those re-runs is a change to make
deliberately rather than as a side effect of wiring ingest.

Accessibility: the values lens is a labeled, always-visible, explained slider;
identity is shown as text + glyph (never color alone); the score chart is paired
with a data table; sources render as real links. The committed static render
(:mod:`app.build_static`) carries the same semantics for the automated a11y gate.

Two interactive features sit on top of the core:

* **"Why this artist"** — each card surfaces the shared
  :class:`~recommender.why.WhyThisArtist`: the sourced identity (with provenance,
  never inferred) plus the hybrid + values-lens reasons.
* **Playlist export** — download a portable track list (text/CSV/M3U/JSPF) with no
  account, or connect Spotify (env-configured OAuth) to push a real playlist.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from export.models import ExportError, ExportFormat
from export.spotify import (
    PkcePair,
    RequestsTransport,
    SpotifyClient,
    SpotifyCredentials,
    SpotifyOAuth,
    capture_redirect,
    export_recommendations,
    parse_redirect,
)
from export.tracklist import recommendations_to_tracks, render
from pipeline.cache import DEFAULT_DB_PATH, Cache
from pipeline.demo import DEMO_USER, demo_catalog, demo_scrobbles, demo_source
from pipeline.ingest import build_profile
from pipeline.lastfm import ScrobbleSource
from pipeline.models import Artist, ListeningProfile, Recommendation, Scrobble
from recommender.coverage import identity_coverage
from recommender.feedback import Feedback
from recommender.lens import VALUES_LENS
from recommender.why import QUEER_SOURCES_HEADING, WhyThisArtist, why_this_artist

from app.observability import LENS_GRID, OBSERVABILITY_K, observability_inputs
from app.render import POSITION_HELD, position_basis


def _load_demo() -> tuple[list[Scrobble], dict[str, Artist], ScrobbleSource]:
    return demo_scrobbles(), demo_catalog(), demo_source()


def _year_range(scrobbles: list[Scrobble]) -> tuple[int, int]:
    years = [datetime.fromtimestamp(item.ts, tz=UTC).year for item in scrobbles]
    lo, hi = min(years), max(years)
    return (lo - 1, hi + 1) if lo == hi else (lo, hi)


UNMEASURED_TEXT = "not measured"


def unmeasured_or_percent(value: object) -> str:
    """A percentage, or the words for a figure that was never measured.

    ``None`` reaches here for two reasons, both of them "there was nothing to measure": a
    rank-protected segment with no artist in pure taste's top-k (#129), and an empty top-k with
    no slots to share out. Formatting either as ``0%`` or ``100%`` states a measurement that did
    not happen, which is the one thing this panel exists not to do.
    """

    if value is None:
        return UNMEASURED_TEXT
    return f"{cast(float, value):.0%}"


def fairness_exposure_table(rows: Sequence[Mapping[str, object]]) -> dict[str, list[object]]:
    """The exposure-share table the fairness panel renders.

    Built here rather than inline in the Streamlit call so that it can be exercised without a
    Streamlit runtime. It was inline, nothing rendered it in the suite, and a value that became
    nullable upstream turned the demo dashboard into a ``TypeError``.
    """

    return {
        "Identity segment": [row["segment"] for row in rows],
        "Base share": [unmeasured_or_percent(row["base_share"]) for row in rows],
        "Current share": [unmeasured_or_percent(row["current_share"]) for row in rows],
    }


def fairness_retention_table(
    rows: Sequence[Mapping[str, object]], lens_keys: Sequence[str]
) -> dict[str, list[object]]:
    """The retention table the fairness panel renders, one column per lens strength."""

    table: dict[str, list[object]] = {"Identity segment": [row["segment"] for row in rows]}
    for key in lens_keys:
        table[f"Lens {key}"] = [
            unmeasured_or_percent(cast("Mapping[str, object]", row["by_lens"])[key]) for row in rows
        ]
    return table


def _build_temporal_profile(
    username: str,
    scrobbles: list[Scrobble],
    catalog: dict[str, Artist],
    *,
    half_life_days: float | None = None,
    era_start: int | None = None,
    era_end: int | None = None,
) -> ListeningProfile:
    base = build_profile(
        username,
        scrobbles,
        half_life_days=half_life_days,
        era_start=era_start,
        era_end=era_end,
    )
    return ListeningProfile(
        username=base.username,
        play_counts=base.play_counts,
        artist_names=base.artist_names,
        tags={aid: catalog[aid].tags for aid in base.play_counts if aid in catalog},
    )


_FALLBACKS: tuple[tuple[str, ExportFormat, str], ...] = (
    ("Plain text", ExportFormat.TEXT, "text/plain"),
    ("CSV", ExportFormat.CSV, "text/csv"),
    ("M3U playlist", ExportFormat.M3U, "audio/x-mpegurl"),
    ("JSPF (JSON)", ExportFormat.JSPF, "application/json"),
)
# Re-exported from `app.observability`, which is where the panel's inputs are
# decided (#114). Kept bound here so existing references to
# `app.dashboard.LENS_GRID` still resolve, and so there is exactly one
# definition rather than a second copy to drift.
__all__ = ["LENS_GRID", "OBSERVABILITY_K"]


def _finish_spotify_export(
    st: Any,
    oauth: SpotifyOAuth,
    pkce: PkcePair,
    code: str,
    recs: list[Recommendation],
    username: str,
    make_public: bool,
) -> None:
    try:
        token = oauth.exchange_code(code, code_verifier=pkce.verifier)
        client = SpotifyClient(token, RequestsTransport())
        result = export_recommendations(recs, client, username=username, public=make_public)
    except ExportError as exc:
        st.error(f"Export failed: {exc}")
        return
    st.success(
        f"Created “{result.playlist_name}” with {result.matched_count}/"
        f"{result.track_count} tracks matched."
    )
    if result.playlist_url:
        st.markdown(f"[Open your playlist]({result.playlist_url})")
    if result.unmatched:
        st.caption("No Spotify match found for: " + ", ".join(result.unmatched))


def _parse_spotify_redirect(st: Any, redirected: str, expected_state: str) -> str | None:
    try:
        return parse_redirect(redirected, expected_state)
    except ExportError as exc:
        st.error(f"Authorization failed: {exc}")
        return None


def _capture_spotify_redirect(st: Any, redirect_uri: str, state: str) -> str | None:
    try:
        with st.spinner("Waiting for Spotify to redirect back to 127.0.0.1…"):
            redirected = capture_redirect(redirect_uri)
    except ExportError as exc:
        st.error(f"Authorization failed: {exc}")
        return None
    return _parse_spotify_redirect(st, redirected, state)


def _render_spotify_panel(st: Any, recs: list[Recommendation], username: str) -> None:
    try:
        creds = SpotifyCredentials.from_env(os.environ)
    except ExportError as exc:
        st.info(
            f"{exc}. Set LAVENDER_SPOTIFY_CLIENT_ID / _SECRET / _REDIRECT_URI to enable "
            "live Spotify export. The portable formats above work without it."
        )
        return
    oauth = SpotifyOAuth(creds, RequestsTransport())
    st.session_state.setdefault("spotify_state", secrets.token_urlsafe(16))
    st.session_state.setdefault("spotify_pkce", PkcePair.generate())
    pkce: PkcePair = st.session_state["spotify_pkce"]
    state: str = st.session_state["spotify_state"]
    auth_url = oauth.authorize_url(state, code_challenge=pkce.challenge)
    st.markdown(f"1. [Authorize on Spotify]({auth_url})")
    make_public = st.checkbox("Make the playlist public", value=False)
    st.markdown("2. Waiting on the local redirect — or paste the URL yourself:")

    if st.button("Listen for the Spotify redirect (recommended)"):
        code = _capture_spotify_redirect(st, creds.redirect_uri, state)
        if code:
            _finish_spotify_export(st, oauth, pkce, code, recs, username, make_public)

    redirected_url = st.text_input(
        "…or paste the full URL you were redirected to (fallback)", type="password"
    )
    if st.button("Create Spotify playlist from pasted URL") and redirected_url:
        code = _parse_spotify_redirect(st, redirected_url, state)
        if code:
            _finish_spotify_export(st, oauth, pkce, code, recs, username, make_public)


def _render_export(recs: list[Recommendation], username: str) -> None:  # pragma: no cover - UI glue
    import streamlit as st

    st.subheader("Export this playlist")
    st.caption(
        "Exports are opt-in and user-initiated — the only data that leaves your "
        "machine, and only when you click. The portable formats need no account."
    )

    tracks = recommendations_to_tracks(recs)
    name = f"Lavender Rotation — {username}"
    cols = st.columns(len(_FALLBACKS))
    for col, (label, fmt, mime) in zip(cols, _FALLBACKS, strict=True):
        col.download_button(
            label,
            data=render(tracks, fmt, playlist_name=name),
            file_name=f"lavender-rotation.{fmt}",
            mime=mime,
        )

    with st.expander("Connect Spotify and push a playlist"):
        _render_spotify_panel(st, recs, username)


def _render_provenance(st: Any, why: WhyThisArtist) -> None:
    """Both source lists for one card, under headings that keep the axes apart.

    Extracted from ``main`` so that adding ADR 0011's second axis (#92) did not
    push the entry point past the complexity gate — and so the two lists are
    written once rather than twice.
    """
    if why.provenance:
        st.markdown("**Sources** (sourced, never inferred)")
        for item in why.provenance:
            st.markdown(
                f"- {item.source_kind} asserted “{item.asserted_value}”: "
                f"[{item.citation}]({item.citation}) (retrieved {item.retrieved_at})"
            )
    else:
        st.caption("Identity unknown — no sources, surfaced on merit.")
    if why.queer_provenance:
        # Rendered only when a source actually asserted something: an empty
        # state here would read as "not queer", which no absence establishes.
        st.markdown(f"**{QUEER_SOURCES_HEADING}**")
        for item in why.queer_provenance:
            st.markdown(
                f"- {item.source_kind} asserted “{item.asserted_value}”: "
                f"[{item.citation}]({item.citation}) (retrieved {item.retrieved_at})"
            )


def main() -> None:  # pragma: no cover - exercised via the live Streamlit runtime
    import streamlit as st

    st.set_page_config(page_title="Lavender Rotation", layout="centered")
    st.title("Lavender Rotation")
    st.write(
        "Discovery with a values lens, done right: identity is **sourced, never "
        "inferred**, and **unknown is first-class** — never down-ranked."
    )

    username = st.text_input("Last.fm username", value=DEMO_USER)
    lens = st.slider(
        "Values lens strength",
        min_value=0.0,
        max_value=1.0,
        value=0.5,
        step=0.05,
        help=(
            "How strongly to boost artists whose identity is sourced as a woman "
            "(cis or trans — no distinction is drawn) or a nonbinary person, and "
            "bands whose sourced lineup is fronted by one of them. Each "
            "front-person's gender is shown as their source stated it. The lens "
            "only ever boosts — it never lowers anyone's score, and never "
            "penalizes unknown."
        ),
    )
    st.caption(f"Active lens: **{VALUES_LENS.name}**")
    with st.expander("What exactly does this lens boost, and why?"):
        st.write(VALUES_LENS.rationale)
        st.caption(VALUES_LENS.harms_note)
    explore = st.slider(
        "Serendipity",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.05,
        help=(
            "Trades relevance for tag-space variety. This pass reads tags and scores "
            "only; it never reads identity or composition."
        ),
    )
    st.subheader("Temporal taste profile")
    half_life = st.slider(
        "Recency half-life (days; 0 is off)",
        min_value=0,
        max_value=730,
        value=0,
        step=30,
        help="At N days, a play from N days ago counts half as much as a recent play.",
    )
    scrobbles, catalog, source = _load_demo()
    lo_year, hi_year = _year_range(scrobbles)
    use_era = st.checkbox("Limit to an era", value=False)
    era_start: int | None = None
    era_end: int | None = None
    if use_era:
        year_from, year_to = st.slider(
            "Era window (years)",
            min_value=lo_year,
            max_value=hi_year,
            value=(lo_year, hi_year),
        )
        era_start = int(datetime(year_from, 1, 1, tzinfo=UTC).timestamp())
        era_end = int(datetime(year_to, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())

    if username != DEMO_USER:
        st.info(
            f"This dashboard always shows the demo world. To see picks for {username}, "
            f"run `lavender ingest --user {username}` once, then `lavender recommend --user "
            f"{username}` (or `lavender report --user {username}` for a shareable page)."
        )
    profile = _build_temporal_profile(
        username,
        scrobbles,
        catalog,
        half_life_days=float(half_life) if half_life else None,
        era_start=era_start,
        era_end=era_end,
    )
    with Cache(DEFAULT_DB_PATH) as cache:
        feedbacks = cache.load_feedback(username)
    # One call decides both the list on screen and the sweep the fairness panel
    # measures, so the two can never drift apart again (#114).
    recs, panel = observability_inputs(
        profile,
        catalog,
        source,
        current_lens=lens,
        k=10,
        panel_k=OBSERVABILITY_K,
        explore=explore,
        feedbacks=feedbacks,
    )

    coverage = identity_coverage(recs)
    st.subheader("Identity coverage")
    st.write(coverage.summary_line())
    st.caption(
        "This is descriptive, not a quota. Unknown identity is a normal, "
        "first-class outcome and never lowers an artist's score."
    )

    st.subheader("Score summary")
    st.table(
        {
            "Rank": [r.rank for r in recs],
            "Artist": [r.artist.name for r in recs],
            "Taste": [round(r.base_score, 3) for r in recs],
            "Values boost": [round(r.rerank_delta, 3) for r in recs],
            "Total": [round(r.score, 3) for r in recs],
            "Position": [position_basis(r) for r in recs],
            "Identity basis": [str(r.explanation.identity_basis) for r in recs],
        }
    )
    st.caption(
        "Rank is not a pure function of Total. Rows marked "
        f"“{POSITION_HELD}” keep the position they had before the lens was "
        "applied — unknown-identity artists and artists sourced as "
        "Gender.OTHER — so a higher-scoring pick can sit below them. The lens "
        "only ever adds to a score; it never subtracts from one."
    )

    exposure_rows = cast("list[dict[str, object]]", panel["exposure_rows"])
    retention_rows = cast("list[dict[str, object]]", panel["retention_rows"])
    lens_keys = list(cast("dict[str, float | None]", retention_rows[0]["by_lens"]))
    st.subheader(f"Fairness observability (top {OBSERVABILITY_K})")
    st.table(fairness_exposure_table(exposure_rows))
    st.table(fairness_retention_table(retention_rows, lens_keys))
    st.caption(
        "Both tables are computed at your current Serendipity setting, over the "
        "same ranking shown below — so the shares describe the picks on this "
        "screen rather than a different list."
    )
    st.caption(
        "Retention covers score, top-k presence, and list position, and it is "
        "checked on emitted output at every merge. It applies to the two "
        "rank-protected segments. Sourced men keep their exact score but can "
        "move down the list — that is the whole of this lens's re-allocation, "
        "and it is stated in the harms note above rather than denied."
    )

    st.subheader("Recommendations")
    for rec in recs:
        why = why_this_artist(rec)
        with st.container(border=True):
            st.markdown(f"### {rec.rank}. {rec.artist.name}")
            st.caption(
                f"Score {rec.score:.3f} = taste {rec.base_score:.3f} "
                f"+ values lens {rec.rerank_delta:.3f}"
            )
            st.write(f"● Identity: {why.identity_statement}")
            st.caption(f"Rank shift: {why.rank_shift}")
            st.markdown("**Why this artist**")
            for reason in why.reasons:
                st.markdown(f"- {reason}")
            _render_provenance(st, why)
            up_col, down_col = st.columns(2)
            vote: int | None = None
            if up_col.button(f"Thumbs up {rec.artist.name}", key=f"up-{rec.artist.artist_id}"):
                vote = 1
            if down_col.button(
                f"Thumbs down {rec.artist.name}", key=f"down-{rec.artist.artist_id}"
            ):
                vote = -1
            if vote is not None:
                now = datetime.now(UTC)
                with Cache(DEFAULT_DB_PATH) as cache:
                    cache.record_feedback(
                        Feedback(
                            username=username,
                            artist_id=rec.artist.artist_id,
                            vote=vote,
                            ts=int(now.timestamp()),
                        ),
                        fetched_at=now.date().isoformat(),
                    )
                st.rerun()

    _render_export(recs, username)


if __name__ == "__main__":  # pragma: no cover
    main()

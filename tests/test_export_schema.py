"""R9: no identity field may appear in any export format.

An export is the project's one egress. To stay true to "never build a misusable
musician-identity database," what leaves must be *only* artist names + a non-identity
reason — never a gender, a basis, or a provenance citation. These tests assert that
structurally (the schema of every format) and by content (the rendered bytes), using
the demo world, which deliberately contains women, nonbinary, and female-fronted
artists — so any leak would show.

**Extended for ADR 0011's second axis.** This module is named, by name, in
`recommender.lens.QUEER_LENS.harms_note` ("identity never leaves the machine —
no export carries it, tests/test_export_schema.py") and in ADR 0011's
Consequences as one of the defenses that became *load-bearing* once the repo
started holding orientation and trans data. It was written before that axis
existed and checked for none of it: no `queer`, no `orientation`, no `p91`, no
`lesbian`, no `trans`. Worse, the demo world carries no sourced queer artist at
all, so even a token list naming those words would have had nothing to observe
— the guard could not have fired on the most sensitive field in the repo.

Two things fix that here. The forbidden vocabulary is *derived from
``Orientation``* rather than hand-listed, so a member added later is covered
without an edit; and :func:`_queer_world` builds a world that actually contains
a sourced queer, trans artist, so the content assertions have a real leak to
find. That artist is invented (`tests/test_live_enrichment.py` gives the
reason): asserting an orientation or a trans self-identification about a real
person, even in a fixture, is exactly what this project refuses to do.
"""

from __future__ import annotations

import dataclasses
import json
import re

from export.models import ExportFormat, PlaylistTrack
from export.tracklist import recommendations_to_tracks, render, to_csv, to_jspf
from pipeline.identity import IdentityEvidence, resolve_identity, resolve_queer_identity
from pipeline.models import Artist, Orientation, SourceKind
from recommender.hybrid import recommend
from recommender.lens import QUEER_LENS

# Field/column/key names that would mean identity is being carried.
FORBIDDEN_FIELDS = frozenset(
    {
        "gender",
        "identity",
        "identity_basis",
        "basis",
        "sex",
        "pronoun",
        "pronouns",
        "self_identified",
        "self-identified",
        "female_fronted",
        "female-fronted",
        "provenance",
        "source",
        "sources",
        "wikidata",
        "musicbrainz",
        "p21",
        # --- ADR 0011's second axis. Absent until this edit. -----------------
        "queer",
        "orientation",
        "orientations",
        "orientation_sources",
        "sexual_orientation",
        "sexuality",
        "trans",
        "trans_self_identified",
        "trans_sources",
        "p91",
    }
)

#: Every orientation the vocabulary can express, minus the first-class unknown.
#: Derived from the enum rather than transcribed, so an ``Orientation`` member
#: added after this test was written is forbidden in an export the moment it
#: exists — the failure mode this whole module exists to prevent, applied to
#: itself.
ORIENTATION_VOCABULARY = frozenset(o.value for o in Orientation if o is not Orientation.UNKNOWN)

#: Queer-axis words that must never appear in exported bytes. Matched on word
#: boundaries, not as substrings, because several of these ("gay", "trans") are
#: legitimate fragments of ordinary words and a substring test would be a
#: different, noisier check than the one intended.
FORBIDDEN_QUEER_TOKENS = frozenset(
    ORIENTATION_VOCABULARY
    | {
        "queer",
        "trans",
        "transgender",
        "orientation",
        "p91",
        "wikidata-p91",
    }
)

# Identity vocabulary that must never appear in the rendered bytes of any export.
FORBIDDEN_CONTENT_TOKENS = (
    "woman",
    "nonbinary",
    "non-binary",
    "self-identified",
    "female-fronted",
    "band-composition",
    "wikidata",
    "musicbrainz",
    "p21",
    "gender",
    "identity:",
)


def _demo_tracks(profile, catalog, source):
    # Full strength + a wide k, so every identity in the demo world is in scope.
    recs = recommend(profile, catalog, source, k=99, lens_strength=1.0)
    return recommendations_to_tracks(recs)


#: A citation shape this project already produces, pointing at an entity that
#: does not exist. Invented on purpose — see the module docstring.
_FIXTURE_QID = "https://www.wikidata.org/wiki/Q000000000"
_FIXTURE_STATEMENT = "https://example.invalid/interview"


def _queer_artist() -> Artist:
    """A sourced queer, trans woman — invented, so no real person is asserted about.

    Carries a citation on *every* leg the queer axis can produce: a P91
    orientation claim, an artist-statement orientation claim, and a P21 gender
    claim whose raw asserted value is itself a trans self-identification
    (``Q1052281``), which is what :func:`resolve_queer_identity` reads.
    """
    evidence = [
        IdentityEvidence(
            kind=SourceKind.WIKIDATA_P21,
            value="Q1052281",  # "trans woman" — gender resolves to WOMAN, ADR 0011
            citation=_FIXTURE_QID,
            retrieved_at="2026-08-16",
        ),
        IdentityEvidence(
            kind=SourceKind.WIKIDATA_P91,
            value="Q6649",  # "lesbian"
            citation=_FIXTURE_QID,
            retrieved_at="2026-08-16",
        ),
        IdentityEvidence(
            kind=SourceKind.ARTIST_STATEMENT,
            value="queer",
            citation=_FIXTURE_STATEMENT,
            retrieved_at="2026-08-16",
        ),
    ]
    return Artist(
        artist_id="fixture-violet-meridian",
        name="Violet Meridian",
        tags=("dream pop",),
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
        listeners=1_000,
    )


def _queer_tracks(profile, catalog, source):
    """Export tracks for a world that genuinely contains sourced queer data.

    Without this the queer-axis assertions below would pass vacuously: the demo
    world holds no orientation and no trans claim anywhere, so no token list
    could have observed a leak of either.
    """
    artist = _queer_artist()
    assert artist.queer.orientation is not Orientation.UNKNOWN
    assert artist.queer.trans_self_identified is True
    assert artist.queer.sources, "the fixture must carry the citations that could leak"
    world = dict(catalog)
    world[artist.artist_id] = artist
    recs = recommend(profile, world, source, k=99, lens_strength=1.0, lens=QUEER_LENS)
    assert any(r.artist.artist_id == artist.artist_id for r in recs), (
        "the sourced-queer fixture artist must actually reach the export"
    )
    return recommendations_to_tracks(recs)


def test_playlist_track_schema_has_no_identity_field() -> None:
    field_names = {f.name.lower() for f in dataclasses.fields(PlaylistTrack)}
    leaked = field_names & FORBIDDEN_FIELDS
    assert not leaked, f"export track schema carries identity fields: {leaked}"


def test_csv_header_has_no_identity_column(profile, catalog, source) -> None:
    header = to_csv(_demo_tracks(profile, catalog, source)).splitlines()[0]
    columns = {c.strip().lower() for c in header.split(",")}
    assert not (columns & FORBIDDEN_FIELDS), (
        f"CSV header leaks identity: {columns & FORBIDDEN_FIELDS}"
    )


def test_jspf_track_keys_have_no_identity_field(profile, catalog, source) -> None:
    doc = json.loads(to_jspf(_demo_tracks(profile, catalog, source)))
    for track in doc["playlist"]["track"]:
        keys = {k.lower() for k in track}
        assert not (keys & FORBIDDEN_FIELDS), (
            f"JSPF track leaks identity: {keys & FORBIDDEN_FIELDS}"
        )


def test_no_export_format_leaks_identity_vocabulary(profile, catalog, source) -> None:
    tracks = _demo_tracks(profile, catalog, source)
    assert tracks  # sanity: the demo produced something to inspect
    for fmt in ExportFormat:
        rendered = render(tracks, fmt).lower()
        assert "mitski" in rendered  # the export is non-empty / really contains artists
        for token in FORBIDDEN_CONTENT_TOKENS:
            assert token not in rendered, f"{fmt} export leaks identity token {token!r}"


def test_no_export_format_leaks_the_queer_axis(profile, catalog, source) -> None:
    """The ADR 0011 axis: orientation and trans data must not reach an export.

    Runs against a world that really holds both, so a regression that threaded
    ``artist.queer.sources`` into ``PlaylistTrack.why`` would fail here rather
    than pass for want of anything to find.
    """
    tracks = _queer_tracks(profile, catalog, source)
    for fmt in ExportFormat:
        rendered = render(tracks, fmt).lower()
        assert "violet meridian" in rendered, (
            f"{fmt} export dropped the fixture artist; the check would be vacuous"
        )
        for token in sorted(FORBIDDEN_QUEER_TOKENS):
            assert not re.search(rf"\b{re.escape(token)}\b", rendered), (
                f"{fmt} export leaks queer-axis token {token!r}"
            )


def test_queer_axis_fields_are_absent_from_every_export_schema(profile, catalog, source) -> None:
    """Structural half: no column, key, or dataclass field names the axis."""
    tracks = _queer_tracks(profile, catalog, source)
    field_names = {f.name.lower() for f in dataclasses.fields(PlaylistTrack)}
    assert not (field_names & FORBIDDEN_FIELDS)
    columns = {c.strip().lower() for c in to_csv(tracks).splitlines()[0].split(",")}
    assert not (columns & FORBIDDEN_FIELDS)
    for track in json.loads(to_jspf(tracks))["playlist"]["track"]:
        assert not ({k.lower() for k in track} & FORBIDDEN_FIELDS)


def test_the_orientation_vocabulary_is_derived_not_transcribed() -> None:
    """The forbidden list tracks the enum, so a new member cannot slip past it.

    A hand-typed list is the exact failure this module was found in: it was
    written before ``Orientation`` existed and never grew to meet it.
    """
    assert {o.value for o in Orientation if o is not Orientation.UNKNOWN} == ORIENTATION_VOCABULARY
    assert Orientation.UNKNOWN.value not in ORIENTATION_VOCABULARY
    assert ORIENTATION_VOCABULARY <= FORBIDDEN_QUEER_TOKENS

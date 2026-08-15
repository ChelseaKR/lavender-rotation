"""Every identity citation the demo ships must locate a real record — the right one.

The project's first guardrail is that an identity label carries a citation
rather than an inference. A citation only does that work if it resolves, and the
demo's MusicBrainz citations did not: `https://musicbrainz.org/artist/mitski`,
`/sm`, `/snail` and `/moses` are not MBIDs and return HTTP 400. They attached
gender claims about real, named musicians to URLs that cannot be looked up, and
an invented act carried a fabricated MusicBrainz record id in a real registry's
URL space.

The claims themselves were right. Checked against the MusicBrainz API, the
corrected MBIDs report exactly the genders the fixture asserted. Only the
citations were placeholders.

**That fix was shape-only, and shape is not enough.** `citation_problem()` asks
whether a string is a well-formed locator. It cannot ask whether the locator
points at the artist it was cited for. Three Wikidata citations passed it while
pointing at entirely different subjects:

* Mitski cited `Q16735549` — Andreas Constantinou, a Cypriot footballer, whose
  own P21 is `Q6581097` (male).
* Phoebe Bridgers cited `Q28907802` — a douar (village) in Morocco, which has no
  P21 at all.
* Lucy Dacus cited `Q47545178` — a politician.

Each is syntactically perfect, and each *resolves*, so neither the shape gate nor
a link checker would ever have flagged them. Only comparing the record's subject
against the artist it was cited for catches this, which is what
:data:`VERIFIED_SUBJECTS` below does: a committed, dated ledger of what a human
actually saw when they opened each URL. A citation that is not in the ledger
fails, so a new unverified identifier cannot be added silently.
"""

from __future__ import annotations

import pytest
from pipeline.demo import demo_catalog
from pipeline.identity import citation_problem
from pipeline.models import SourceKind

#: Date the registry lookups below were performed by hand, against the live
#: MusicBrainz / Wikidata / Discogs APIs.
VERIFIED_ON = "2026-08-15"

#: citation URL -> (subject the registry reports, what the registry says).
#:
#: "Subject" is the record's own label as the registry returns it, not the name
#: the fixture wanted it to be. That is the whole point: the previous three bad
#: Wikidata citations would have had to be written down here as "Andreas
#: Constantinou", "douar in Morocco" and "politician" next to the artists they
#: were cited for, which is not a thing anyone types by accident.
VERIFIED_SUBJECTS: dict[str, tuple[str, str]] = {
    # MusicBrainz — GET https://musicbrainz.org/ws/2/artist/<mbid>?fmt=json
    "https://musicbrainz.org/artist/fa58cf24-0e44-421d-8519-8bf461dcfaa5": (
        "Mitski",
        "gender=Female, type=Person",
    ),
    "https://musicbrainz.org/artist/84803b1d-dcb3-490d-9af5-debbba64a011": (
        "Soccer Mommy",
        "gender=Female, type=Person",
    ),
    "https://musicbrainz.org/artist/86cd4d38-857c-42bd-a5da-9acedcab1e01": (
        "Snail Mail",
        "gender=Female, type=Person",
    ),
    "https://musicbrainz.org/artist/89c081d4-2ab2-4d3e-8589-ad77dfc40384": (
        "Moses Sumney",
        "gender=Male, type=Person",
    ),
    # Wikidata — GET https://www.wikidata.org/wiki/Special:EntityData/<qid>.json
    "https://www.wikidata.org/wiki/Q23761694": (
        "Mitski",
        "P21=Q6581072 (female); P434 MBID matches the MusicBrainz citation above",
    ),
    "https://www.wikidata.org/wiki/Q24883319": (
        "Phoebe Bridgers",
        "P21=Q6581072 (female)",
    ),
    "https://www.wikidata.org/wiki/Q27967785": (
        "Lucy Dacus",
        "P21=Q6581072 (female)",
    ),
    # Discogs — GET https://api.discogs.com/artists/<id>
    "https://www.discogs.com/artist/5009441-Big-Thief": (
        "Big Thief",
        "members: AdriAnne Lenker, James Krivchenia, Buck Meek, Max Oleartchik, Jason Burger",
    ),
    "https://www.discogs.com/artist/6774153-boygenius": (
        "boygenius",
        "members: Phoebe Bridgers, Julien Baker, Lucy Dacus",
    ),
}

#: Citations that deliberately do NOT locate a record, and why.
#:
#: `example.org` is reserved by RFC 2606 and can never host anything, so every
#: entry here is a placeholder by construction rather than by accident.
#:
#: Only ``big-pop-dude`` has a justification that holds: it is an invented act,
#: so no real registry record for it can exist, and inventing an identifier in a
#: real registry's URL space is the one citation shape this project must not
#: model (``pipeline/demo.py``).
#:
#: The rest are placeholders attached to the identity claims of **real, named
#: people**, which is a weaker position than the module docstring above claims
#: for this fixture. They are enumerated rather than waived: this list is the
#: work item, and the test below fails the moment a sixteenth one appears.
ILLUSTRATIVE_CITATIONS: dict[str, str] = {
    "https://example.org/big-pop-dude-statement": (
        "invented act — no real record can exist; this is the sanctioned shape"
    ),
    "https://example.org/zauner-interview": "real person (Michelle Zauner) — placeholder",
    "https://example.org/lenker": "real person (Adrianne Lenker) — placeholder",
    "https://example.org/shamir-nb": "real person (Shamir) — placeholder",
    "https://example.org/jbaker": "real person (Julien Baker) — placeholder",
    "https://example.org/ldacus": "real person (Lucy Dacus) — placeholder",
}


def _shipped_citations() -> list[tuple[str, SourceKind, str]]:
    """Every (subject, kind, citation) the demo fixture actually ships.

    Walks individual identity, band composition, **and** the front-person
    identities nested inside composition. The last of those was outside the
    previous walk entirely: the gate reached one level short of the data it
    was meant to cover.
    """
    found: list[tuple[str, SourceKind, str]] = []
    for artist in demo_catalog().values():
        for source in artist.identity.sources:
            found.append((artist.name, source.kind, source.citation))
        composition = artist.composition
        if composition is None:
            continue
        for source in composition.sources:
            found.append((artist.name, source.kind, source.citation))
        for person in composition.members_fronting:
            for source in person.identity.sources:
                found.append((person.name, source.kind, source.citation))
    return found


def test_the_walk_reaches_the_front_person_identities() -> None:
    """Guard the traversal itself, not just what it finds.

    Without this, deleting the ``members_fronting`` loop above would silently
    shrink the gate's reach and every other test here would still pass.
    """
    subjects = {name for name, _kind, _citation in _shipped_citations()}
    assert {"Julien Baker", "Lucy Dacus", "Adrianne Lenker"} <= subjects


def test_every_demo_identity_citation_can_locate_its_record() -> None:
    bad: list[str] = []
    for subject, kind, citation in _shipped_citations():
        problem = citation_problem(kind, citation)
        if problem is not None:
            bad.append(f"{subject}: {citation!r} {problem}")
    assert bad == [], "unresolvable identity citations in the shipped demo:\n" + "\n".join(bad)


def test_every_shipped_citation_is_either_verified_or_declared_illustrative() -> None:
    """Subject, not shape. A well-formed identifier for the wrong record fails here.

    This is the check the three bad Wikidata citations needed: each was a valid
    Q-number that resolved, so the only way to catch them was to have written
    down whose record it actually is.
    """
    unaccounted = [
        f"{subject}: {citation!r}"
        for subject, _kind, citation in _shipped_citations()
        if citation not in VERIFIED_SUBJECTS and citation not in ILLUSTRATIVE_CITATIONS
    ]
    assert unaccounted == [], (
        "citation(s) shipped without a verified subject. Open each URL, confirm the "
        "record is the artist it is cited for, and add it to VERIFIED_SUBJECTS with "
        "what you saw (or to ILLUSTRATIVE_CITATIONS with why it cannot resolve):\n"
        + "\n".join(unaccounted)
    )


def test_the_ledger_has_no_stale_entries() -> None:
    """A ledger that outlives the citations it describes stops being evidence."""
    shipped = {citation for _subject, _kind, citation in _shipped_citations()}
    stale = sorted((VERIFIED_SUBJECTS.keys() | ILLUSTRATIVE_CITATIONS.keys()) - shipped)
    assert stale == [], "ledger entries for citations the demo no longer ships:\n" + "\n".join(
        stale
    )


def test_no_citation_is_both_verified_and_illustrative() -> None:
    assert not (VERIFIED_SUBJECTS.keys() & ILLUSTRATIVE_CITATIONS.keys())


def test_every_illustrative_citation_is_on_a_reserved_domain() -> None:
    """Keeps the escape hatch narrow.

    ``ILLUSTRATIVE_CITATIONS`` is the one way to ship a citation that does not
    resolve. Restricting it to RFC 2606's reserved domain means it can never be
    used to wave through a real-looking URL nobody checked.
    """
    wrong = [c for c in ILLUSTRATIVE_CITATIONS if not c.startswith("https://example.org/")]
    assert wrong == [], "illustrative citations must be on example.org (RFC 2606):\n" + "\n".join(
        wrong
    )


@pytest.mark.parametrize(
    ("kind", "citation"),
    [
        # The three subjects-that-were-not-the-artist, kept as regression shapes.
        (SourceKind.WIKIDATA_P21, "https://www.wikidata.org/wiki/Q16735549"),
        (SourceKind.WIKIDATA_P21, "https://www.wikidata.org/wiki/Q28907802"),
        (SourceKind.WIKIDATA_P21, "https://www.wikidata.org/wiki/Q47545178"),
    ],
)
def test_a_wrong_subject_is_shape_valid_which_is_why_the_ledger_exists(
    kind: SourceKind, citation: str
) -> None:
    """Pins the limitation that motivates the ledger.

    Each of these passes ``citation_problem()`` and points at the wrong record.
    If a future change makes the shape gate reject them, that is a real
    improvement — but it must not be mistaken for the ledger being redundant,
    because the next wrong Q-number will be shape-valid too.
    """
    assert citation_problem(kind, citation) is None
    assert citation not in VERIFIED_SUBJECTS


def test_checker_rejects_a_placeholder_and_accepts_a_real_identifier() -> None:
    """Pin the shapes, so the gate above cannot pass by accepting everything."""
    assert citation_problem(SourceKind.MUSICBRAINZ_GENDER, "https://musicbrainz.org/artist/mitski")
    assert (
        citation_problem(
            SourceKind.MUSICBRAINZ_GENDER,
            "https://musicbrainz.org/artist/fa58cf24-0e44-421d-8519-8bf461dcfaa5",
        )
        is None
    )
    assert citation_problem(SourceKind.WIKIDATA_P21, "https://www.wikidata.org/wiki/mitski")
    assert (
        citation_problem(SourceKind.WIKIDATA_P21, "https://www.wikidata.org/wiki/Q16735549") is None
    )
    # A statement is cited by whatever URL carries it; no pattern is imposed.
    assert citation_problem(SourceKind.ARTIST_STATEMENT, "https://example.org/interview") is None


def test_a_registry_url_must_be_a_locator_whatever_the_source_kind() -> None:
    """The DISCOGS_LINEUP hole: exempt by kind, but still a registry address.

    The old reasoning — "a lineup is cited by whatever URL carries it, and
    inventing a pattern would reject honest citations" — holds for citations
    that are not registry addresses, and only for those. Discogs addresses
    artists by numeric id, so a bare slug locates nothing.
    """
    assert citation_problem(
        SourceKind.DISCOGS_LINEUP, "https://www.discogs.com/artist/big-thief"
    ), "a bare Discogs slug is not a locator"
    assert (
        citation_problem(
            SourceKind.DISCOGS_LINEUP, "https://www.discogs.com/artist/5009441-Big-Thief"
        )
        is None
    )
    # Still free-form when it is not a registry address.
    assert (
        citation_problem(SourceKind.DISCOGS_LINEUP, "https://en.wikipedia.org/wiki/Big_Thief")
        is None
    )
    # And the host rule binds every kind, not just the one that names the registry.
    assert citation_problem(
        SourceKind.ARTIST_STATEMENT, "https://www.wikidata.org/wiki/big-thief"
    ), "an artist statement may not claim a registry address it cannot resolve"

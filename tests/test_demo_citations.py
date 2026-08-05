"""Every identity citation the demo ships must locate a real record.

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
"""

from __future__ import annotations

from pipeline.demo import demo_catalog
from pipeline.identity import citation_problem
from pipeline.models import SourceKind


def test_every_demo_identity_citation_can_locate_its_record() -> None:
    bad: list[str] = []
    for artist in demo_catalog().values():
        for source in list(artist.identity.sources) + list(
            getattr(artist.composition, "sources", ()) if artist.composition else []
        ):
            problem = citation_problem(source.kind, source.citation)
            if problem is not None:
                bad.append(f"{artist.name}: {source.citation!r} {problem}")
    assert bad == [], "unresolvable identity citations in the shipped demo:\n" + "\n".join(bad)


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
    # A statement or lineup is cited by whatever URL carries it; no pattern is imposed.
    assert citation_problem(SourceKind.ARTIST_STATEMENT, "https://example.org/interview") is None

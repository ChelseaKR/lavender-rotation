"""Deep links to the *upstream, human-facing edit UI* for a sourced citation.

Part of EXP-05 ("Fix it at the source"): when a provenance item is wrong or
stale, the honest fix is to correct it at the source it came from, not to
quietly override it locally. This module is a pure, egress-free mapping from
a :class:`~recommender.why.ProvenanceItem` (or the
:class:`~pipeline.models.Source` it mirrors) to the edit page a person can
open in their own browser.

Guardrails, mirrored from the identity invariant (README / CONTRIBUTING):

* **String parsing only, no network.** This module never fetches anything —
  it only recognizes the shape of a citation URL this project already
  produces and rewrites it to the corresponding edit surface.
* **Never an auto-edit or API-write link.** Only the ordinary, human-facing
  edit UI is linked — Wikidata's own page (anchored at the relevant
  statement) or MusicBrainz's `/edit` form. There is no code path here that
  writes to an upstream source; a person always reviews and submits the edit
  themselves.
* **No guessing.** A citation that doesn't parse, or a source kind with no
  defined upstream edit surface (e.g. a Discogs lineup page has no single
  canonical "edit this claim" URL we can safely construct), returns ``None``
  rather than fabricating a link that might be wrong or misleading.
"""

from __future__ import annotations

import re

#: Wikidata QID, e.g. the "Q12345" in https://www.wikidata.org/wiki/Q12345
_WIKIDATA_QID = re.compile(r"(Q\d+)")

#: The artist-id path segment in a MusicBrainz artist URL, e.g.
#: https://musicbrainz.org/artist/<mbid-or-slug>. Real MusicBrainz artist ids
#: are UUIDs; fixture/demo citations in this project use readable slugs
#: instead, so this deliberately accepts either — it is a URL-shape parse,
#: not a validator of what MusicBrainz itself would accept.
_MUSICBRAINZ_ARTIST = re.compile(r"musicbrainz\.org/artist/([^/?#\s]+)")

#: Source kinds whose citation is a MusicBrainz artist page. Both the
#: individual-identity kind (``musicbrainz-gender``) and the
#: band-composition kind (``musicbrainz-relationship``) point at the same
#: kind of citation URL, so they share the same edit-link shape.
_MUSICBRAINZ_KINDS = frozenset({"musicbrainz-gender", "musicbrainz-relationship"})

#: Wikidata source kinds, mapped to the statement anchor on the entity page.
#: A dict rather than a chain of ``if``s so that adding a property means adding
#: a row: the P91 case (#92) was missing for as long as the P21 case was a
#: hand-written branch, and ``tests/test_upstream.py`` now asserts this covers
#: every ``SourceKind`` whose value starts with ``wikidata-``.
_WIKIDATA_ANCHORS: dict[str, str] = {
    "wikidata-p21": "#P21",
    "wikidata-p91": "#P91",
}


def upstream_edit_url(source_kind: str, citation: str) -> str | None:
    """Return the upstream *edit-UI* URL for one sourced citation, or ``None``.

    * ``wikidata-p21`` — the entity's own page, anchored at the P21 ("sex or
      gender") statement: ``https://www.wikidata.org/wiki/{Qid}#P21``. This is
      the safe choice: Wikidata does not honor a query-string that opens an
      edit form pre-filled for a specific claim, so this anchors the reader at
      the right statement on the entity page instead of fabricating one.
    * ``wikidata-p91`` — the same entity page, anchored at the P91 ("sexual
      orientation") statement (#92). ADR 0011 admits P91 for coverage while
      noting it is more often a biographer's characterization than the artist's
      own words, which makes it the citation here most likely to *need*
      correcting — and it was the one kind with no edit link at all, so the
      fix-at-source affordance existed for every claim except the one most
      likely to be wrong.
    * ``musicbrainz-gender`` / ``musicbrainz-relationship`` — the artist's
      ``/edit`` page: ``https://musicbrainz.org/artist/{id}/edit``.
    * Anything else (an unknown kind, a Discogs-lineup-only citation, an
      ``artist-statement`` whose citation is somebody's own website, or a
      citation that does not parse as one of the shapes above) — ``None``. No
      link is offered rather than a guessed one.
    """
    anchor = _WIKIDATA_ANCHORS.get(source_kind)
    if anchor is not None:
        match = _WIKIDATA_QID.search(citation)
        if match is None:
            return None
        return f"https://www.wikidata.org/wiki/{match.group(1)}{anchor}"
    if source_kind in _MUSICBRAINZ_KINDS:
        match = _MUSICBRAINZ_ARTIST.search(citation)
        if match is None:
            return None
        return f"https://musicbrainz.org/artist/{match.group(1)}/edit"
    return None

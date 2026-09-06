"""Census: what a cached world's identity coverage actually is, in counts only.

The research roadmap's central empirical claim — that **unknown is the common
case** — is cited from the literature (well under half of the humans on Wikidata
carry a sex-or-gender claim) and has never been measured on this tool's own
data. That asymmetry is the thing this module fixes: a reviewer, or the owner,
can now ask a cache what fraction of it resolves, which source did the work, how
stale the answers are, and *why* the unknowns are unknown.

**Counts only, and that is a design constraint rather than a convenience.**
Ideation E2 proposed a per-artist identity export and was rejected because it
would be exactly the redistributable musician-identity dataset this project
refuses to create. A census is the aggregate that answers the research question
without becoming that dataset, so nothing here may emit an artist id, an artist
name, or a citation URL, and ``tests/test_census.py`` asserts it over the real
demo world rather than trusting the code to stay that way.

Nothing is fetched. Every number is read from what the cache already holds.

**On ``unknown_reason``.** Only reasons the cache can actually support are
emitted:

``never-enriched``
    The artist is known to the world (it appears in the listening history) but
    has no enriched row at all, so nothing was ever asked about it.
``no-permitted-claim``
    The artist *was* enriched and came back carrying no individual-identity
    citation on any axis — upstream holds nothing this project is permitted to
    read.
``sourced-on-another-axis-only``
    Enriched, gender unknown, but a citation exists on the queer axis or the
    band-composition axis. Distinguished because it is a materially different
    state from "nothing is known about this act".

Notably absent is ``upstream-unreachable``. A refresh that got no answer is a
real and distinct reason for an unknown, and :class:`~pipeline.ingest.RefreshOutcome`
knows it at the time — but it is not persisted, so this module cannot tell it
apart from ``no-permitted-claim`` by reading the cache. Emitting it anyway, or
folding it into another bucket without saying so, would be the exact defect this
project keeps finding: an absence rendered as a measurement. It is listed in
:data:`UNSUPPORTED_UNKNOWN_REASONS` with the reason it cannot be produced, and
issue #122 is where persisting it belongs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from pipeline.models import (
    INDIVIDUAL_IDENTITY_SOURCES,
    Artist,
    Gender,
    IdentityBasis,
    SourceKind,
)

#: Bumped when the emitted JSON changes shape. A consumer that pins this can
#: tell a schema change from a data change.
SCHEMA_VERSION = "1"

#: ``unknown_reason`` codes this module can derive from a cache alone.
UNKNOWN_REASONS: tuple[str, ...] = (
    "never-enriched",
    "no-permitted-claim",
    "sourced-on-another-axis-only",
)

#: Reasons that are real but not derivable here, and why. Emitted in the JSON so
#: a reader knows the reason table is partial by construction rather than
#: assuming the codes above are exhaustive.
UNSUPPORTED_UNKNOWN_REASONS: Mapping[str, str] = {
    "upstream-unreachable": (
        "a refresh that got no answer is not recorded on the cached row, so it "
        "cannot be told apart from 'no-permitted-claim' by reading the cache "
        "(see issue #122)"
    ),
}

#: Age buckets for ``fetched_at``, in days, as (label, inclusive upper bound).
#: ``None`` is the open-ended final bucket.
_AGE_BUCKETS: tuple[tuple[str, Optional[int]], ...] = (
    ("0-7", 7),
    ("8-30", 30),
    ("31-90", 90),
    ("91-365", 365),
    ("over-365", None),
)

#: The bucket for an artist with no enriched row at all. Deliberately not folded
#: into ``over-365``: "never checked" and "checked a long time ago" are different
#: facts and only one of them is a measurement.
NEVER_FETCHED = "never"

#: The bucket for an artist that *was* enriched but carries no usable lineage
#: date — a fixture world such as the demo catalog, or an unparseable value.
#: Also kept apart from both ``never`` and ``over-365``: "we did not record when"
#: is not "we never asked", and it is certainly not an age.
LINEAGE_NOT_RECORDED = "not-recorded"


@dataclass(frozen=True)
class Census:
    """An aggregate readout of one cached world. No artist is identifiable here."""

    schema_version: str
    as_of: str
    #: Every artist the world knows about: enriched rows plus artists named by
    #: the listening history that were never enriched.
    total_artists: int
    enriched_artists: int
    by_gender: Mapping[str, int]
    by_basis: Mapping[str, int]
    #: Artists carrying at least one citation of each kind. Counted per artist,
    #: not per citation, so an artist with two MusicBrainz rows counts once and
    #: the numbers stay comparable to ``total_artists``.
    artists_by_source_kind: Mapping[str, int]
    #: Acts carrying a *sourced lineup*. Counted separately from
    #: ``by_basis["band-composition"]`` because they are different claims: an
    #: act's own gender label is basis ``band-composition`` only when a lineup
    #: established it, whereas most sourced-lineup acts keep an ``unknown``
    #: label of their own and carry the lineup alongside. Reporting only the
    #: former would say "0 band-composition" about a world full of sourced
    #: lineups.
    acts_with_sourced_composition: int
    conflicting: int
    with_local_correction: int
    with_pending_upstream_correction: int
    fetched_at_age_days: Mapping[str, int]
    unknown_reasons: Mapping[str, int]
    unsupported_unknown_reasons: Mapping[str, str]

    @property
    def sourced(self) -> int:
        """Artists whose gender is sourced — i.e. not first-class ``UNKNOWN``."""
        return self.total_artists - self.by_gender[str(Gender.UNKNOWN)]

    @property
    def unknown_fraction(self) -> float:
        """Share of the world that is ``UNKNOWN``.

        A world with no artists has no share — the caller gets ``0.0`` and the
        ``total_artists`` of ``0`` beside it, never a bare percentage standing
        in for a population that does not exist.
        """
        if not self.total_artists:
            return 0.0
        return self.by_gender[str(Gender.UNKNOWN)] / self.total_artists

    def to_dict(self) -> dict[str, object]:
        """The JSON shape. Keys are stable within a ``schema_version``."""
        return {
            "schema_version": self.schema_version,
            "as_of": self.as_of,
            "total_artists": self.total_artists,
            "enriched_artists": self.enriched_artists,
            "sourced": self.sourced,
            "unknown_fraction": round(self.unknown_fraction, 4),
            "by_gender": dict(self.by_gender),
            "by_basis": dict(self.by_basis),
            "artists_by_source_kind": dict(self.artists_by_source_kind),
            "acts_with_sourced_composition": self.acts_with_sourced_composition,
            "conflicting": self.conflicting,
            "with_local_correction": self.with_local_correction,
            "with_pending_upstream_correction": self.with_pending_upstream_correction,
            "fetched_at_age_days": dict(self.fetched_at_age_days),
            "unknown_reasons": dict(self.unknown_reasons),
            "unsupported_unknown_reasons": dict(self.unsupported_unknown_reasons),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    def to_text(self) -> str:
        """A plain table. Says "no artists" rather than printing a table of zeros."""
        if not self.total_artists:
            return (
                f"census (as of {self.as_of}): this world holds no artists — "
                "nothing has been ingested, so there is nothing to report.\n"
            )
        lines = [
            f"census (as of {self.as_of}): {self.total_artists} artist(s), "
            f"{self.enriched_artists} enriched",
            f"  sourced: {self.sourced}  unknown: "
            f"{self.by_gender[str(Gender.UNKNOWN)]} ({self.unknown_fraction:.0%}) "
            "— unknown is a normal, first-class outcome, never a gap",
            "",
            "  by sourced gender:",
        ]
        lines += [f"    {name:<24} {count}" for name, count in self.by_gender.items()]
        lines += ["", "  by basis:"]
        lines += [f"    {name:<24} {count}" for name, count in self.by_basis.items()]
        lines += ["", "  artists carrying a citation of kind:"]
        lines += [f"    {name:<24} {count}" for name, count in self.artists_by_source_kind.items()]
        lines += [
            "",
            f"  acts with a sourced lineup:  {self.acts_with_sourced_composition}",
            f"  sources disagree:            {self.conflicting}",
            f"  local corrections filed:     {self.with_local_correction}",
            f"  pending upstream corrections:{self.with_pending_upstream_correction}",
            "",
            "  lineage age (days since last enrichment):",
        ]
        lines += [f"    {name:<24} {count}" for name, count in self.fetched_at_age_days.items()]
        lines += ["", "  why the unknowns are unknown:"]
        lines += [f"    {name:<28} {count}" for name, count in self.unknown_reasons.items()]
        for reason, why in self.unsupported_unknown_reasons.items():
            lines.append(f"    {reason:<28} not derivable — {why}")
        return "\n".join(lines) + "\n"


def _age_bucket(fetched_at: Optional[str], as_of: date) -> str:
    """The age bucket for an *enriched* artist. Absence is its own bucket."""
    if not fetched_at:
        return LINEAGE_NOT_RECORDED
    try:
        when = datetime.strptime(fetched_at, "%Y-%m-%d").date()
    except ValueError:
        # An unparseable lineage date is not an old one. Counting it as
        # `over-365` would publish a guess as a measurement.
        return LINEAGE_NOT_RECORDED
    age = (as_of - when).days
    for label, upper in _AGE_BUCKETS:
        if upper is None or age <= upper:
            return label
    return _AGE_BUCKETS[-1][0]


def _unknown_reason(artist: Optional[Artist]) -> str:
    if artist is None:
        return "never-enriched"
    other_axis = bool(artist.queer.sources) or bool(
        artist.composition.sources if artist.composition is not None else ()
    )
    return "sourced-on-another-axis-only" if other_axis else "no-permitted-claim"


def census(
    catalog: Mapping[str, Artist],
    *,
    as_of: str,
    known_artist_ids: Iterable[str] = (),
    fetched_at: Mapping[str, Optional[str]] | None = None,
    local_correction_ids: Iterable[str] = (),
    pending_correction_ids: Iterable[str] = (),
) -> Census:
    """Aggregate one world. Pure: no cache, no clock, no network.

    ``catalog`` is the enriched rows. ``known_artist_ids`` names every artist the
    world knows about — the listening history's artists included, whether or not
    they were ever enriched. The difference between the two is not noise: it is
    the ``never-enriched`` bucket, and dropping it would let a cache with one
    enriched artist out of a thousand report 100% sourced coverage.
    """
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    fetched = dict(fetched_at or {})
    all_ids = sorted(set(catalog) | set(known_artist_ids))

    by_gender = {str(g): 0 for g in Gender}
    by_basis = {str(b): 0 for b in IdentityBasis}
    by_kind = {str(k): 0 for k in SourceKind}
    ages = {label: 0 for label, _ in _AGE_BUCKETS}
    ages[LINEAGE_NOT_RECORDED] = 0
    ages[NEVER_FETCHED] = 0
    reasons = dict.fromkeys(UNKNOWN_REASONS, 0)

    conflicting = 0
    sourced_composition = 0
    for artist_id in all_ids:
        artist = catalog.get(artist_id)
        if artist is None:
            # Known to the world, never enriched: unknown on every axis, by
            # absence rather than by an answer.
            by_gender[str(Gender.UNKNOWN)] += 1
            by_basis[str(IdentityBasis.UNKNOWN)] += 1
            ages[NEVER_FETCHED] += 1
            reasons["never-enriched"] += 1
            continue

        by_gender[str(artist.identity.gender)] += 1
        by_basis[str(artist.identity.basis)] += 1
        ages[_age_bucket(fetched.get(artist_id), as_of_date)] += 1
        if artist.identity.conflict:
            conflicting += 1
        if artist.identity.gender is Gender.UNKNOWN:
            reasons[_unknown_reason(artist)] += 1
        if artist.composition is not None and artist.composition.sources:
            sourced_composition += 1

        kinds = {source.kind for source in artist.identity.sources}
        kinds |= {source.kind for source in artist.queer.sources}
        if artist.composition is not None:
            kinds |= {source.kind for source in artist.composition.sources}
        for kind in kinds:
            by_kind[str(kind)] += 1

    known = set(all_ids)
    return Census(
        schema_version=SCHEMA_VERSION,
        as_of=as_of,
        total_artists=len(all_ids),
        enriched_artists=len([a for a in all_ids if a in catalog]),
        by_gender=by_gender,
        by_basis=by_basis,
        artists_by_source_kind=by_kind,
        acts_with_sourced_composition=sourced_composition,
        conflicting=conflicting,
        # Scoped to this world on purpose: a ledger row for an artist this world
        # has never heard of is not a fact about this world.
        with_local_correction=len({a for a in local_correction_ids if a in known}),
        with_pending_upstream_correction=len({a for a in pending_correction_ids if a in known}),
        fetched_at_age_days=ages,
        unknown_reasons=reasons,
        unsupported_unknown_reasons=dict(UNSUPPORTED_UNKNOWN_REASONS),
    )


def individual_identity_source_kinds() -> Sequence[str]:
    """The source kinds that may establish an individual's gender, as strings."""
    return tuple(sorted(str(kind) for kind in INDIVIDUAL_IDENTITY_SOURCES))

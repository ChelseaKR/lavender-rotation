"""Per-run identity-coverage readout — make 'unknown is first-class' *visible*.

The re-rank already knows, for every pick, whether its identity is **sourced**
(an individual self-identification, or a sourced band lineup)
or **unknown** (surfaced on musical similarity alone). This module turns that
already-computed fact into one honest, render-agnostic readout —
"N of K picks carry a sourced identity; M were surfaced on similarity alone" —
so a guarantee that is *mechanically true in code* (unknown is never down-ranked,
never dropped) is also **legible** in the product.

Two framing rules are baked in so the readout can never curdle into a scorecard
that pathologizes the common case:

* **Unknown is first-class.** It is described as "surfaced on musical similarity
  alone" — a normal, expected outcome — never a gap, a miss, or a failure. Source
  data is sparse (well under half of people on Wikidata carry a sex/gender claim),
  so a high unknown count is the *expected* state, not a defect.
* **Descriptive, not target-driven.** Nothing here feeds a score or a re-rank; it
  only *reports*. The numbers never change a recommendation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pipeline.models import Gender, IdentityBasis, Recommendation


@dataclass(frozen=True)
class IdentityCoverage:
    """A descriptive tally of how each pick's identity was established.

    ``self_identified + band_composition + unknown == total`` and, within the
    self-identified set, ``women + nonbinary + men + other == self_identified``.

    The two fractions are ``None`` over an empty run rather than ``0.0``: with no picks there is
    no share to report, and a zero would read as a measurement that never happened.
    """

    total: int
    self_identified: int  # sourced individual self-ID
    band_composition: int  # sourced band lineup (distinct from the act's own gender)
    unknown: int  # surfaced on musical similarity alone — first-class
    women: int
    nonbinary: int
    men: int
    other: int

    @property
    def sourced(self) -> int:
        """Picks whose identity basis is *not* unknown (individual or composition)."""
        return self.self_identified + self.band_composition

    @property
    def fractions_measured(self) -> bool:
        """Whether there was anything to take a fraction of."""
        return self.total > 0

    @property
    def sourced_fraction(self) -> float | None:
        """Share of picks carrying a sourced identity, or ``None`` over no picks.

        A run with no picks used to report ``0.0`` here, which reads as "none of them were
        sourced" -- a measurement -- when what happened is that there was nothing to measure.
        The tell is that the two fractions came back ``0.0`` and ``0.0``: over any real run they
        sum to one, so a pair that does not is not a distribution. ``None`` is the honest value,
        and ``total`` beside it is the denominator that says why.

        Same rule, same reason, as ``segment_retention`` reporting ``None`` for a segment that
        was absent from pure taste's top-k (#129).
        """
        return self.sourced / self.total if self.total else None

    @property
    def unknown_fraction(self) -> float | None:
        """Share of picks surfaced on similarity alone, or ``None`` over no picks."""
        return self.unknown / self.total if self.total else None

    def summary_line(self) -> str:
        """One honest sentence. Unknown is framed as normal, never as a failure."""
        if self.total == 0:
            return "No picks yet."
        sourced_bits = []
        if self.self_identified:
            sourced_bits.append(f"{self.self_identified} self-identified")
        if self.band_composition:
            sourced_bits.append(f"{self.band_composition} sourced from a band lineup")
        detail = f" ({', '.join(sourced_bits)})" if sourced_bits else ""
        return (
            f"{self.sourced} of {self.total} picks carry a sourced identity{detail}; "
            f"{self.unknown} surfaced on musical similarity alone — a normal, "
            f"first-class outcome that is never down-ranked."
        )

    def basis_breakdown(self) -> tuple[tuple[str, int], ...]:
        """Stable (label, count) rows for a table/list. Unknown is always shown."""
        return (
            ("Self-identified (woman)", self.women),
            ("Self-identified (nonbinary)", self.nonbinary),
            ("Self-identified (man)", self.men),
            ("Self-identified (other)", self.other),
            # Deliberately not "Sourced female-fronted band": this row counts
            # every pick whose basis is a sourced lineup, and the front-people
            # are not all women. Naming the row after one of the genders it
            # covers would misgender the rest.
            ("Sourced band lineup", self.band_composition),
            ("Unknown — surfaced on similarity alone", self.unknown),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "sourced": self.sourced,
            "self_identified": self.self_identified,
            "band_composition": self.band_composition,
            "unknown": self.unknown,
            "women": self.women,
            "nonbinary": self.nonbinary,
            "men": self.men,
            "other": self.other,
            # Null, not zero, over an empty run. A consumer that has to tell "no pick was
            # sourced" from "there was no pick" cannot do it from a number, and `total` is the
            # denominator that settles it. `fractions_measured` is published beside them so a
            # consumer can branch on the flag rather than on a null it might not expect.
            "fractions_measured": self.fractions_measured,
            "sourced_fraction": (
                round(self.sourced_fraction, 4) if self.sourced_fraction is not None else None
            ),
            "unknown_fraction": (
                round(self.unknown_fraction, 4) if self.unknown_fraction is not None else None
            ),
        }


def identity_coverage(recs: Sequence[Recommendation]) -> IdentityCoverage:
    """Tally identity coverage over a run's recommendations.

    Reads only data the pipeline already computed (each pick's
    :class:`~pipeline.models.IdentityBasis` and sourced
    :class:`~pipeline.models.Gender`); it never inspects, infers, or guesses
    anything new about an artist.
    """
    self_identified = band_composition = unknown = 0
    women = nonbinary = men = other = 0
    for rec in recs:
        basis = rec.explanation.identity_basis
        if basis is IdentityBasis.SELF_IDENTIFIED:
            self_identified += 1
            gender = rec.artist.identity.gender
            if gender is Gender.WOMAN:
                women += 1
            elif gender is Gender.NONBINARY:
                nonbinary += 1
            elif gender is Gender.MAN:
                men += 1
            else:
                other += 1
        elif basis is IdentityBasis.BAND_COMPOSITION:
            band_composition += 1
        else:
            unknown += 1
    return IdentityCoverage(
        total=len(recs),
        self_identified=self_identified,
        band_composition=band_composition,
        unknown=unknown,
        women=women,
        nonbinary=nonbinary,
        men=men,
        other=other,
    )

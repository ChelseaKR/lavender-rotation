"""Identity-blind content filters: tags and era, and nothing else.

A discovery tool that can only say "more of what you already play" goes stale — which is what
``--explore`` exists for. This is the other half a listener needs: "post-punk", "not country",
"since 2015". Those are questions about the *music*, and this module can only answer questions
about the music, by construction.

**Why a separate module from :mod:`recommender.filters`.** That module holds
``is_sourced_man_only``, which reads a sourced gender claim on purpose. This one must never read
anything of the kind, and the strongest available guarantee of that is the whole-module AST scan
:mod:`recommender.diversify` already lives under: every function in the file, every attribute it
touches, checked against the forbidden set. Two modules is what makes that scan applicable here;
putting these predicates in ``filters.py`` would have forced the guard down to a per-function
allowlist, which is a weaker promise and a maintenance trap. See
``tests/test_no_inference.py::test_content_filters_never_read_a_forbidden_attribute``.

**Why absence never excludes.** An artist with no tags, or with no known start year, is *kept* by
every filter here. This is the same rule that governs identity: an absent claim is not a claim.
It matters more than it looks. Tag and year coverage upstream is thinnest for the least
documented artists, which on MusicBrainz skews small, new, and independent — the artists this
project exists to surface. A filter that dropped them for having no metadata would quietly
re-impose the popularity bias the whole ranking is built to resist, while looking like a neutral
content preference. So the rule is: a filter removes only on a *positive* match against a value
the artist actually has.

**Where they apply.** Before scoring, on the candidate pool. Everything downstream — the values
lens, rank protection, the serendipity pass, the base-rank and lens-rank counterfactuals — then
runs unchanged over whatever survived, and the rank-shift sentence on each card stays a claim
about the lens rather than about the filter (#113).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

#: The years a filter will accept. Recorded music does not predate the first, and a filter bound
#: past the second is a typo rather than an era. Both ends are rejected loudly: silently clamping
#: a nonsense bound would return a list nobody asked for and say nothing about it.
MIN_YEAR = 1860
MAX_YEAR = 2200


def normalize_tag(tag: str) -> str:
    """Fold one tag to the form both sides of a comparison are held to.

    Upstream tags arrive as free text with inconsistent case, spacing and separators:
    ``Dream Pop``, ``dream-pop`` and ``dream pop`` are one tag as far as a listener is
    concerned, and a filter that treated them as three would answer "no matches" to a
    perfectly good request.
    """
    return " ".join(tag.replace("-", " ").replace("_", " ").split()).casefold()


def normalize_tags(tags: Iterable[str]) -> frozenset[str]:
    """Fold an iterable of tags, dropping any that fold to nothing.

    A bare string is refused rather than iterated: ``normalize_tags("shoegaze")`` would
    otherwise silently mean "the tags s, h, o, e...", and a filter built from it would match
    nothing while looking well-formed.
    """
    if isinstance(tags, str):
        raise TypeError("expected an iterable of tags, not a single string")
    folded = (normalize_tag(tag) for tag in tags)
    return frozenset(tag for tag in folded if tag)


class FilterSpecError(ValueError):
    """Raised when a filter is specified in a way that cannot be honored as written."""


@dataclass(frozen=True)
class ContentFilter:
    """One listener's identity-blind narrowing of the candidate pool.

    Every field is optional and the default instance is inert: :meth:`keeps` returns ``True`` for
    everything and :attr:`active` is ``False``, so an existing caller that does not pass one gets
    exactly today's behavior.
    """

    include_tags: frozenset[str] = frozenset()
    exclude_tags: frozenset[str] = frozenset()
    year_from: Optional[int] = None
    year_to: Optional[int] = None

    @classmethod
    def build(
        cls,
        *,
        include_tags: Iterable[str] = (),
        exclude_tags: Iterable[str] = (),
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> ContentFilter:
        """Normalize and validate a filter from raw CLI input."""
        include = normalize_tags(include_tags)
        exclude = normalize_tags(exclude_tags)
        both = include & exclude
        if both:
            # Not resolvable in the listener's favor in either direction, so it is refused
            # rather than silently decided. `exclude` winning would return an empty list;
            # `include` winning would return artists the listener asked not to see.
            raise FilterSpecError(
                "these tags are both included and excluded: " + ", ".join(sorted(both))
            )
        for label, year in (("--year-from", year_from), ("--year-to", year_to)):
            if year is not None and not (MIN_YEAR <= year <= MAX_YEAR):
                raise FilterSpecError(f"{label} must be between {MIN_YEAR} and {MAX_YEAR}")
        if year_from is not None and year_to is not None and year_from > year_to:
            raise FilterSpecError("--year-from is later than --year-to, which matches nothing")
        return cls(include_tags=include, exclude_tags=exclude, year_from=year_from, year_to=year_to)

    @property
    def active(self) -> bool:
        """True when this filter can remove anything at all."""
        return bool(
            self.include_tags
            or self.exclude_tags
            or self.year_from is not None
            or self.year_to is not None
        )

    def keeps_tags(self, tags: Iterable[str]) -> bool:
        """Whether an artist's tags survive the tag half. Absence is always kept."""
        folded = normalize_tags(tags)
        if not folded:
            return True
        if self.exclude_tags and (folded & self.exclude_tags):
            return False
        return not (self.include_tags and not (folded & self.include_tags))

    def keeps_year(self, year: Optional[int]) -> bool:
        """Whether a start year survives the era half. ``None`` is always kept."""
        if year is None:
            return True
        if self.year_from is not None and year < self.year_from:
            return False
        return not (self.year_to is not None and year > self.year_to)

    def describe(self) -> str:
        """The one-line statement every surface prints, so the result is never unexplained."""
        if not self.active:
            return "filters: none"
        parts: list[str] = []
        if self.include_tags:
            parts.append(f"include-tags={', '.join(sorted(self.include_tags))}")
        if self.exclude_tags:
            parts.append(f"exclude-tags={', '.join(sorted(self.exclude_tags))}")
        if self.year_from is not None:
            parts.append(f"year-from={self.year_from}")
        if self.year_to is not None:
            parts.append(f"year-to={self.year_to}")
        return (
            "filters: "
            + "; ".join(parts)
            + " (artists with no tags, or no known start year, are kept)"
        )


#: The inert filter. Passing this is identical to passing nothing.
NO_FILTER = ContentFilter()

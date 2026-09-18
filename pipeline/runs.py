"""Reproducible run manifests, and a diff that names *why* a list moved.

The repository already guarantees seeded reproducibility inside one process. It
does not let a listener answer "why is this week's list different from last
week's?" without diffing terminal output by eye. A manifest makes each run a
citable artifact; :func:`diff_runs` turns the per-card rank-shift transparency
into a statement between two runs.

**Attribution is a claim, so it is made only where the record supports it.**
:mod:`recommender.hybrid` stamps three ranks precisely so that movement can be
separated by mechanism (#113): ``base_rank`` before the lens, ``lens_rank``
immediately after it, ``rank`` after the identity-blind serendipity pass and the
listener's ``hide_sourced_men`` subtraction. A diff that recomputed causes from
``rank`` alone would undo that and would look right while doing it.

So the diff decomposes each artist's movement into the three intervals the
pipeline actually recorded, and attributes a shift to a mechanism **only when
exactly one of them moved**. When more than one moved, the honest answer is
:data:`CAUSE_UNDETERMINED` with the candidates named — not whichever cause is
checked first. That case is the common one in real use: change the lens and add
a thumbs-down between two runs and no record can separate their contributions.

A shift whose base-rank interval moved is upstream of the lens, and three
different things live up there — the listening profile, the feedback ledger, and
the content filter's narrowing of the candidate pool. The manifest carries a
digest of each, so the same rule applies one level down: exactly one changed
means it is named, more than one means the cause is undetermined and the
candidates are listed.

**What a manifest holds.** Artist ids, names, the three ranks, the identity
segment and basis a run already computed, and the coverage and exposure figures
it already published. It never holds listening history, play counts, or an
inferred field: the profile, the feedback ledger and the filter appear only as
digests, which compare without disclosing. There is no slot for an identity a
source did not assert.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from recommender.content_filters import ContentFilter
from recommender.coverage import identity_coverage
from recommender.exposure import exposure_at_k, identity_segment

from pipeline.models import Recommendation
from pipeline.paths import resolve_data_dir

#: Manifest format version. Its own sequence: a manifest and a cache row change
#: for unrelated reasons, and a reader handed an unknown one must stop rather
#: than guess at a field it does not have.
RUN_MANIFEST_SCHEMA_VERSION = "1.0"

#: Directory under the resolved data dir where manifests are written.
RUNS_DIRNAME = "runs"

#: Default number of manifests `prune` keeps. Manifests are small, but they are
#: written on every run, so an unbounded directory is a slow leak.
DEFAULT_KEEP = 200

#: Refuse to read a manifest larger than this. A run's manifest is a few tens of
#: kilobytes; anything past this is not one, and parsing it anyway is how a
#: local-first tool ends up with an unbounded read.
MAX_MANIFEST_BYTES = 4 * 1024 * 1024

#: What a diff says when the record cannot single out a mechanism. Not a cause.
CAUSE_UNDETERMINED = "cause-not-determined"

#: The three intervals `recommender.hybrid` records, and what moved them.
CAUSE_LENS = "lens"
CAUSE_EXPLORE = "explore"
CAUSE_UPSTREAM = "upstream"

#: Upstream sub-causes, distinguishable only when exactly one digest changed.
CAUSE_PROFILE = "profile"
CAUSE_FEEDBACK = "feedback"
CAUSE_FILTER = "content-filter"

#: A diff refuses to run across these unless the caller opts in. Each makes the
#: two runs answers to *different questions*, so "these artists left the top-k"
#: would be true and misleading at once.
COMPARABLE_FIELDS = ("listener_digest", "lens_name", "content_filter")


class RunManifestError(ValueError):
    """A manifest that cannot be read, or two that must not be compared."""


def _digest(payload: object) -> str:
    """A short, stable digest of a JSON-serializable value.

    Used for the profile, the feedback ledger, the listener and the filter. A
    digest compares without disclosing, which is what lets a manifest say "the
    listening profile changed between these runs" while holding no listening
    history at all.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def listener_digest(username: str) -> str:
    """Stable per-listener identity. Changes only when the listener does."""
    return _digest(["listener", username])


def profile_digest(username: str, artist_ids: Iterable[str], total_plays: int) -> str:
    """Digest of *what was listened to*, not of the listening itself.

    Takes the artist ids the profile covers and its play total, never the
    per-artist counts: two profiles that differ produce different digests, and
    the digest discloses neither the artists nor the history.
    """
    return _digest(["profile", username, sorted(artist_ids), total_plays])


def feedback_digest(votes: Iterable[tuple[str, int]]) -> str:
    """Digest of the feedback ledger as (artist_id, vote) pairs."""
    return _digest(["feedback", sorted((str(a), int(v)) for a, v in votes)])


def filter_description(content_filter: ContentFilter | None) -> dict[str, Any]:
    """Serialize the content filter, keeping "inert" distinct from "absent"."""
    if content_filter is None:
        return {"stated": False, "active": False}
    return {
        "stated": True,
        "active": content_filter.active,
        "include_tags": sorted(content_filter.include_tags),
        "exclude_tags": sorted(content_filter.exclude_tags),
        "year_from": content_filter.year_from,
        "year_to": content_filter.year_to,
    }


@dataclass(frozen=True)
class RunEntry:
    """One recommendation as the manifest records it."""

    artist_id: str
    name: str
    rank: int
    base_rank: int
    lens_rank: int
    segment: str
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artist_id": self.artist_id,
            "name": self.name,
            "rank": self.rank,
            "base_rank": self.base_rank,
            "lens_rank": self.lens_rank,
            "segment": self.segment,
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunEntry:
        return cls(
            artist_id=str(payload["artist_id"]),
            name=str(payload["name"]),
            rank=int(payload["rank"]),
            base_rank=int(payload["base_rank"]),
            lens_rank=int(payload["lens_rank"]),
            segment=str(payload["segment"]),
            basis=str(payload["basis"]),
        )


@dataclass(frozen=True)
class RunManifest:
    """One recorded run: the question that was asked, and the answer given."""

    run_id: str
    created_at: str
    surface: str
    listener_digest: str
    profile_digest: str
    feedback_digest: str
    lens_name: str
    lens_strength: float
    explore: float
    hide_sourced_men: bool
    k: int
    content_filter: dict[str, Any]
    cache_schema_version: int
    coverage: dict[str, Any]
    exposure: dict[str, float | None]
    entries: list[RunEntry] = field(default_factory=list)
    schema_version: str = RUN_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "surface": self.surface,
            "listener_digest": self.listener_digest,
            "profile_digest": self.profile_digest,
            "feedback_digest": self.feedback_digest,
            "lens_name": self.lens_name,
            "lens_strength": self.lens_strength,
            "explore": self.explore,
            "hide_sourced_men": self.hide_sourced_men,
            "k": self.k,
            "content_filter": self.content_filter,
            "cache_schema_version": self.cache_schema_version,
            "coverage": self.coverage,
            "exposure": self.exposure,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunManifest:
        version = str(payload.get("schema_version", ""))
        if version != RUN_MANIFEST_SCHEMA_VERSION:
            # Named, not merely rejected: a reader told only "unsupported" has to
            # open the file to learn what it was.
            raise RunManifestError(
                f"unsupported run manifest schema version {version!r}; "
                f"this build reads {RUN_MANIFEST_SCHEMA_VERSION!r}"
            )
        try:
            return cls(
                schema_version=version,
                run_id=str(payload["run_id"]),
                created_at=str(payload["created_at"]),
                surface=str(payload["surface"]),
                listener_digest=str(payload["listener_digest"]),
                profile_digest=str(payload["profile_digest"]),
                feedback_digest=str(payload["feedback_digest"]),
                lens_name=str(payload["lens_name"]),
                lens_strength=float(payload["lens_strength"]),
                explore=float(payload["explore"]),
                hide_sourced_men=bool(payload["hide_sourced_men"]),
                k=int(payload["k"]),
                content_filter=dict(payload["content_filter"]),
                cache_schema_version=int(payload["cache_schema_version"]),
                coverage=dict(payload["coverage"]),
                exposure=dict(payload["exposure"]),
                entries=[RunEntry.from_dict(item) for item in payload["entries"]],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RunManifestError(f"malformed run manifest: {exc}") from exc

    def by_artist(self) -> dict[str, RunEntry]:
        return {entry.artist_id: entry for entry in self.entries}


def build_manifest(
    *,
    surface: str,
    recs: Sequence[Recommendation],
    username: str,
    profile_artist_ids: Iterable[str],
    profile_total_plays: int,
    votes: Iterable[tuple[str, int]],
    lens_name: str,
    lens_strength: float,
    explore: float,
    hide_sourced_men: bool,
    k: int,
    content_filter: ContentFilter | None,
    cache_schema_version: int,
    now: datetime | None = None,
) -> RunManifest:
    """Record one run. Reads only what the run already computed."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    entries = [
        RunEntry(
            artist_id=rec.artist.artist_id,
            name=rec.artist.name,
            rank=rec.rank,
            base_rank=rec.base_rank,
            lens_rank=rec.lens_rank,
            segment=identity_segment(rec.artist),
            basis=str(rec.artist.identity.basis),
        )
        for rec in recs
    ]
    return RunManifest(
        run_id=stamp.strftime("%Y%m%dT%H%M%S%f"),
        created_at=stamp.isoformat(),
        surface=surface,
        listener_digest=listener_digest(username),
        profile_digest=profile_digest(username, profile_artist_ids, profile_total_plays),
        feedback_digest=feedback_digest(votes),
        lens_name=lens_name,
        lens_strength=lens_strength,
        explore=explore,
        hide_sourced_men=hide_sourced_men,
        k=k,
        content_filter=filter_description(content_filter),
        cache_schema_version=cache_schema_version,
        coverage=identity_coverage(recs).to_dict(),
        # `exposure_at_k` returns None per segment over an empty list rather than
        # a table of zeroes, and that distinction is carried into the manifest
        # unchanged: a run with no slots is not a run where nobody held one.
        exposure=exposure_at_k(list(recs), max(1, k)),
        entries=entries,
    )


def runs_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or resolve_data_dir()) / RUNS_DIRNAME


def write_manifest(manifest: RunManifest, data_dir: Path | None = None) -> Path:
    """Write one manifest and return its path."""
    directory = runs_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{manifest.run_id}-{manifest.surface}.json"
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def list_manifest_paths(data_dir: Path | None = None) -> list[Path]:
    """Manifest paths, newest last. Run ids sort chronologically by construction."""
    directory = runs_dir(data_dir)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def read_manifest(path: Path) -> RunManifest:
    if not path.is_file():
        raise RunManifestError(f"no such run manifest: {path}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise RunManifestError(
            f"{path.name} is larger than {MAX_MANIFEST_BYTES} bytes; refusing to read it"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunManifestError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunManifestError(f"{path.name} is not a run manifest object")
    return RunManifest.from_dict(payload)


def find_manifest(run_id: str, data_dir: Path | None = None) -> Path:
    """Resolve a run id, accepting an unambiguous prefix."""
    matches = [p for p in list_manifest_paths(data_dir) if p.name.startswith(run_id)]
    if not matches:
        raise RunManifestError(f"no run manifest matches {run_id!r}")
    if len(matches) > 1:
        names = ", ".join(p.stem for p in matches)
        raise RunManifestError(f"{run_id!r} matches more than one run: {names}")
    return matches[0]


def prune_manifests(keep: int = DEFAULT_KEEP, data_dir: Path | None = None) -> list[Path]:
    """Delete all but the newest ``keep`` manifests; return what was removed."""
    if keep < 0:
        raise ValueError("keep must not be negative")
    paths = list_manifest_paths(data_dir)
    removed = paths[: max(0, len(paths) - keep)]
    for path in removed:
        path.unlink()
    return removed


def _delta(before: Any, after: Any) -> float | None:
    """Difference between two published figures, or ``None`` if either is absent.

    Both :meth:`recommender.coverage.IdentityCoverage.to_dict` and
    :func:`recommender.exposure.exposure_at_k` publish ``None`` where a figure
    was not measurable, and a difference taken against a value nobody measured is
    not zero. Subtracting them into ``0.0`` here would put the absence back as a
    number in the one document written to be cited.
    """
    if before is None or after is None:
        return None
    if isinstance(before, bool) or isinstance(after, bool):
        return None
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    return round(float(after) - float(before), 6)


def _upstream_causes(before: RunManifest, after: RunManifest) -> list[str]:
    """Which recorded upstream inputs changed between two runs."""
    changed = []
    if before.profile_digest != after.profile_digest:
        changed.append(CAUSE_PROFILE)
    if before.feedback_digest != after.feedback_digest:
        changed.append(CAUSE_FEEDBACK)
    if before.content_filter != after.content_filter:
        changed.append(CAUSE_FILTER)
    return changed


def attribute_shift(
    before: RunEntry, after: RunEntry, upstream_causes: Sequence[str]
) -> tuple[str, list[str]]:
    """Name the mechanism behind one artist's movement, or refuse to.

    The three intervals are the ones :mod:`recommender.hybrid` stamps, and they
    are the only decomposition the record supports. A mechanism is named only
    when exactly one interval moved; otherwise the answer is
    :data:`CAUSE_UNDETERMINED` with the movers listed, because a diff that
    attributes every movement to *something* is the #113 defect wearing a
    different hat.
    """
    intervals = (
        (CAUSE_UPSTREAM, after.base_rank - before.base_rank),
        (CAUSE_LENS, (after.lens_rank - after.base_rank) - (before.lens_rank - before.base_rank)),
        (CAUSE_EXPLORE, (after.rank - after.lens_rank) - (before.rank - before.lens_rank)),
    )
    moved = [name for name, delta in intervals if delta]
    if len(moved) != 1:
        return CAUSE_UNDETERMINED, moved
    if moved[0] != CAUSE_UPSTREAM:
        return moved[0], list(moved)
    # The base-rank interval moved, which is upstream of the lens. Three
    # recorded things live up there; one changed means it is named, and anything
    # else -- including *nothing* recorded having changed -- means it is not.
    candidates = list(upstream_causes) or [CAUSE_UPSTREAM]
    if len(candidates) == 1 and candidates[0] != CAUSE_UPSTREAM:
        return candidates[0], candidates
    return CAUSE_UNDETERMINED, candidates


@dataclass(frozen=True)
class RankShift:
    """One artist that appears in both runs at different displayed ranks."""

    artist_id: str
    name: str
    from_rank: int
    to_rank: int
    cause: str
    candidates: list[str] = field(default_factory=list)

    @property
    def delta(self) -> int:
        return self.to_rank - self.from_rank

    def to_dict(self) -> dict[str, Any]:
        return {
            "artist_id": self.artist_id,
            "name": self.name,
            "from_rank": self.from_rank,
            "to_rank": self.to_rank,
            "delta": self.delta,
            "cause": self.cause,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class RunDiff:
    """What changed between two runs, and what the record can say about why."""

    before_run_id: str
    after_run_id: str
    entered: list[RunEntry]
    left: list[RunEntry]
    shifts: list[RankShift]
    held: int
    coverage_delta: dict[str, float | None]
    exposure_delta: dict[str, float | None]
    changed_inputs: list[str]
    mixed_fields: list[str]

    @property
    def unchanged(self) -> bool:
        return not self.entered and not self.left and not self.shifts

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_run_id": self.before_run_id,
            "after_run_id": self.after_run_id,
            "entered": [entry.to_dict() for entry in self.entered],
            "left": [entry.to_dict() for entry in self.left],
            "shifts": [shift.to_dict() for shift in self.shifts],
            "held": self.held,
            "coverage_delta": self.coverage_delta,
            "exposure_delta": self.exposure_delta,
            "changed_inputs": list(self.changed_inputs),
            "mixed_fields": list(self.mixed_fields),
            "unchanged": self.unchanged,
        }

    def summary_lines(self) -> list[str]:
        lines: list[str] = [f"{self.before_run_id} -> {self.after_run_id}"]
        if self.mixed_fields:
            lines.append(
                "compared across differing "
                + ", ".join(self.mixed_fields)
                + " (--allow-mixed): these runs answer different questions"
            )
        if self.unchanged:
            lines.append("no changes: same artists, same ranks")
        for entry in self.entered:
            lines.append(f"+ {entry.name} entered at {entry.rank}")
        for entry in self.left:
            lines.append(f"- {entry.name} left (was {entry.rank})")
        for shift in self.shifts:
            direction = "up" if shift.delta < 0 else "down"
            if shift.cause == CAUSE_UNDETERMINED:
                named = ", ".join(shift.candidates) or "nothing recorded"
                why = f"cause not determined; more than one thing moved ({named})"
            else:
                why = f"cause: {shift.cause}"
            lines.append(
                f"~ {shift.name} {shift.from_rank} -> {shift.to_rank} "
                f"({direction} {abs(shift.delta)}); {why}"
            )
        if self.changed_inputs:
            lines.append("upstream inputs that changed: " + ", ".join(self.changed_inputs))
        return lines


def diff_runs(before: RunManifest, after: RunManifest, *, allow_mixed: bool = False) -> RunDiff:
    """Compare two recorded runs.

    Refuses runs that are answers to different questions -- a different listener,
    lens, or content filter -- unless ``allow_mixed`` is set, because "these
    artists left the top-k" is true and misleading at the same time when the
    question changed underneath it.
    """
    mixed = [name for name in COMPARABLE_FIELDS if getattr(before, name) != getattr(after, name)]
    if mixed and not allow_mixed:
        raise RunManifestError(
            "refusing to diff runs that differ in " + ", ".join(mixed) + "; pass --allow-mixed "
            "to compare them anyway, and read the result as two answers to different questions"
        )

    before_by_id, after_by_id = before.by_artist(), after.by_artist()
    upstream = _upstream_causes(before, after)

    entered = [after_by_id[a] for a in after_by_id if a not in before_by_id]
    left = [before_by_id[a] for a in before_by_id if a not in after_by_id]
    shifts: list[RankShift] = []
    held = 0
    for artist_id, after_entry in after_by_id.items():
        before_entry = before_by_id.get(artist_id)
        if before_entry is None:
            continue
        if before_entry.rank == after_entry.rank:
            held += 1
            continue
        cause, candidates = attribute_shift(before_entry, after_entry, upstream)
        shifts.append(
            RankShift(
                artist_id=artist_id,
                name=after_entry.name,
                from_rank=before_entry.rank,
                to_rank=after_entry.rank,
                cause=cause,
                candidates=candidates,
            )
        )

    entered.sort(key=lambda e: e.rank)
    left.sort(key=lambda e: e.rank)
    shifts.sort(key=lambda s: s.to_rank)
    coverage_delta = {
        key: _delta(before.coverage.get(key), value) for key, value in after.coverage.items()
    }
    exposure_delta = {
        key: _delta(before.exposure.get(key), value) for key, value in after.exposure.items()
    }
    return RunDiff(
        before_run_id=before.run_id,
        after_run_id=after.run_id,
        entered=entered,
        left=left,
        shifts=shifts,
        held=held,
        coverage_delta=coverage_delta,
        exposure_delta=exposure_delta,
        changed_inputs=upstream,
        mixed_fields=mixed,
    )

"""A local pending-corrections list — the other half of "fix it at the source".

EXP-05's round-trip is: a provenance item is wrong or stale -> the reader
opens the pre-filled upstream edit link (:mod:`recommender.upstream`) ->
they file a *local* note of what they proposed here -> they make the real
edit on Wikidata/MusicBrainz themselves -> the next ``wad refresh`` re-fetches
and reports the change (:class:`~pipeline.ingest.IdentityLabelChange`) ->
:func:`reconcile` clears the matching pending row.

This module never talks to a network. It is a small JSON file next to the
local cache (Quality §9's data-lineage discipline extended to corrections:
every row records what was filed, when, and against which citation).

**A filed row is never deleted without evidence (#70).** Reconciliation used to
match on ``(artist_id, source_kind)`` alone, so *any* change on that key cleared
the row — including a change where only ``retrieved_at`` moved and the asserted
value was byte-identical. The proposed value was never read. A person filed a
note saying a database had their gender wrong, and the next ordinary refresh
deleted it while reporting success. Now:

* a row is reconciled only when the value upstream now asserts **is the value
  that was proposed** (compared through the controlled vocabulary, so
  ``"female"`` reconciles a ``"woman"`` proposal);
* a date-only change is not evidence of an edit and reconciles nothing;
* a change to some *other* value marks the row **superseded** — it stays in the
  file, carrying what upstream now says, and is reported. Nothing vanishes;
* reconciliation runs only when an upstream source was actually consulted. The
  shipped ``wad refresh`` is demo-only and queries nothing, so it reconciles
  nothing and says so, instead of reporting "reconciled N pending upstream
  correction(s)" with no upstream behind the word.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional

from pipeline.cache import DEFAULT_DB_PATH
from pipeline.identity import normalise_asserted_value
from pipeline.ingest import IdentityLabelChange
from pipeline.models import SourceKind

#: Conventional location: alongside the cache db, e.g. ``data/pending-corrections.json``.
DEFAULT_CORRECTIONS_PATH: Path = DEFAULT_DB_PATH.parent / "pending-corrections.json"


@dataclass(frozen=True)
class PendingCorrection:
    """One filed-but-not-yet-reconciled correction request.

    Filing this never edits anything — it is a local note of *what a person
    believes is right and why*, made the moment they open the upstream edit
    link. The actual edit happens in the upstream UI, by that person, in
    their own browser. ``edit_url`` records which link was offered so the
    round-trip is auditable end to end.
    """

    artist_id: str
    source_kind: str
    citation: str
    current_value: str
    proposed_value: str
    note: str
    filed_at: str
    edit_url: Optional[str] = None
    #: Set when a refresh observed this source asserting something *other* than
    #: what was proposed. The row stays on file — the person's note is still
    #: open, and now records that upstream moved somewhere else (#70).
    superseded_by_value: Optional[str] = None
    #: The date that observation was made.
    superseded_at: Optional[str] = None

    @property
    def is_superseded(self) -> bool:
        return self.superseded_by_value is not None

    def describe(self) -> str:
        """One line for the CLI listing, including any superseding observation."""
        line = (
            f"{self.artist_id} [{self.source_kind}] {self.current_value!r} -> "
            f"{self.proposed_value!r} — filed {self.filed_at}: {self.note}"
        )
        if self.is_superseded:
            line += (
                f"\n  still open — upstream now asserts {self.superseded_by_value!r}"
                f" (observed {self.superseded_at})"
            )
        return line


def default_path(db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    """The conventional corrections-file location alongside a given cache db."""
    return Path(db_path).parent / "pending-corrections.json"


def _read_all(path: str | Path) -> list[PendingCorrection]:
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [PendingCorrection(**row) for row in raw]


def _write_all(path: str | Path, corrections: Sequence[PendingCorrection]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([asdict(c) for c in corrections], indent=2) + "\n",
        encoding="utf-8",
    )


def add_correction(
    path: str | Path,
    *,
    artist_id: str,
    source_kind: str,
    citation: str,
    current_value: str,
    proposed_value: str,
    note: str,
    filed_at: str,
    edit_url: Optional[str] = None,
) -> PendingCorrection:
    """File and persist a new pending correction. Returns the stored row."""
    corrections = _read_all(path)
    row = PendingCorrection(
        artist_id=artist_id,
        source_kind=source_kind,
        citation=citation,
        current_value=current_value,
        proposed_value=proposed_value,
        note=note,
        filed_at=filed_at,
        edit_url=edit_url,
    )
    corrections.append(row)
    _write_all(path, corrections)
    return row


def list_corrections(path: str | Path) -> list[PendingCorrection]:
    """Return every pending correction currently on file (empty if none)."""
    return _read_all(path)


@dataclass(frozen=True)
class ReconcileOutcome:
    """Everything reconciliation did, named. Nothing is dropped unreported.

    ``reconciled`` rows are the completed round-trips: upstream now asserts what
    the person proposed, so the note has done its job and leaves the file.
    ``superseded`` rows stay on file — upstream moved, but to something else.
    ``still_open`` rows saw no value change at all.
    """

    reconciled: tuple[PendingCorrection, ...] = ()
    superseded: tuple[PendingCorrection, ...] = ()
    still_open: tuple[PendingCorrection, ...] = ()
    #: False when no upstream source was consulted, in which case no upstream
    #: edit *could* have landed and nothing is reconciled regardless of input.
    upstream_queried: bool = True

    def report_lines(self) -> list[str]:
        """The operator-facing summary, written here rather than in the CLI.

        ``pipeline/cli.py`` sits in ``[tool.coverage.run] omit`` as thin argparse
        glue, which is how the old, wrong summary line went unmeasured (#70).
        This lives in the covered module instead.
        """
        if not self.upstream_queried:
            open_rows = len(self.still_open) + len(self.superseded)
            return [
                "reconciled 0 pending correction(s) — no upstream identity source was "
                "queried, so no upstream edit could have landed",
                f"{open_rows} pending correction(s) still open",
            ]
        lines = [f"reconciled {len(self.reconciled)} pending upstream correction(s)"]
        lines += [
            f"  reconciled {row.artist_id} [{row.source_kind}]: upstream now asserts "
            f"{row.proposed_value!r}, as proposed"
            for row in self.reconciled
        ]
        lines += [
            f"  still open: {row.artist_id} [{row.source_kind}] — upstream now asserts "
            f"{row.superseded_by_value!r}, you proposed {row.proposed_value!r}"
            for row in self.superseded
        ]
        remaining = len(self.superseded) + len(self.still_open)
        lines.append(f"{remaining} pending correction(s) still open")
        return lines


def _same_claim(source_kind: str, proposed: str, observed: str) -> bool:
    """Do two *asserted* values state the same thing for this source kind?

    Compared through the controlled vocabulary, so a ``"woman"`` proposal is
    satisfied by an upstream ``"female"`` and by Wikidata's ``"Q6581072"``.
    Values the vocabulary does not cover fall back to a literal, case-folded
    comparison — never to a guess about what they might mean.
    """
    try:
        kind = SourceKind(source_kind)
    except ValueError:
        kind = None
    if kind is not None:
        proposed_gender = normalise_asserted_value(kind, proposed)
        observed_gender = normalise_asserted_value(kind, observed)
        if proposed_gender is not None and observed_gender is not None:
            return proposed_gender is observed_gender
    return proposed.strip().casefold() == observed.strip().casefold()


def _value_changes_by_key(
    changes: Iterable[IdentityLabelChange],
) -> dict[tuple[str, str], IdentityLabelChange]:
    """Index the changes that actually moved a *value*, keyed by artist+source.

    Date-only changes are dropped here. ``pipeline.ingest._diff_sources`` keeps
    emitting them — a fresh ``retrieved_at`` is real lineage and belongs in the
    change log — but a retrieval date moving is not evidence that anybody edited
    anything, and it must never clear a person's filed note (#70).
    """
    return {
        (change.artist_id, change.source_kind): change
        for change in changes
        if change.old_value != change.new_value
    }


def reconcile(
    path: str | Path,
    changes: Iterable[IdentityLabelChange],
    *,
    observed_at: str,
) -> ReconcileOutcome:
    """Resolve pending corrections against values an upstream source now asserts.

    A row is reconciled — and only then dropped from the file — when a change on
    its ``(artist_id, source_kind)`` shows the source now asserting *the value
    that was proposed*. A change to any other value marks the row superseded and
    **keeps** it, recording what upstream said instead. A change that moved only
    the retrieval date is not a change for this purpose at all.

    ``observed_at`` is the date of the observation, stamped onto superseded rows
    so the ledger keeps its lineage.
    """
    pending = _read_all(path)
    if not pending:
        return ReconcileOutcome()

    by_key = _value_changes_by_key(changes)
    reconciled: list[PendingCorrection] = []
    superseded: list[PendingCorrection] = []
    still_open: list[PendingCorrection] = []
    for row in pending:
        change = by_key.get((row.artist_id, row.source_kind))
        if change is None:
            still_open.append(row)
        elif _same_claim(row.source_kind, row.proposed_value, change.new_value):
            reconciled.append(row)
        else:
            superseded.append(
                replace(
                    row,
                    superseded_by_value=change.new_value,
                    superseded_at=observed_at,
                )
            )

    if reconciled or superseded:
        _write_all(path, [*superseded, *still_open])
    return ReconcileOutcome(
        reconciled=tuple(reconciled),
        superseded=tuple(superseded),
        still_open=tuple(still_open),
    )


def reconcile_after_refresh(
    path: str | Path,
    changes: Iterable[IdentityLabelChange],
    *,
    upstream_queried: bool,
    observed_at: str,
) -> ReconcileOutcome:
    """Reconcile only if an upstream source was actually consulted.

    The shipped ``wad refresh`` rewrites the committed fixture catalog and
    performs no network fetch (``pipeline.ingest.refresh_catalog``'s dict
    branch), so it passes ``upstream_queried=False`` and nothing is reconciled —
    no upstream edit *could* have landed. When the deferred live-enrichment work
    (FIX-01) wires a real :class:`~pipeline.enrich.EnrichmentSource`, that path
    passes ``True`` and the round-trip closes for real.
    """
    if not upstream_queried:
        return ReconcileOutcome(still_open=tuple(_read_all(path)), upstream_queried=False)
    return reconcile(path, changes, observed_at=observed_at)

"""One versioned JSON document per invocation, and the schemas that describe them.

The project's guarantees are strongest when a machine can check them. "Unknown is
never down-ranked", "every pick has a why", "identity is never inferred", "the
tool contacts these hosts and no others" are asserted today by tests over Python
objects, which means a third party has to take them on trust: nothing they can
run from the command line reports them.

These documents make three of them externally checkable:

* ``recommend`` carries, per pick, the *basis* on which identity was established
  and the citations behind it. The schema has **no slot for an inferred value**:
  ``inferred`` is ``const: false`` and a sourced gender without provenance is a
  schema error, so a reviewer can run ``recommend --json`` and confirm the
  no-inference property structurally rather than by reading prose.
* ``export`` wraps whatever the chosen portable format already produces, so the
  envelope cannot carry a field the format does not. It adds only metadata:
  which format, how many entries, when.
* ``doctor`` reports the egress allowlist — the modules this project permits to
  open a socket and the hosts they reach — so a reader can see what the tool is
  allowed to contact without reading its test suite. It also reports
  ``upstream_checked``, because "no upstream failure" and "upstream not probed"
  are different findings and must not render the same.

Two shape rules, both the same rule:

* **Absence is a value, not a gap.** An unmeasurable share is ``null`` and never
  ``0.0``; an unknown identity is the string ``"unknown"`` and never a missing
  key. A consumer that sees a number always sees one somebody measured.
* **Per-document schema versions.** ``recommend``, ``export``, ``doctor`` and
  ``error`` each carry their own, because bumping one shared number would tell
  every consumer their contract had changed when only one document moved.

The schemas are generated from the definitions here rather than written beside
them, and ``schemas/*.schema.json`` is committed for integrators;
``tests/test_jsonout.py`` regenerates and compares, so a schema cannot drift
from the code it describes without failing a test.

Dependency-free by construction, validator included: this project ships one
runtime dependency and a JSON-schema library is not going to be the second.
:func:`validate` understands exactly the keywords these schemas use, and
:data:`SUPPORTED_KEYWORDS` is asserted against them, so the checker cannot go
quiet by ignoring one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from export.models import ExportFormat, PlaylistTrack
from recommender.coverage import IdentityCoverage
from recommender.why import ProvenanceItem, WhyThisArtist

from pipeline.doctor import (
    ENV_KEYS,
    NETWORK_EGRESS_MODULES,
    UPSTREAM_APIS,
    DoctorReport,
)
from pipeline.models import Gender, IdentityBasis
from pipeline.runs import (
    CAUSE_EXPLORE,
    CAUSE_FEEDBACK,
    CAUSE_FILTER,
    CAUSE_LENS,
    CAUSE_PROFILE,
    CAUSE_UNDETERMINED,
    CAUSE_UPSTREAM,
    COMPARABLE_FIELDS,
    RunDiff,
)

#: Per-document, on purpose. See the module docstring.
SCHEMA_VERSIONS: dict[str, int] = {
    "recommend": 1,
    "export": 1,
    "doctor": 1,
    "error": 1,
    "corrections": 1,
    "pending_corrections": 1,
    "diff": 1,
    "report": 1,
    "feedback": 1,
    "refresh": 1,
    "eval": 1,
    "runs": 1,
}

_SCHEMA_BASE = "https://github.com/ChelseaKR/lavender-rotation/blob/main/schemas"

#: The error kinds a document may report. A caller switching on these must not
#: have to parse a human sentence.
ERROR_KINDS: tuple[str, ...] = ("invalid_filter", "live_mode", "not_found", "invalid_input")


def emit(document: Mapping[str, Any]) -> str:
    """The bytes one invocation prints. Sorted keys, two-space indent, one newline.

    Sorted rather than insertion-ordered so two runs of the same command produce
    byte-identical output regardless of how the document was assembled.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# --- documents ---------------------------------------------------------------


def _provenance(items: Iterable[ProvenanceItem]) -> list[dict[str, Any]]:
    return [
        {
            "source_kind": item.source_kind,
            "asserted_value": item.asserted_value,
            "citation": item.citation,
            "retrieved_at": item.retrieved_at,
            "is_local_correction": bool(getattr(item, "is_local_correction", False)),
        }
        for item in items
    ]


def _identity(why: WhyThisArtist, sourced_gender: Gender) -> dict[str, Any]:
    """The identity block, shaped so the no-inference guarantee is structural.

    ``sourced_gender`` is the enum value, so an unknown identity is the string
    ``"unknown"`` rather than a missing key: absence is first class here and a
    consumer must not be able to mistake it for a field somebody forgot.
    """
    return {
        "basis": why.identity_basis.value,
        "sourced_gender": sourced_gender.value,
        "statement": why.identity_statement,
        # Not a field the pipeline computes and might one day set: the schema
        # pins it to false, so a build that started guessing would fail
        # validation rather than publish a guess.
        "inferred": False,
        "conflict_note": why.conflict_note,
        "provenance": _provenance(why.provenance),
        "queer_provenance": _provenance(why.queer_provenance),
    }


def _coverage(coverage: IdentityCoverage) -> dict[str, Any]:
    """Counts, plus two shares that are ``null`` over an empty run.

    ``0.0`` would read as a measured zero. There is no share to report when
    nothing was picked, and the model already draws that distinction.
    """
    measured = coverage.fractions_measured
    return {
        "total": coverage.total,
        "sourced": coverage.sourced,
        "self_identified": coverage.self_identified,
        "band_composition": coverage.band_composition,
        "unknown": coverage.unknown,
        "women": coverage.women,
        "nonbinary": coverage.nonbinary,
        "men": coverage.men,
        "other": coverage.other,
        "sourced_share": (coverage.sourced / coverage.total) if measured else None,
        "unknown_share": (coverage.unknown / coverage.total) if measured else None,
        "summary": coverage.summary_line(),
    }


def _picks(recommendations: Sequence[Any]) -> list[dict[str, Any]]:
    """One entry per recommendation, identity block included.

    Shared by ``recommend`` and ``report`` so the two cannot describe the same
    ranking differently. ``report`` renders an HTML page from exactly these
    recommendations, and a second, drifting serialisation of them is how the
    dashboard came to show one ranking while measuring another (see
    ``app/observability.py``).
    """
    from recommender.why import why_this_artist

    picks: list[dict[str, Any]] = []
    for rec in recommendations:
        why = why_this_artist(rec)
        picks.append(
            {
                "rank": rec.rank,
                "artist_id": rec.artist.artist_id,
                "artist_name": rec.artist.name,
                "score": rec.score,
                "why": {
                    "headline": why.headline,
                    "reasons": list(why.reasons),
                    "rank_shift": why.rank_shift,
                },
                "identity": _identity(why, rec.artist.identity.gender),
            }
        )
    return picks


def recommend_document(
    *,
    recommendations: Sequence[Any],
    coverage: IdentityCoverage,
    listener: str,
    lens_name: str,
    lens_strength: float,
    explore: float,
    hide_sourced_men: bool,
    k: int,
    content_filter_description: str,
) -> dict[str, Any]:
    """One ``recommend`` run.

    Deliberately carries no timestamp and no run id. Both would change between
    two runs of the same query, and a byte-identical document is the only way a
    reviewer can tell a ranking change from a bookkeeping one. The run manifest
    already records when a run happened; ``lavender runs`` is where that lives.
    """
    return {
        "schema_version": SCHEMA_VERSIONS["recommend"],
        "command": "recommend",
        "query": {
            "listener": listener,
            "k": k,
            "lens_name": lens_name,
            "lens_strength": lens_strength,
            "explore": explore,
            "hide_sourced_men": hide_sourced_men,
            "content_filter": content_filter_description,
        },
        "identity_coverage": _coverage(coverage),
        "recommendations": _picks(recommendations),
    }


def report_document(
    *,
    recommendations: Sequence[Any],
    coverage: IdentityCoverage,
    exposure_panel: Mapping[str, Any],
    listener: str,
    lens_name: str,
    lens_strength: float,
    hide_sourced_men: bool,
    k: int,
    content_filter_description: str,
    written_to: str,
    bytes_written: int,
) -> dict[str, Any]:
    """One ``report`` run: the data behind the page, and where the page went.

    The HTML is not embedded. It is a rendering of the picks below, and a
    document carrying both would let the two disagree -- which is the drift this
    command's own ``observability_inputs`` seam exists to prevent. What the
    caller cannot get any other way is the *input*: the same picks, the same
    coverage, and the fairness panel the page prints, in a shape a script can
    read without parsing markup.

    ``report`` exposes no ``--explore``, so the query block reports the 0.0 the
    run actually used rather than omitting a knob and leaving a reader to guess
    which default applied.
    """
    return {
        "schema_version": SCHEMA_VERSIONS["report"],
        "command": "report",
        "query": {
            "listener": listener,
            "k": k,
            "lens_name": lens_name,
            "lens_strength": lens_strength,
            "explore": 0.0,
            "hide_sourced_men": hide_sourced_men,
            "content_filter": content_filter_description,
        },
        "written_to": written_to,
        "bytes_written": bytes_written,
        "identity_coverage": _coverage(coverage),
        "recommendations": _picks(recommendations),
        "exposure_panel": dict(exposure_panel),
    }


def feedback_document(
    *, listener: str, artist_id: str, vote: str, recorded_at: str, database: str
) -> dict[str, Any]:
    """One thumbs vote, recorded.

    ``vote`` is the word the caller typed rather than the ``+1``/``-1`` the cache
    stores, because the stored integer is an implementation detail and a script
    switching on it would be pinned to one.
    """
    if vote not in ("up", "down"):
        raise ValueError(f"unknown vote: {vote}")
    return {
        "schema_version": SCHEMA_VERSIONS["feedback"],
        "command": "feedback",
        "database": database,
        "listener": listener,
        "artist_id": artist_id,
        "vote": vote,
        "recorded_at": recorded_at,
    }


def _change(change: Any) -> dict[str, Any]:
    return {
        "artist_id": change.artist_id,
        "source_kind": change.source_kind,
        "old_value": change.old_value,
        "new_value": change.new_value,
        "retrieved_at": change.retrieved_at,
    }


def _reconciliation(outcome: Any) -> dict[str, Any]:
    """What reconciliation did, with the reason it may have done nothing.

    ``upstream_queried`` is carried rather than inferred from an empty
    ``reconciled`` list. "No filed correction had landed upstream" and "no
    upstream source was consulted, so none could have" are different findings
    and the counts are identical in both.
    """
    return {
        "upstream_queried": outcome.upstream_queried,
        "reconciled": [
            {
                "artist_id": row.artist_id,
                "source_kind": row.source_kind,
                "proposed_value": row.proposed_value,
            }
            for row in outcome.reconciled
        ],
        "superseded": [
            {
                "artist_id": row.artist_id,
                "source_kind": row.source_kind,
                "proposed_value": row.proposed_value,
                "superseded_by_value": row.superseded_by_value,
            }
            for row in outcome.superseded
        ],
        "still_open": len(outcome.superseded) + len(outcome.still_open),
    }


def refresh_document(
    *,
    mode: str,
    database: str,
    summary: str,
    expired_http_cache_rows: int,
    changes: Sequence[Any],
    reconciliation: Any,
    outcome: Any | None = None,
) -> dict[str, Any]:
    """One ``refresh`` run, in either of its two modes.

    The live branch re-asks upstream; the demo branch rewrites the fixture
    catalog and queries nothing. Both are reported through one document with a
    ``mode`` discriminator, and every field the demo branch cannot supply is
    ``null`` rather than zero -- a demo run reporting ``attempted: 0`` alongside
    ``upstream_answered: false`` would be indistinguishable from a live run that
    reached nothing, which is the single distinction this command's own
    :class:`~pipeline.ingest.RefreshOutcome` was written to keep.

    ``upstream_answered`` is ``outcome.upstream_answered``: positive proof that
    a citation came back over the wire, not ``failed == ()``. And ``protected``
    carries **every** artist id, not the console's preview of the first few,
    with ``count`` beside it so a truncating consumer cannot mistake the list it
    holds for the whole of it.
    """
    if mode not in ("live", "demo"):
        raise ValueError(f"unknown refresh mode: {mode}")
    upstream: dict[str, Any]
    if outcome is None:
        upstream = {
            # Not zeroes. Nothing was asked, so there is no count of what
            # answered; a zero here is a measurement of an upstream this run
            # never contacted.
            "queried": False,
            "answered": None,
            "attempted": None,
            "verified": None,
            "unverified": None,
            "failed": None,
            "protected": None,
        }
    else:
        upstream = {
            "queried": True,
            "answered": outcome.upstream_answered,
            "attempted": outcome.attempted,
            "verified": len(outcome.verified),
            "unverified": len(outcome.unverified),
            "failed": len(outcome.failed),
            "protected": {
                "count": len(outcome.protected),
                "artist_ids": list(outcome.protected),
            },
        }
    return {
        "schema_version": SCHEMA_VERSIONS["refresh"],
        "command": "refresh",
        "mode": mode,
        "database": database,
        "summary": summary,
        "expired_http_cache_rows": expired_http_cache_rows,
        "upstream": upstream,
        "changes": [_change(change) for change in changes],
        "reconciliation": _reconciliation(reconciliation),
    }


#: The named verdicts ``eval`` returns 0 or 1 on. Each is a separate claim, and
#: collapsing them into one boolean is what left the ``other``-retention half
#: unchecked for as long as it was (#68).
EVAL_VERDICTS: tuple[str, ...] = (
    "hybrid_beats_popularity",
    "multiworld_hybrid_beats_popularity",
    "unknown_retention_all_lenses",
    "other_retention_all_lenses",
    "no_score_reduced_any_artist",
)


def eval_document(
    *,
    k: int,
    verdicts: Mapping[str, bool],
    regressed_vs_baseline: bool | None,
    baseline_path: str,
    unmeasured_guarantees: Sequence[Mapping[str, Any]],
    written_to: str,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """One ``eval`` run: the verdict first, the report body beside it.

    ``eval`` already printed its report as JSON, and that is not the same thing
    as having a document. The report says what the metrics were; only the exit
    code said whether the run *passed*, and the reasons it did not lived in
    ``FAIL:`` sentences on stderr. Both are here, named.

    Two fields carry the honesty this command already practises in prose:

    * ``regressed_vs_baseline`` is ``null`` when no baseline file was present.
      ``false`` would report that nothing regressed, which is a claim about a
      comparison nobody ran.
    * ``unmeasured_guarantees`` lists every retention guarantee that passed
      because its segment was empty. Those are printed as ``UNMEASURED:``
      warnings today and are invisible to anything reading the exit code -- a
      guarantee that had nothing to violate is not a guarantee that held.
    """
    missing = set(EVAL_VERDICTS) - set(verdicts)
    if missing:
        raise ValueError(f"eval verdicts missing: {sorted(missing)}")
    passed = all(verdicts[name] for name in EVAL_VERDICTS) and not bool(regressed_vs_baseline)
    return {
        "schema_version": SCHEMA_VERSIONS["eval"],
        "command": "eval",
        "ok": True,
        "k": k,
        "passed": passed,
        "verdicts": {name: bool(verdicts[name]) for name in EVAL_VERDICTS},
        "regressed_vs_baseline": regressed_vs_baseline,
        "baseline_path": baseline_path,
        "baseline_present": regressed_vs_baseline is not None,
        "unmeasured_guarantees": [dict(item) for item in unmeasured_guarantees],
        "written_to": written_to,
        "report": dict(report),
    }


def runs_document(
    *,
    action: str,
    runs: Sequence[Mapping[str, Any]] | None = None,
    unreadable: Sequence[Mapping[str, Any]] | None = None,
    manifest: Mapping[str, Any] | None = None,
    pruned: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One ``runs`` invocation. ``action`` says which of the three it was.

    ``unreadable`` is a first-class list beside ``runs``, not a silent omission
    and not an error. The text listing already names an unreadable manifest and
    keeps going, because one bad file must not hide every good one; a JSON
    listing that dropped those rows would publish a shorter history as a
    complete one.
    """
    if action not in ("list", "show", "prune"):
        raise ValueError(f"unknown runs action: {action}")
    return {
        "schema_version": SCHEMA_VERSIONS["runs"],
        "command": "runs",
        "action": action,
        "count": None if runs is None else len(runs),
        "runs": None if runs is None else [dict(row) for row in runs],
        "unreadable": None if unreadable is None else [dict(row) for row in unreadable],
        "manifest": None if manifest is None else dict(manifest),
        "pruned": None if pruned is None else dict(pruned),
    }


def export_document(
    *,
    export_format: ExportFormat,
    playlist_name: str,
    tracks: Sequence[PlaylistTrack],
    content: str,
    generated_at: str,
    written_to: str | None,
) -> dict[str, Any]:
    """One ``export`` run: the portable file, wrapped, plus metadata.

    ``content`` is verbatim whatever the chosen format rendered. Wrapping rather
    than re-serialising is the point: the envelope cannot carry a field the
    format does not already carry, so the export contract is unchanged by the
    existence of this document.
    """
    return {
        "schema_version": SCHEMA_VERSIONS["export"],
        "command": "export",
        "format": str(export_format),
        "playlist_name": playlist_name,
        "track_count": len(tracks),
        "generated_at": generated_at,
        "written_to": written_to,
        "content": content,
    }


def doctor_document(report: DoctorReport, *, upstream_checked: bool) -> dict[str, Any]:
    """One ``doctor`` run, plus what this tool is permitted to contact.

    ``upstream_checked`` is reported because a run without ``--check-upstream``
    has no upstream checks in it at all, and an empty list of upstream failures
    would otherwise read as "every API is reachable".
    """
    return {
        "schema_version": SCHEMA_VERSIONS["doctor"],
        "command": "doctor",
        "ok": report.ok,
        "upstream_checked": upstream_checked,
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
                "hard": check.hard,
            }
            for check in report.checks
        ],
        "egress_allowlist": {
            "modules": sorted(NETWORK_EGRESS_MODULES),
            "hosts": [url for _label, url in UPSTREAM_APIS],
            "note": (
                "The modules permitted to open a socket, and the hosts the "
                "opt-in upstream probe reaches. Nothing here is contacted "
                "unless a command that needs it is run; the default paths open "
                "no socket at all."
            ),
        },
        "environment_variables_read": list(ENV_KEYS),
    }


def corrections_document(
    *, corrections: Iterable[tuple[str, Any, str]], database: str
) -> dict[str, Any]:
    """The local corrections ledger, listed.

    Every row carries a citation by construction -- ``put_correction`` refuses
    an unsourced one -- so the ledger has the same sourced-or-nothing shape the
    recommendation document publishes, and the two can be read together: this is
    what a person asserted, that is what the ranking then did with it.

    ``count`` beside an empty list is deliberate, and is the one place in this
    module where an empty container is the honest answer. An empty ledger is a
    *measured* emptiness: the store was opened and held nothing. The ``null``s
    elsewhere in these documents mean something the command did not do, which is
    why the write path below reports ``corrections: null`` rather than ``[]``.
    """
    rows = [
        {
            "artist_id": artist_id,
            "source_kind": str(evidence.kind),
            "asserted_value": evidence.value,
            "citation": evidence.citation,
            "retrieved_at": evidence.retrieved_at,
            "entered_at": entered_at,
        }
        for artist_id, evidence, entered_at in corrections
    ]
    return {
        "schema_version": SCHEMA_VERSIONS["corrections"],
        "command": "corrections",
        "action": "list",
        "database": database,
        "count": len(rows),
        "corrections": rows,
        "recorded": None,
    }


def correction_recorded_document(
    *, artist_id: str, citation: str, retrieved_at: str, entered_at: str, database: str
) -> dict[str, Any]:
    """One correction written. The ledger was not listed, and says so.

    The asserted value is **not** echoed back, matching the console path and for
    the same reason: an identity value is the one thing this project promises
    never leaves the machine it was typed on, and JSON is the output most likely
    to be piped into a log. The caller supplied that value a moment ago; what it
    does not already know is that the row landed, and under which citation.
    """
    return {
        "schema_version": SCHEMA_VERSIONS["corrections"],
        "command": "corrections",
        "action": "record",
        "database": database,
        # Not 0 and not []: this run did not read the ledger. A zero here would
        # tell a script the ledger is empty immediately after it was added to.
        "count": None,
        "corrections": None,
        "recorded": {
            "artist_id": artist_id,
            "citation": citation,
            "retrieved_at": retrieved_at,
            "entered_at": entered_at,
        },
    }


def _pending_row(row: Any) -> dict[str, Any]:
    """One filed request.

    ``edit_url`` and the two superseding fields stay ``null`` rather than
    becoming empty strings. "No edit route is known for this source kind" and
    "the edit route is the empty string" are different statements, and only the
    first is one this project can make.
    """
    return {
        "artist_id": row.artist_id,
        "source_kind": row.source_kind,
        "citation": row.citation,
        "current_value": row.current_value,
        "proposed_value": row.proposed_value,
        "note": row.note,
        "filed_at": row.filed_at,
        "edit_url": row.edit_url,
        "is_superseded": row.is_superseded,
        "superseded_by_value": row.superseded_by_value,
        "superseded_at": row.superseded_at,
    }


def pending_corrections_document(*, rows: Iterable[Any], path: str) -> dict[str, Any]:
    """Filed-but-not-yet-reconciled upstream edit requests, listed."""
    listed = [_pending_row(row) for row in rows]
    return {
        "schema_version": SCHEMA_VERSIONS["pending_corrections"],
        "command": "pending-corrections",
        "action": "list",
        "path": path,
        "count": len(listed),
        "pending_corrections": listed,
        "filed": None,
    }


def pending_correction_filed_document(*, row: Any, path: str) -> dict[str, Any]:
    """One request filed. Filing edits nothing upstream; see the schema."""
    return {
        "schema_version": SCHEMA_VERSIONS["pending_corrections"],
        "command": "pending-corrections",
        "action": "file",
        "path": path,
        "count": None,
        "pending_corrections": None,
        "filed": _pending_row(row),
    }


def diff_document(result: RunDiff) -> dict[str, Any]:
    """One ``diff`` between two recorded runs, as a versioned document.

    The body is :meth:`pipeline.runs.RunDiff.to_dict` unchanged -- flat, at the
    top level, exactly where a caller who was already reading ``shifts`` and
    ``unchanged`` will still find them. This adds the two things that make it a
    contract rather than a print: a ``schema_version`` a consumer can pin, and a
    ``command`` key, so a document read off a pipe identifies itself.

    ``ok`` is always ``True`` here, and that is the point of it. A refused diff
    is an ``error`` document carrying ``ok: false``, so a caller can branch on
    one key across both outcomes rather than on whether ``json.loads`` threw.
    """
    return {
        "schema_version": SCHEMA_VERSIONS["diff"],
        "command": "diff",
        "ok": True,
        **result.to_dict(),
    }


def error_document(*, command: str, kind: str, message: str) -> dict[str, Any]:
    """A refusal, in the same shape as a result.

    A caller that asked for JSON gets JSON when the run fails too. A traceback on
    stderr and an empty stdout is the shape that makes a script treat a refusal
    as an empty result.
    """
    if kind not in ERROR_KINDS:
        raise ValueError(f"unknown error kind: {kind}")
    return {
        "schema_version": SCHEMA_VERSIONS["error"],
        "command": command,
        "ok": False,
        "error": {"kind": kind, "message": message},
    }


# --- the published schemas, generated from the definitions above --------------


def _enum(values: Iterable[str], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values), "description": description}


def _provenance_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "required": [
                "source_kind",
                "asserted_value",
                "citation",
                "retrieved_at",
                "is_local_correction",
            ],
            "additionalProperties": False,
            "properties": {
                "source_kind": {"type": "string"},
                "asserted_value": {
                    "type": "string",
                    "description": "The raw thing the source said, so a reader can audit it.",
                },
                "citation": {"type": "string", "minLength": 1},
                "retrieved_at": {"type": "string"},
                "is_local_correction": {"type": "boolean"},
            },
        },
    }


def _query_schema() -> dict[str, Any]:
    """The knobs a ranking run was asked for. Shared by ``recommend`` and ``report``."""
    return {
        "type": "object",
        "required": [
            "listener",
            "k",
            "lens_name",
            "lens_strength",
            "explore",
            "hide_sourced_men",
            "content_filter",
        ],
        "additionalProperties": False,
        "properties": {
            "listener": {"type": "string"},
            "k": {"type": "integer", "minimum": 1},
            "lens_name": {"type": "string"},
            "lens_strength": {"type": "number", "minimum": 0, "maximum": 1},
            "explore": {"type": "number", "minimum": 0, "maximum": 1},
            "hide_sourced_men": {"type": "boolean"},
            "content_filter": {"type": "string"},
        },
    }


def _coverage_schema() -> dict[str, Any]:
    """Identity coverage over one ranking. Shared by ``recommend`` and ``report``.

    One definition rather than a copy per document: a second, drifting copy of a
    shape whose whole job is to say a share may be ``null`` is exactly how the
    ``0.0``-for-``null`` defect gets back in through the other surface.
    """
    return {
        "type": "object",
        "required": [
            "total",
            "sourced",
            "self_identified",
            "band_composition",
            "unknown",
            "women",
            "nonbinary",
            "men",
            "other",
            "sourced_share",
            "unknown_share",
            "summary",
        ],
        "additionalProperties": False,
        "properties": {
            "total": {"type": "integer", "minimum": 0},
            "sourced": {"type": "integer", "minimum": 0},
            "self_identified": {"type": "integer", "minimum": 0},
            "band_composition": {"type": "integer", "minimum": 0},
            "unknown": {"type": "integer", "minimum": 0},
            "women": {"type": "integer", "minimum": 0},
            "nonbinary": {"type": "integer", "minimum": 0},
            "men": {"type": "integer", "minimum": 0},
            "other": {"type": "integer", "minimum": 0},
            "sourced_share": {
                "type": ["number", "null"],
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "null over an empty run. A zero would read as a "
                    "measured share, and there is none to measure."
                ),
            },
            "unknown_share": {
                "type": ["number", "null"],
                "minimum": 0,
                "maximum": 1,
            },
            "summary": {"type": "string"},
        },
    }


def _picks_schema() -> dict[str, Any]:
    """One ranking's picks. Shared by ``recommend`` and ``report``.

    This is where the no-inference guarantee is structural, so it has exactly
    one definition: ``inferred`` is pinned to ``false`` and ``sourced_gender``
    is drawn from the resolver's own enum, in which ``unknown`` is a value. A
    per-document copy of this shape is a second place for a nullable gender to
    appear, which is the failure the guarantee is written against.
    """
    return {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["rank", "artist_id", "artist_name", "score", "why", "identity"],
            "additionalProperties": False,
            "properties": {
                "rank": {"type": "integer", "minimum": 1},
                "artist_id": {"type": "string", "minLength": 1},
                "artist_name": {"type": "string", "minLength": 1},
                "score": {"type": "number"},
                "why": {
                    "type": "object",
                    "required": ["headline", "reasons", "rank_shift"],
                    "additionalProperties": False,
                    "properties": {
                        "headline": {"type": "string", "minLength": 1},
                        "reasons": {"type": "array", "items": {"type": "string"}},
                        "rank_shift": {"type": "string"},
                    },
                },
                "identity": {
                    "type": "object",
                    "required": [
                        "basis",
                        "sourced_gender",
                        "statement",
                        "inferred",
                        "conflict_note",
                        "provenance",
                        "queer_provenance",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "basis": _enum(
                            [basis.value for basis in IdentityBasis],
                            "How the identity was established. Never 'inferred'.",
                        ),
                        "sourced_gender": _enum(
                            [gender.value for gender in Gender],
                            (
                                "'unknown' is a value, not a gap: an artist "
                                "with no sourced identity is first class here "
                                "and is never down-ranked for it."
                            ),
                        ),
                        "statement": {"type": "string", "minLength": 1},
                        "inferred": {
                            "type": "boolean",
                            "const": False,
                            "description": (
                                "Pinned. Identity in this system is sourced or "
                                "unknown; there is no third state."
                            ),
                        },
                        "conflict_note": {"type": "string"},
                        "provenance": _provenance_schema(),
                        "queer_provenance": _provenance_schema(),
                    },
                },
            },
        },
    }


def recommend_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/recommend.schema.json",
        "title": "lavender recommend --json",
        "description": (
            "One recommendation run. There is no slot for an inferred identity: "
            "'inferred' is pinned to false and every non-unknown sourced gender "
            "must carry provenance, so the no-inference guarantee is a property "
            "of this document's shape rather than of its prose."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "query",
            "identity_coverage",
            "recommendations",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["recommend"]},
            "command": {"type": "string", "const": "recommend"},
            "query": _query_schema(),
            "identity_coverage": _coverage_schema(),
            "recommendations": _picks_schema(),
        },
    }


def report_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/report.schema.json",
        "title": "lavender report --json",
        "description": (
            "The data behind the HTML discovery report, plus where the page was "
            "written. The picks and the coverage block are the same shapes "
            "'recommend' publishes, from the same builder, so a script cannot be "
            "handed two different descriptions of one ranking. The HTML itself is "
            "not embedded: it is a rendering of these picks, and carrying both "
            "would let the two disagree."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "query",
            "written_to",
            "bytes_written",
            "identity_coverage",
            "recommendations",
            "exposure_panel",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["report"]},
            "command": {"type": "string", "const": "report"},
            "query": _query_schema(),
            "written_to": {"type": "string", "minLength": 1},
            "bytes_written": {"type": "integer", "minimum": 0},
            "identity_coverage": _coverage_schema(),
            "recommendations": _picks_schema(),
            "exposure_panel": {
                "type": "object",
                "description": (
                    "The fairness panel the page prints, verbatim from "
                    "app.observability. Its retention and rank-shift maps are "
                    "keyed by lens strength and by segment, which come from the "
                    "run rather than from this schema, so 'object' is the honest "
                    "bound here. tests/test_jsonout.py carries the constraint "
                    "this cannot: every share and every retention value is a "
                    "number or null, never a zero standing in for an empty top-k."
                ),
            },
        },
    }


def feedback_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/feedback.schema.json",
        "title": "lavender feedback --json",
        "description": (
            "One thumbs vote, recorded. 'vote' is the word rather than the "
            "signed integer the cache stores, so a consumer is not pinned to an "
            "internal representation."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "database",
            "listener",
            "artist_id",
            "vote",
            "recorded_at",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["feedback"]},
            "command": {"type": "string", "const": "feedback"},
            "database": {"type": "string", "minLength": 1},
            "listener": {"type": "string", "minLength": 1},
            "artist_id": {"type": "string", "minLength": 1},
            "vote": _enum(["up", "down"], "Which way the listener voted."),
            "recorded_at": {"type": "string", "minLength": 1},
        },
    }


def _identity_change_schema(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "required": ["artist_id", "source_kind", "old_value", "new_value", "retrieved_at"],
            "additionalProperties": False,
            "properties": {
                "artist_id": {"type": "string", "minLength": 1},
                "source_kind": {"type": "string"},
                "old_value": {"type": "string"},
                "new_value": {"type": "string"},
                "retrieved_at": {"type": "string"},
            },
        },
    }


def refresh_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/refresh.schema.json",
        "title": "lavender refresh --json",
        "description": (
            "One refresh run. 'mode' says whether upstream was actually asked. "
            "Every count under 'upstream' is null in demo mode rather than zero, "
            "because a zero is a measurement of an upstream this run never "
            "contacted -- and 'upstream.answered' is positive proof that a "
            "citation came back over the wire, not the absence of a failure."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "mode",
            "database",
            "summary",
            "expired_http_cache_rows",
            "upstream",
            "changes",
            "reconciliation",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["refresh"]},
            "command": {"type": "string", "const": "refresh"},
            "mode": _enum(
                ["live", "demo"],
                (
                    "'demo' rewrites the fixture catalog and queries no upstream "
                    "identity source at all."
                ),
            ),
            "database": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "expired_http_cache_rows": {"type": "integer", "minimum": 0},
            "upstream": {
                "type": "object",
                "required": [
                    "queried",
                    "answered",
                    "attempted",
                    "verified",
                    "unverified",
                    "failed",
                    "protected",
                ],
                "additionalProperties": False,
                "properties": {
                    "queried": {"type": "boolean"},
                    "answered": {
                        "type": ["boolean", "null"],
                        "description": (
                            "True only when at least one citation came back over "
                            "the wire. null when nothing was asked: 'we did not "
                            "look' is not 'nothing had changed'."
                        ),
                    },
                    "attempted": {"type": ["integer", "null"], "minimum": 0},
                    "verified": {"type": ["integer", "null"], "minimum": 0},
                    "unverified": {"type": ["integer", "null"], "minimum": 0},
                    "failed": {"type": ["integer", "null"], "minimum": 0},
                    "protected": {
                        "type": ["object", "null"],
                        "description": (
                            "Artists whose existing citation was kept because "
                            "upstream said nothing about it. The console prints a "
                            "preview; this list is complete, and 'count' is beside "
                            "it so a truncating consumer cannot mistake one for "
                            "the other."
                        ),
                        "required": ["count", "artist_ids"],
                        "additionalProperties": False,
                        "properties": {
                            "count": {"type": "integer", "minimum": 0},
                            "artist_ids": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
            "changes": _identity_change_schema(
                "Cited source values that moved on this run, one entry per source."
            ),
            "reconciliation": {
                "type": "object",
                "required": ["upstream_queried", "reconciled", "superseded", "still_open"],
                "additionalProperties": False,
                "properties": {
                    "upstream_queried": {
                        "type": "boolean",
                        "description": (
                            "False means no upstream edit could have landed, so "
                            "an empty 'reconciled' list says nothing about the "
                            "filed corrections."
                        ),
                    },
                    "reconciled": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["artist_id", "source_kind", "proposed_value"],
                            "additionalProperties": False,
                            "properties": {
                                "artist_id": {"type": "string", "minLength": 1},
                                "source_kind": {"type": "string"},
                                "proposed_value": {"type": "string"},
                            },
                        },
                    },
                    "superseded": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "artist_id",
                                "source_kind",
                                "proposed_value",
                                "superseded_by_value",
                            ],
                            "additionalProperties": False,
                            "properties": {
                                "artist_id": {"type": "string", "minLength": 1},
                                "source_kind": {"type": "string"},
                                "proposed_value": {"type": "string"},
                                "superseded_by_value": {"type": ["string", "null"]},
                            },
                        },
                    },
                    "still_open": {"type": "integer", "minimum": 0},
                },
            },
        },
    }


def eval_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/eval.schema.json",
        "title": "lavender eval --json",
        "description": (
            "One offline eval run: the verdict, then the report it was read "
            "from. Each gate is named separately rather than collapsed into "
            "'passed', and 'unmeasured_guarantees' lists every retention "
            "guarantee that held only because its segment was empty -- a "
            "guarantee with nothing to violate is not a guarantee that held."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "ok",
            "k",
            "passed",
            "verdicts",
            "regressed_vs_baseline",
            "baseline_path",
            "baseline_present",
            "unmeasured_guarantees",
            "written_to",
            "report",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["eval"]},
            "command": {"type": "string", "const": "eval"},
            "ok": {
                "type": "boolean",
                "const": True,
                "description": (
                    "The command ran. A failing gate is a result, not a refusal: "
                    "read 'passed'. Only a refused run emits the error document."
                ),
            },
            "k": {"type": "integer", "minimum": 1},
            "passed": {"type": "boolean"},
            "verdicts": {
                "type": "object",
                "required": list(EVAL_VERDICTS),
                "additionalProperties": False,
                "properties": {name: {"type": "boolean"} for name in EVAL_VERDICTS},
            },
            "regressed_vs_baseline": {
                "type": ["boolean", "null"],
                "description": (
                    "null when no baseline file was present. false would report "
                    "that nothing regressed, which is a claim about a comparison "
                    "nobody ran."
                ),
            },
            "baseline_path": {"type": "string", "minLength": 1},
            "baseline_present": {"type": "boolean"},
            "unmeasured_guarantees": {
                "type": "array",
                "description": (
                    "A retention guarantee whose segment never appeared in pure "
                    "taste's top-k passed without testing anything. Printed as an "
                    "UNMEASURED warning on stderr today, and invisible to anything "
                    "reading the exit code."
                ),
                "items": {
                    "type": "object",
                    "required": ["guarantee", "base_count", "note"],
                    "additionalProperties": False,
                    "properties": {
                        "guarantee": {"type": "string", "minLength": 1},
                        "base_count": {"type": "integer", "minimum": 0},
                        "note": {"type": "string", "minLength": 1},
                    },
                },
            },
            "written_to": {"type": "string", "minLength": 1},
            "report": {
                "type": "object",
                "description": (
                    "The eval report, verbatim -- the same object the command "
                    "already wrote to --out. Its inner keys come from the metric "
                    "set and the fixture worlds rather than from this schema, so "
                    "'object' is the honest bound; the envelope above is the part "
                    "this document contracts."
                ),
            },
        },
    }


def runs_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/runs.schema.json",
        "title": "lavender runs --json",
        "description": (
            "One 'runs' invocation. 'action' says which of the three it was, and "
            "every field the other two do not fill is null rather than empty. "
            "'unreadable' is a first-class list beside 'runs': a listing that "
            "silently dropped a manifest it could not parse would publish a "
            "shorter history as a complete one."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "action",
            "count",
            "runs",
            "unreadable",
            "manifest",
            "pruned",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["runs"]},
            "command": {"type": "string", "const": "runs"},
            "action": _enum(["list", "show", "prune"], "Which of the three verbs ran."),
            "count": {
                "type": ["integer", "null"],
                "minimum": 0,
                "description": "Readable manifests listed; null when this run did not list.",
            },
            "runs": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "required": [
                        "run_id",
                        "surface",
                        "created_at",
                        "lens_name",
                        "lens_strength",
                        "k",
                        "listener_digest",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "run_id": {"type": "string", "minLength": 1},
                        "surface": {"type": "string", "minLength": 1},
                        "created_at": {"type": "string", "minLength": 1},
                        "lens_name": {"type": "string"},
                        "lens_strength": {"type": "number"},
                        "k": {"type": "integer", "minimum": 1},
                        "listener_digest": {"type": "string"},
                    },
                },
            },
            "unreadable": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "required": ["name", "error"],
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "error": {"type": "string", "minLength": 1},
                    },
                },
            },
            "manifest": {
                "type": ["object", "null"],
                "description": (
                    "One run manifest, verbatim from pipeline.runs, which carries "
                    "its own schema_version. null unless the action was 'show'."
                ),
            },
            "pruned": {
                "type": ["object", "null"],
                "required": ["removed", "kept"],
                "additionalProperties": False,
                "properties": {
                    "removed": {"type": "integer", "minimum": 0},
                    "kept": {"type": "integer", "minimum": 0},
                },
            },
        },
    }


def export_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/export.schema.json",
        "title": "lavender export --json",
        "description": (
            "One export run. 'content' is verbatim what the chosen portable "
            "format rendered, so this envelope cannot carry a field that format "
            "does not already carry."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "format",
            "playlist_name",
            "track_count",
            "generated_at",
            "written_to",
            "content",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["export"]},
            "command": {"type": "string", "const": "export"},
            "format": _enum([str(item) for item in ExportFormat], "The portable format rendered."),
            "playlist_name": {"type": "string", "minLength": 1},
            "track_count": {"type": "integer", "minimum": 0},
            "generated_at": {"type": "string", "minLength": 1},
            "written_to": {
                "type": ["string", "null"],
                "description": "null when the export went to stdout.",
            },
            "content": {"type": "string"},
        },
    }


def doctor_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/doctor.schema.json",
        "title": "lavender doctor --json",
        "description": (
            "One diagnostics run, plus the egress allowlist. A reader can see "
            "what this tool is permitted to contact without reading its tests."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "ok",
            "upstream_checked",
            "checks",
            "egress_allowlist",
            "environment_variables_read",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["doctor"]},
            "command": {"type": "string", "const": "doctor"},
            "ok": {"type": "boolean"},
            "upstream_checked": {
                "type": "boolean",
                "description": (
                    "False unless --check-upstream was given. Without it the run "
                    "carries no upstream checks at all, and their absence is not "
                    "a report that every API was reachable."
                ),
            },
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "passed", "detail", "hard"],
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "passed": {"type": "boolean"},
                        "detail": {"type": "string"},
                        "hard": {
                            "type": "boolean",
                            "description": (
                                "False for informational checks, which never fail a run."
                            ),
                        },
                    },
                },
            },
            "egress_allowlist": {
                "type": "object",
                "required": ["modules", "hosts", "note"],
                "additionalProperties": False,
                "properties": {
                    "modules": {"type": "array", "items": {"type": "string"}},
                    "hosts": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
            "environment_variables_read": {"type": "array", "items": {"type": "string"}},
        },
    }


def corrections_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/corrections.schema.json",
        "title": "lavender corrections --json",
        "description": (
            "The local corrections ledger. Every row carries a citation by "
            "construction, so this is the sourced-or-nothing shape the "
            "recommendation document publishes, one layer earlier. 'action' "
            "says which of the two things this run did: a listing fills "
            "'corrections' and 'count', a write fills 'recorded' and leaves "
            "both of those null. Null rather than 0 and [], because a zero "
            "immediately after a write would tell a script the ledger is empty. "
            "A write never echoes the asserted value back: an identity value is "
            "the one thing this project promises never leaves the machine it "
            "was typed on, and JSON is the output most likely to reach a log."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "action",
            "database",
            "count",
            "corrections",
            "recorded",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["corrections"]},
            "command": {"type": "string", "const": "corrections"},
            "action": _enum(("list", "record"), "Which of the two things this run did."),
            "database": {"type": "string", "minLength": 1},
            "count": {
                "type": ["integer", "null"],
                "minimum": 0,
                "description": (
                    "How many rows the ledger held, or null when this run did "
                    "not read it. Zero is a measured emptiness; null is not."
                ),
            },
            "corrections": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "required": [
                        "artist_id",
                        "source_kind",
                        "asserted_value",
                        "citation",
                        "retrieved_at",
                        "entered_at",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "artist_id": {"type": "string", "minLength": 1},
                        "source_kind": {"type": "string", "minLength": 1},
                        "asserted_value": {
                            "type": "string",
                            "description": (
                                "The raw thing the person asserted, so it can be audited."
                            ),
                        },
                        "citation": {"type": "string", "minLength": 1},
                        "retrieved_at": {"type": "string", "minLength": 1},
                        "entered_at": {"type": "string", "minLength": 1},
                    },
                },
            },
            "recorded": {
                "type": ["object", "null"],
                "required": ["artist_id", "citation", "retrieved_at", "entered_at"],
                "additionalProperties": False,
                "properties": {
                    "artist_id": {"type": "string", "minLength": 1},
                    "citation": {"type": "string", "minLength": 1},
                    "retrieved_at": {"type": "string", "minLength": 1},
                    "entered_at": {"type": "string", "minLength": 1},
                },
            },
        },
    }


def _pending_row_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "artist_id",
            "source_kind",
            "citation",
            "current_value",
            "proposed_value",
            "note",
            "filed_at",
            "edit_url",
            "is_superseded",
            "superseded_by_value",
            "superseded_at",
        ],
        "additionalProperties": False,
        "properties": {
            "artist_id": {"type": "string", "minLength": 1},
            "source_kind": {"type": "string", "minLength": 1},
            "citation": {"type": "string", "minLength": 1},
            "current_value": {"type": "string"},
            "proposed_value": {"type": "string", "minLength": 1},
            "note": {"type": "string"},
            "filed_at": {"type": "string", "minLength": 1},
            "edit_url": {
                "type": ["string", "null"],
                "description": (
                    "The upstream edit link that was offered, or null when this "
                    "project knows no edit route for that source kind. Never an "
                    "empty string: 'no route known' and 'the route is empty' are "
                    "different statements."
                ),
            },
            "is_superseded": {"type": "boolean"},
            "superseded_by_value": {
                "type": ["string", "null"],
                "description": (
                    "What a later refresh observed the source asserting instead, "
                    "or null if none has. The row stays on file either way."
                ),
            },
            "superseded_at": {"type": ["string", "null"]},
        },
    }


def pending_corrections_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/pending-corrections.schema.json",
        "title": "lavender pending-corrections --json",
        "description": (
            "Filed-but-not-yet-reconciled upstream edit requests. Filing records "
            "what a person believes is right and why; it edits nothing upstream, "
            "because the edit happens in the upstream interface, by that person. "
            "'action' says which of the two things this run did, and the fields "
            "the other action would have filled are null rather than empty."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "action",
            "path",
            "count",
            "pending_corrections",
            "filed",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "integer",
                "const": SCHEMA_VERSIONS["pending_corrections"],
            },
            "command": {"type": "string", "const": "pending-corrections"},
            "action": _enum(("list", "file"), "Which of the two things this run did."),
            "path": {"type": "string", "minLength": 1},
            "count": {
                "type": ["integer", "null"],
                "minimum": 0,
                "description": "Null when this run filed rather than listed.",
            },
            "pending_corrections": {
                "type": ["array", "null"],
                "items": _pending_row_schema(),
            },
            "filed": _pending_row_schema() | {"type": ["object", "null"]},
        },
    }


def error_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/error.schema.json",
        "title": "lavender <command> --json, when the run is refused",
        "description": (
            "A refusal in the same shape as a result, so a script cannot read a "
            "refusal as an empty result."
        ),
        "type": "object",
        "required": ["schema_version", "command", "ok", "error"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["error"]},
            "command": {"type": "string", "minLength": 1},
            "ok": {"type": "boolean", "const": False},
            "error": {
                "type": "object",
                "required": ["kind", "message"],
                "additionalProperties": False,
                "properties": {
                    "kind": _enum(ERROR_KINDS, "Machine-switchable reason."),
                    "message": {"type": "string", "minLength": 1},
                },
            },
        },
    }


def _run_entry_schema(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "required": ["artist_id", "name", "rank", "base_rank", "lens_rank", "segment", "basis"],
            "additionalProperties": False,
            "properties": {
                "artist_id": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "rank": {"type": "integer", "minimum": 1},
                "base_rank": {"type": "integer", "minimum": 1},
                "lens_rank": {"type": "integer", "minimum": 1},
                "segment": {"type": "string"},
                "basis": {"type": "string"},
            },
        },
    }


def _delta_map_schema(description: str) -> dict[str, Any]:
    """A map whose keys come from the manifests being compared, not from here.

    The keys are whatever ``coverage``/``exposure`` the two runs recorded, so
    this schema cannot enumerate them and the validator in this module has no
    ``additionalProperties``-as-schema or ``patternProperties`` to constrain
    them with. Declaring only ``object`` is therefore the honest bound, and
    saying so here is better than a shape that looks stricter than it is.
    ``tests/test_jsonout.py`` carries the constraint this cannot: every value in
    both maps is a number or ``null``.
    """
    return {"type": "object", "description": description}


def diff_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/diff.schema.json",
        "title": "lavender diff <before> <after> --json",
        "description": (
            "What changed between two recorded runs, and what the record can "
            "support saying about why. A cause is named only where exactly one "
            "mechanism moved; otherwise the shift reports "
            f"{CAUSE_UNDETERMINED!r} and lists the candidates, which is a "
            "statement about the evidence rather than about the ranking."
        ),
        "type": "object",
        "required": [
            "schema_version",
            "command",
            "ok",
            "before_run_id",
            "after_run_id",
            "entered",
            "left",
            "shifts",
            "held",
            "coverage_delta",
            "exposure_delta",
            "changed_inputs",
            "mixed_fields",
            "unchanged",
        ],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSIONS["diff"]},
            "command": {"type": "string", "const": "diff"},
            "ok": {"type": "boolean", "const": True},
            "before_run_id": {"type": "string", "minLength": 1},
            "after_run_id": {"type": "string", "minLength": 1},
            "entered": _run_entry_schema("Artists in the later run's top-k and not the earlier."),
            "left": _run_entry_schema("Artists in the earlier run's top-k and not the later."),
            "shifts": {
                "type": "array",
                "description": "Artists in both runs at different displayed ranks.",
                "items": {
                    "type": "object",
                    "required": [
                        "artist_id",
                        "name",
                        "from_rank",
                        "to_rank",
                        "delta",
                        "cause",
                        "candidates",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "artist_id": {"type": "string", "minLength": 1},
                        "name": {"type": "string"},
                        "from_rank": {"type": "integer", "minimum": 1},
                        "to_rank": {"type": "integer", "minimum": 1},
                        "delta": {
                            "type": "integer",
                            "description": "to_rank - from_rank. Negative is a move up the list.",
                        },
                        "cause": _enum(
                            (
                                CAUSE_LENS,
                                CAUSE_EXPLORE,
                                CAUSE_UPSTREAM,
                                CAUSE_PROFILE,
                                CAUSE_FEEDBACK,
                                CAUSE_FILTER,
                                CAUSE_UNDETERMINED,
                            ),
                            "The mechanism the record singles out, or "
                            f"{CAUSE_UNDETERMINED!r} when more than one moved. "
                            "Never absent: a diff that cannot attribute a shift "
                            "says so rather than omitting the key.",
                        ),
                        "candidates": {
                            "type": "array",
                            "description": (
                                "Every mechanism that moved. One entry when the "
                                "cause is named; two or more when it is not."
                            ),
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "held": {
                "type": "integer",
                "minimum": 0,
                "description": "Artists present in both runs at the same rank.",
            },
            "coverage_delta": _delta_map_schema(
                "Change in each recorded coverage figure. A null is 'one side "
                "did not record this', never a zero change."
            ),
            "exposure_delta": _delta_map_schema(
                "Change in each recorded exposure share, with the same null rule."
            ),
            "changed_inputs": {
                "type": "array",
                "description": ("Upstream inputs whose digest differs between the two runs."),
                "items": {"type": "string", "minLength": 1},
            },
            "mixed_fields": {
                "type": "array",
                "description": (
                    "Fields that make these two runs answers to different "
                    "questions, compared anyway under --allow-mixed. Empty on a "
                    "like-for-like diff; a non-empty list is a caveat on every "
                    "other figure in this document."
                ),
                "items": _enum(COMPARABLE_FIELDS, "A field a diff refuses to cross by default."),
            },
            "unchanged": {
                "type": "boolean",
                "description": "True when nothing entered, left, or moved.",
            },
        },
    }


SCHEMAS: dict[str, Callable[[], dict[str, Any]]] = {
    "recommend.schema.json": recommend_schema,
    "report.schema.json": report_schema,
    "feedback.schema.json": feedback_schema,
    "refresh.schema.json": refresh_schema,
    "eval.schema.json": eval_schema,
    "runs.schema.json": runs_schema,
    "export.schema.json": export_schema,
    "doctor.schema.json": doctor_schema,
    "corrections.schema.json": corrections_schema,
    "pending-corrections.schema.json": pending_corrections_schema,
    "diff.schema.json": diff_schema,
    "error.schema.json": error_schema,
}


def render_schema(name: str) -> str:
    """The committed bytes for one schema: two-space JSON with a trailing newline."""
    if name not in SCHEMAS:
        raise KeyError(name)
    return json.dumps(SCHEMAS[name](), indent=2) + "\n"


# --- a validator that understands exactly these schemas -----------------------

#: Every keyword :func:`validate` implements. `tests/test_jsonout.py` asserts the
#: committed schemas use nothing outside this set, so the checker cannot pass a
#: document by quietly ignoring a constraint it does not know.
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minimum",
        "maximum",
        "minLength",
        "pattern",
    }
)

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "null": lambda value: value is None,
}


def _type_errors(value: Any, schema: Mapping[str, Any], path: str) -> list[str]:
    declared = schema.get("type")
    if declared is None:
        return []
    names = [declared] if isinstance(declared, str) else list(declared)
    unknown = [name for name in names if name not in _TYPE_CHECKS]
    if unknown:
        raise ValueError(f"{path}: schema declares unknown type(s) {unknown}")
    if any(_TYPE_CHECKS[name](value) for name in names):
        return []
    return [f"{path}: expected {'/'.join(names)}, got {type(value).__name__}"]


def _object_errors(value: Any, schema: Mapping[str, Any], path: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    errors: list[str] = []
    properties: Mapping[str, Any] = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            errors.append(f"{path}: missing required property {name!r}")
    if schema.get("additionalProperties") is False:
        for name in sorted(set(value) - set(properties)):
            errors.append(f"{path}: unexpected property {name!r}")
    for name, subschema in properties.items():
        if name in value:
            errors.extend(validate(value[name], subschema, path=f"{path}.{name}"))
    return errors


def _scalar_errors(value: Any, schema: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if "minimum" in schema and numeric and value < schema["minimum"]:
        errors.append(f"{path}: {value!r} is below minimum {schema['minimum']}")
    if "maximum" in schema and numeric and value > schema["maximum"]:
        errors.append(f"{path}: {value!r} is above maximum {schema['maximum']}")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    if (
        "pattern" in schema
        and isinstance(value, str)
        and not re.search(str(schema["pattern"]), value)
    ):
        errors.append(f"{path}: does not match {schema['pattern']!r}")
    return errors


def validate(document: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Every way ``document`` violates ``schema``, as readable paths.

    Returns a list rather than raising on the first problem: an integrator
    fixing a five-field document should not need five runs to see five errors.
    """
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        raise ValueError(f"{path}: schema uses unsupported keyword(s) {unknown}")
    errors = _type_errors(document, schema, path)
    if errors:
        return errors
    errors.extend(_scalar_errors(document, schema, path))
    errors.extend(_object_errors(document, schema, path))
    if isinstance(document, list) and "items" in schema:
        for index, item in enumerate(document):
            errors.extend(validate(item, schema["items"], path=f"{path}[{index}]"))
    return errors


def schema_keywords(schema: Any) -> set[str]:
    """Every keyword used anywhere in a schema, for the coverage assertion."""
    used: set[str] = set()
    if isinstance(schema, dict):
        used.update(schema)
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                for subschema in value.values():
                    used.update(schema_keywords(subschema))
            elif key in {"items"}:
                used.update(schema_keywords(value))
    return used


__all__ = [
    "ERROR_KINDS",
    "SCHEMAS",
    "SCHEMA_VERSIONS",
    "SUPPORTED_KEYWORDS",
    "doctor_document",
    "emit",
    "error_document",
    "export_document",
    "recommend_document",
    "render_schema",
    "schema_keywords",
    "validate",
]

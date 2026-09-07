"""Command-line entry point: ``lavender ingest|eval|recommend|export|refresh``.

Argparse glue over the library; omitted from coverage accounting, but the gate
behaviour of ``lavender eval`` (exit codes, regression/fairness blocks) and
``lavender refresh`` is exercised directly by ``tests/test_eval.py`` and
``tests/test_cache_lifecycle.py``.

Every product command still defaults to the offline demo world, and everything
that reaches upstream is opt-in and named: ``lavender ingest --user <you>`` is the
one command that fetches a real listening history and resolves identity against
MusicBrainz/Wikidata, and ``--user`` on the recommendation surfaces then reads
back what it cached. Without ``--user`` nothing here opens a socket.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from app.observability import observability_inputs
from app.render import render_cards_html
from export.models import ExportFormat
from export.tracklist import recommendations_to_tracks, render
from recommender.content_filters import (
    MAX_YEAR,
    MIN_YEAR,
    ContentFilter,
    FilterSpecError,
)
from recommender.coverage import identity_coverage
from recommender.eval import (
    check_regression,
    eval_real,
    evaluate,
    evaluate_worlds,
    fairness_report,
    to_report,
)
from recommender.feedback import Feedback
from recommender.hybrid import recommend
from recommender.lens import LENSES
from recommender.upstream import upstream_edit_url
from recommender.why import QUEER_SOURCES_HEADING, why_this_artist

from pipeline import corrections as pending_corrections
from pipeline.cache import DEFAULT_DB_PATH, DEFAULT_HTTP_TTL_DAYS, Cache
from pipeline.demo import DEMO_USER, demo_catalog, demo_profile, demo_scrobbles, demo_source
from pipeline.doctor import run_diagnostics
from pipeline.enrich import MusicBrainzEnricher
from pipeline.fileingest import FORMATS as FILE_FORMATS
from pipeline.fileingest import FileIngestError, ImportedHistory, dedupe, read_history
from pipeline.http import CachedHttpFetcher, build_user_agent
from pipeline.identity import (
    IdentityEvidence,
    accepted_gender_values,
    normalise_asserted_value,
)
from pipeline.ingest import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_PER_SEED,
    DEFAULT_SEEDS,
    catalog_from_cache,
    diff_identity_labels,
    discover_candidates,
    enrich_candidates,
    ingest,
    profile_from_cache,
    refresh_catalog,
)
from pipeline.jsonout import (
    correction_recorded_document,
    corrections_document,
    diff_document,
    doctor_document,
    emit,
    error_document,
    export_document,
    pending_correction_filed_document,
    pending_corrections_document,
    recommend_document,
)
from pipeline.lastfm import CachedLastfm, LastfmClient, ScrobbleSource
from pipeline.logconfig import LOG_FORMATS, configure_logging
from pipeline.models import (
    Artist,
    ListeningProfile,
    Recommendation,
    SourceKind,
    UnsourcedIdentityError,
)
from pipeline.runs import (
    DEFAULT_KEEP,
    RunManifestError,
    build_manifest,
    diff_runs,
    find_manifest,
    list_manifest_paths,
    prune_manifests,
    read_manifest,
    write_manifest,
)

_BASELINE_METRICS = frozenset({"precision_at_k", "recall_at_k", "map_at_k"})

#: Number of the listener's own most-played artists that a live ingest enriches.
#: See ``pipeline.ingest.ingest``'s ``enrich_top`` for why this is bounded.
DEFAULT_ENRICH_TOP = 50

#: Cached artists one `lavender refresh --user` run re-asks upstream about.
#: Bounded for the same reason ``DEFAULT_ENRICH_TOP`` is: MusicBrainz and
#: Wikidata are ~1 req/s and a real catalog runs to thousands of artists, so a
#: whole-catalog refresh is a sequence of resumable runs, not one long one.
DEFAULT_REFRESH_LIMIT = 100

#: How many protected artists `lavender refresh --user` names before summarising.
#: The count is always reported in full; only the listing is capped.
_PROTECTED_PREVIEW = 20


class LiveModeError(RuntimeError):
    """A live command was asked for without what live mode needs."""


def _require_api_key() -> str:
    key = os.environ.get("LAVENDER_LASTFM_API_KEY", "").strip()
    if not key:
        raise LiveModeError(
            "live mode needs a Last.fm API key. Set LAVENDER_LASTFM_API_KEY "
            "(get one at https://www.last.fm/api/account/create), or omit --user "
            "to use the offline demo world."
        )
    return key


def _live_enricher(cache: Cache, *, retrieved_at: str, ttl_days: int) -> MusicBrainzEnricher:
    """The live identity enricher, wired to the one allowlisted HTTP seam."""
    fetcher = CachedHttpFetcher(
        cache,
        user_agent=build_user_agent(os.environ.get("LAVENDER_CONTACT", "")),
        ttl_days=ttl_days,
    )
    return MusicBrainzEnricher(fetcher, retrieved_at=retrieved_at)


def _load_world(
    cache: Cache, args: argparse.Namespace
) -> tuple[ListeningProfile, dict[str, Artist], ScrobbleSource]:
    """The demo world, or the operator's own cached one when ``--user`` is given.

    Both branches are offline. ``lavender ingest`` is the command that reaches
    upstream; everything it fetched — scrobbles, tags, the similar-artist graph
    the collaborative signal walks — is in the cache by the time a
    recommendation surface runs, so reading it back needs no credential and
    opens no socket.
    """
    username = getattr(args, "user", DEMO_USER) or DEMO_USER
    if username == DEMO_USER:
        return demo_profile(), demo_catalog(), demo_source()
    source = CachedLastfm(cache)
    profile = profile_from_cache(cache, username)
    if not profile.play_counts:
        raise LiveModeError(
            f"no listening history cached for {username!r} — run "
            f"`lavender ingest --user {username}` first"
        )
    return profile, catalog_from_cache(cache), source


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _iso_date_problem(value: str) -> str | None:
    """Why ``value`` is not an ISO ``YYYY-MM-DD`` date, or ``None`` if it is."""
    try:
        date.fromisoformat(value)
    except ValueError:
        return f"{value!r} is not an ISO date (YYYY-MM-DD)"
    return None


def _unit_interval(value: str) -> float:
    """A float in [0, 1] — the only shape a lens or explore strength may take.

    ``--lens`` was the one unvalidated numeric argument on this CLI: ``--k`` goes
    through :func:`_positive_int`, ``--ttl-days`` through
    :func:`_nonnegative_int`, and ``--explore`` is range-checked downstream in
    ``recommender.diversify``. ``--lens 5``, ``--lens -1``, ``--lens nan`` and
    ``--lens inf`` all parsed, then surfaced as a bare ``ValueError`` traceback
    from inside the ranker (and, for ``report``, from inside a dict
    comprehension) instead of argparse's clean usage error.
    """
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be a finite number")
    if not (0.0 <= parsed <= 1.0):
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _tag_list(raw: str) -> tuple[str, ...]:
    """A comma-separated tag list. Empty entries are dropped, not treated as a tag."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _year(raw: str) -> int:
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a four-digit year") from exc
    if not (MIN_YEAR <= parsed <= MAX_YEAR):
        raise argparse.ArgumentTypeError(f"must be between {MIN_YEAR} and {MAX_YEAR}")
    return parsed


def _add_world_args(parser: argparse.ArgumentParser) -> None:
    """``--user``/``--db``: which world a recommendation surface reads from."""
    parser.add_argument(
        "--user",
        default=DEMO_USER,
        help=f"Last.fm username previously synced with `lavender ingest` (default: the "
        f"offline {DEMO_USER!r} world)",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="cache database path")
    parser.add_argument(
        "--lens-name",
        choices=sorted(LENSES),
        default="women-nonbinary",
        help="which declared values lens boosts: 'women-nonbinary' (default) or "
        "'queer' (sourced queer women + sourced nonbinary artists, ADR 0011)",
    )
    parser.add_argument(
        "--hide-sourced-men",
        action="store_true",
        help="drop artists whose sourced gender is a man's, and acts whose sourced "
        "lineup is entirely sourced men. Never drops unknown-identity artists — "
        "an absent claim is not a claim (see recommender/filters.py)",
    )
    _add_content_filter_args(parser)


def _add_content_filter_args(parser: argparse.ArgumentParser) -> None:
    """``--include-tags``/``--exclude-tags``/``--year-from``/``--year-to``.

    Identity-blind by construction: they read tags and a start year and nothing else, and an
    artist with no tags or no known start year is *kept* by all four (see
    :mod:`recommender.content_filters`).
    """
    parser.add_argument(
        "--include-tags",
        type=_tag_list,
        default=(),
        metavar="TAG,TAG",
        help="keep only artists carrying at least one of these tags. Artists with no "
        "tags at all are kept: an absent tag is not a mismatch",
    )
    parser.add_argument(
        "--exclude-tags",
        type=_tag_list,
        default=(),
        metavar="TAG,TAG",
        help="drop artists carrying any of these tags. Artists with no tags are kept",
    )
    parser.add_argument(
        "--year-from",
        type=_year,
        default=None,
        help="drop artists whose MusicBrainz life-span begins before this year. This is "
        "when the act began, not the year of its first release; artists with no known "
        "start year are kept",
    )
    parser.add_argument(
        "--year-to",
        type=_year,
        default=None,
        help="drop artists whose MusicBrainz life-span begins after this year. Artists "
        "with no known start year are kept",
    )


def _content_filter(args: argparse.Namespace) -> ContentFilter:
    """Build the filter from parsed args, or exit non-zero saying what cannot be honoured."""
    return ContentFilter.build(
        include_tags=args.include_tags,
        exclude_tags=args.exclude_tags,
        year_from=args.year_from,
        year_to=args.year_to,
    )


def _baseline_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _load_eval_baseline(path: Path) -> tuple[dict[str, float], float]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not parse {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("baseline root must be an object")
    raw_metrics = document.get("metrics")
    if not isinstance(raw_metrics, dict) or not raw_metrics:
        raise ValueError("baseline metrics must be a non-empty object")
    metric_names = set(raw_metrics)
    missing = _BASELINE_METRICS - metric_names
    unknown = metric_names - _BASELINE_METRICS
    if unknown:
        raise ValueError(f"baseline contains unknown metric(s): {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"baseline is missing metric(s): {', '.join(sorted(missing))}")
    metrics = {
        field: _baseline_number(value, field=f"metrics.{field}")
        for field, value in raw_metrics.items()
    }
    tolerance = _baseline_number(document.get("tolerance", 0.10), field="tolerance")
    if not 0.0 <= tolerance < 1.0:
        raise ValueError("tolerance must be in [0, 1)")
    return metrics, tolerance


def _warn_unmeasured_guarantees(guarantees: dict[str, object], *, k: int) -> None:
    """Say loudly when a retention guarantee passed over an empty segment.

    A guarantee whose segment never appeared in pure taste's top-k had nothing to
    violate, so it reports no violation. That is not a failure and does not fail the
    run -- whether the eval world *should* contain such an artist is a curation
    decision about real people's sourced identities, not one this gate can make. But
    it is emphatically not the check the harms note promises, and until the empty case
    stopped scoring 1.0 it was indistinguishable from a real pass.
    """

    for label, measured_key, count_key in (
        ("unknown-identity", "unknown_retention_measured", "unknown_base_count"),
        ("sourced-Gender.OTHER", "other_retention_measured", "other_base_count"),
    ):
        if not bool(guarantees[measured_key]):
            print(  # noqa: T201
                f"UNMEASURED: no {label} artist was in pure taste's top-{k}, so the "
                f"{label} retention guarantee passed without testing anything "
                f"({count_key}={guarantees[count_key]}). The eval world needs such an "
                "artist for this guarantee to mean anything.",
                file=sys.stderr,
            )


def _cmd_eval(args: argparse.Namespace) -> int:
    scrobbles, catalog, source = demo_scrobbles(), demo_catalog(), demo_source()
    results = evaluate(DEMO_USER, scrobbles, catalog, source, k=args.k)
    report = to_report(results)
    multiworld = evaluate_worlds(k=args.k)
    report["multiworld"] = multiworld
    # FIX-05: computed exposure / rank-fairness metrics, emitted alongside the eval.
    fairness = fairness_report(DEMO_USER, scrobbles, catalog, source, k=args.k)
    report["fairness"] = fairness

    # AIEV-26/27: regression-vs-baseline, not just beats-popularity. A missing
    # baseline file is a warning, not a failure — the first `lavender eval` run on a
    # fresh clone (or before docs/audits/eval-baseline.json is ever created)
    # must still pass.
    baseline_path = Path(args.baseline)
    regression: dict[str, object] | None = None
    if baseline_path.is_file():
        try:
            baseline_metrics, tolerance = _load_eval_baseline(baseline_path)
        except ValueError as exc:
            print(f"invalid eval baseline: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        regression = check_regression(
            results["hybrid"],
            baseline_metrics,
            tolerance=tolerance,
        )
        report["regression_vs_baseline"] = regression
    else:
        print(f"no baseline at {baseline_path} — skipping regression check", file=sys.stderr)  # noqa: T201

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))  # noqa: T201

    beat_baseline = bool(report["hybrid_beats_popularity"])
    guarantees = cast("dict[str, object]", fairness["guarantees"])
    unknown_retained = bool(guarantees["unknown_retention_all_lenses"])
    # #68: the same gate for the other rank-protected segment, and for the
    # universal no-penalty claim. Before this, the lens's harms note promised
    # both and only the unknown half was ever checked.
    other_retained = bool(guarantees["other_retention_all_lenses"])
    no_score_reduced = bool(guarantees["no_score_reduced_any_artist"])
    _warn_unmeasured_guarantees(guarantees, k=args.k)
    regressed = bool(regression is not None and regression["regressed"])
    if not beat_baseline:
        print("FAIL: hybrid did not beat the popularity baseline", file=sys.stderr)  # noqa: T201
    if not unknown_retained:
        print(  # noqa: T201
            "FAIL: an unknown-identity artist lost score/rank to the values lens "
            f"(unknown-retention < 100%): {guarantees}",
            file=sys.stderr,
        )
    if not other_retained:
        print(  # noqa: T201
            "FAIL: an artist sourced as Gender.OTHER lost score/rank to the values "
            f"lens (other-retention < 100%): {guarantees}",
            file=sys.stderr,
        )
    if not no_score_reduced:
        print(  # noqa: T201
            "FAIL: the values lens reduced some artist's score — the boost-only "
            f"invariant no longer holds on emitted output: {guarantees}",
            file=sys.stderr,
        )
    if regressed:
        print(  # noqa: T201
            f"FAIL: hybrid metrics regressed vs docs/audits/eval-baseline.json: {regression}",
            file=sys.stderr,
        )
    multiworld_passed = bool(multiworld["hybrid_beats_popularity"])
    if not multiworld_passed:
        print("FAIL: hybrid did not beat popularity across fixture worlds", file=sys.stderr)  # noqa: T201
    passed = (
        beat_baseline
        and unknown_retained
        and other_retained
        and no_score_reduced
        and not regressed
        and multiworld_passed
    )
    return 0 if passed else 1


def _cmd_eval_real(args: argparse.Namespace) -> int:
    """LOCAL ONLY: summarize evaluation against the operator's cached plays."""
    report = eval_real(args.user, args.scrobbles, demo_catalog(), demo_source(), k=args.k)
    text = json.dumps(report, indent=2)
    print(text)  # noqa: T201
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0


def _cmd_refresh_live(args: argparse.Namespace) -> int:
    """LIVE: re-ask upstream about artists already in the cache, and fold in corrections.

    The read half of the correction round-trip. ``lavender ingest`` records what
    MusicBrainz and Wikidata said on the day it ran; this re-asks, so an edit
    landed upstream since then — including one the operator filed themselves via
    ``lavender pending-corrections add`` — can reach the local catalog.

    Bounded on purpose. Upstream is ~1 req/s and a real listening history holds
    thousands of artists, so ``--limit`` caps a run and ``--artist`` targets one.
    Re-running resumes: everything already fetched is served from the HTTP cache
    until ``--ttl-days`` ages it out, so the second pass costs only what expired.
    """
    from datetime import date

    today = date.today().isoformat()
    try:
        api_key = _require_api_key()
    except LiveModeError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    with Cache(args.db) as cache:
        if args.artist is not None:
            if cache.get_artist(args.artist) is None:
                print(f"no such artist in cache: {args.artist}", file=sys.stderr)  # noqa: T201
                return 1
            targets: list[str] = [args.artist]
        else:
            # Stalest first, not insertion order: a re-sourced artist gets
            # today's lineage date and sorts to the back, so the next bounded
            # run picks up where this one stopped instead of re-walking the
            # same head of the catalog forever.
            targets = cache.stalest_artist_ids(args.limit)
        # Expire *before* re-enriching, so the refetch below is a real one. The
        # ordering matters: expiring rows that nothing then re-fetches would
        # quietly strip the cached similarity graph `lavender recommend --user`
        # reads, and a thinner recommendation list is not a visible failure.
        expired = cache.expire_http_cache(ttl_days=args.ttl_days, now=today)
        print(f"expired {expired} stale http-cache row(s)")  # noqa: T201
        print(f"re-asking upstream about {len(targets)} cached artist(s) …", flush=True)  # noqa: T201
        source = LastfmClient(api_key, cache)
        enricher = _live_enricher(cache, retrieved_at=today, ttl_days=args.ttl_days)
        outcome = refresh_catalog(
            cache,
            source,
            enricher,
            fetched_at=today,
            artist_ids=targets,
        )
        # Artist keys are MBIDs for most of a real catalog, so resolve display
        # names while the cache is still open — a wall of UUIDs is not a report.
        names = {
            artist_id: (artist.name if (artist := cache.get_artist(artist_id)) else artist_id)
            for artist_id in outcome.protected[:_PROTECTED_PREVIEW]
        }
    print(outcome.summary_line())  # noqa: T201
    for change in outcome.changes:
        print(  # noqa: T201
            f"{names.get(change.artist_id, change.artist_id)}: {change.source_kind} "
            f"{change.old_value} -> {change.new_value} (retrieved {change.retrieved_at})"
        )
    if outcome.protected:
        print(  # noqa: T201
            "kept an existing citation where upstream returned nothing — an unreachable "
            "source and a retracted claim are indistinguishable here, so neither is "
            "applied automatically. Review and use `lavender corrections add` to drop one:"
        )
        for artist_id in outcome.protected[:_PROTECTED_PREVIEW]:
            print(f"  {names.get(artist_id, artist_id)} ({artist_id})")  # noqa: T201
        remaining = len(outcome.protected) - _PROTECTED_PREVIEW
        if remaining > 0:
            # Say the real total. A truncated list that looks complete is the
            # same lie this command exists to stop telling.
            print(  # noqa: T201
                f"  … and {remaining} more (listed {_PROTECTED_PREVIEW} of "
                f"{len(outcome.protected)})"
            )
    pending_path = getattr(args, "pending_corrections", None) or pending_corrections.default_path(
        args.db
    )
    # The one place this may be True. `upstream_answered` is not "we tried" — it
    # is "at least one citation came back over the wire", which is the only
    # evidence that an upstream edit *could* have been observed. Passing True
    # after a silent total failure would close every filed correction as
    # reconciled against an upstream nobody read.
    reconcile_outcome = pending_corrections.reconcile_after_refresh(
        pending_path,
        list(outcome.changes),
        upstream_queried=outcome.upstream_answered,
        observed_at=today,
    )
    for line in reconcile_outcome.report_lines():
        print(line)  # noqa: T201
    if not outcome.upstream_answered and outcome.attempted:
        print(  # noqa: T201
            "error: nothing was verified against upstream — check the network and re-run",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    """Exercise cache refresh with demo fixtures; no upstream enricher is wired."""
    from datetime import date

    if getattr(args, "user", None):
        return _cmd_refresh_live(args)

    catalog = demo_catalog()
    if args.artist:
        catalog = {aid: a for aid, a in catalog.items() if aid == args.artist}
        if not catalog:
            print(f"no such artist: {args.artist}", file=sys.stderr)  # noqa: T201
            return 1
    today = date.today().isoformat()
    with Cache(args.db) as cache:
        expired = cache.expire_http_cache(ttl_days=args.ttl_days, now=today)
        changes = refresh_catalog(cache, catalog, fetched_at=today)
    print("DEMO ONLY: rewrote fixture catalog; no upstream identity API was queried")  # noqa: T201
    source_changes = [
        source_change
        for change in changes
        for source_change in diff_identity_labels(change.artist_id, change.old, change.new)
    ]
    pending_path = getattr(args, "pending_corrections", None) or pending_corrections.default_path(
        args.db
    )
    # DEMO ONLY: the dict branch of refresh_catalog above performs no network
    # fetch, so no upstream edit could have landed and nothing may be
    # reconciled. Flipping this to True is FIX-01's job, once a real
    # EnrichmentSource exists to make the word "upstream" mean something (#70).
    outcome = pending_corrections.reconcile_after_refresh(
        pending_path,
        source_changes,
        upstream_queried=False,
        observed_at=today,
    )
    if changes:
        for change in changes:
            print(  # noqa: T201
                # --- Reviewed suppression: py/clear-text-logging-sensitive-data ---
                # CodeQL flags the expression below because the attribute is
                # literally named ``gender``, which its sensitive-data heuristic
                # classifies as "private". Reviewed 2026-08-01 and suppressed
                # deliberately, for this one expression only:
                #
                # * The sink is ``print`` to **stdout** — this command's report to
                #   the operator who ran it, not a diagnostic log. The
                #   no-identity-in-logs invariant (OBS-11) governs the ``lavender.*``
                #   logger stream and is enforced by ``tests/test_log_privacy.py``;
                #   no logger call site is involved here, so that gate is untouched.
                # * ``IdentityLabel.gender`` is not a secret. A non-UNKNOWN value is
                #   only constructible from at least one cited, SELF_IDENTIFIED
                #   source (``pipeline/models.py``), and showing it alongside that
                #   basis and those sources is the product's stated purpose (README
                #   "Guardrails"). ``lavender recommend`` prints the same fact, and the
                #   dashboard renders it.
                # * This subcommand is DEMO ONLY (see the banner printed above):
                #   ``new`` comes from the fixture catalog committed to this repo and
                #   ``old`` from the operator's own local cache, on their own screen.
                #
                # What this repo actually protects — API keys, OAuth tokens, PKCE
                # verifiers, listening history — never reaches this expression, and
                # the query stays armed everywhere else: the CI gate skips only
                # results CodeQL itself reports as suppressed in source.
                # codeql[py/clear-text-logging-sensitive-data]
                f"{change.artist_id}: {change.old.gender} -> {change.new.gender} "
                f"(sources: {len(change.old.sources)} -> {len(change.new.sources)})"
            )
    else:
        print("no identity-label changes")  # noqa: T201
    print(f"expired {expired} stale http-cache row(s)")  # noqa: T201
    for line in outcome.report_lines():
        print(line)  # noqa: T201
    return 0


def _cmd_corrections(args: argparse.Namespace) -> int:
    """List the local corrections ledger, or add one (citation required)."""
    as_json = bool(getattr(args, "json", False))
    with Cache(args.db) as cache:
        if args.artist or args.value or args.citation:
            if not (args.artist and args.value and args.citation):
                return _corrections_refusal(
                    "adding a correction requires --artist, --value, and --citation",
                    as_json=as_json,
                )
            today = datetime.now(UTC).date().isoformat()
            # A correction whose value the controlled vocabulary does not cover
            # resolves to nothing, for ever. It used to be written anyway and
            # reported as "recorded correction for X: 'femalee'" — a command
            # that printed success for an action that could never take effect,
            # and left a row in the ledger that no refresh could ever act on.
            if normalise_asserted_value(SourceKind.ARTIST_STATEMENT, args.value) is None:
                # The rejected value is deliberately not echoed. It is an asserted
                # gender, and this project's guarantee is that an identity value
                # never leaves the machine it was typed on. stderr is redirected
                # into logs and pasted into transcripts often enough that an echo
                # here would be a plausible first place for one to leak; CodeQL
                # reports it as py/clear-text-logging-sensitive-data and is right
                # to. The caller typed the value a moment ago, so pointing at the
                # accepted vocabulary is enough to act on.
                #
                # The vocabulary itself is not interpolated here either, and that
                # is a design point rather than only an analyser's preference: it
                # is carried in `--value`'s own `help=` (see `_build_parser`),
                # still derived from `accepted_gender_values()` rather than
                # transcribed, so a caller can read the accepted terms *before*
                # spending a run on a value that could never take effect. Naming
                # the schema on the failure path only was the strictly worse half
                # of that.
                return _corrections_refusal(
                    "that is not a value this vocabulary covers, so the "
                    "correction could never take effect. Nothing was written. "
                    "The accepted values are listed under --value in "
                    "`lavender corrections --help`",
                    as_json=as_json,
                )
            retrieved_at = args.retrieved_at or today
            # An unparseable date silently makes the row permanently "stale"
            # (`_parse_iso_date` returns None and every TTL comparison then
            # treats it as never fetched), which is a slow, invisible failure.
            if _iso_date_problem(retrieved_at) is not None:
                return _corrections_refusal(
                    f"--retrieved-at {retrieved_at!r} is not an ISO date "
                    "(YYYY-MM-DD). Nothing was written.",
                    as_json=as_json,
                )
            evidence = IdentityEvidence(
                kind=SourceKind.ARTIST_STATEMENT,
                value=args.value,
                citation=args.citation,
                retrieved_at=retrieved_at,
            )
            try:
                cache.put_correction(args.artist, evidence, entered_at=today)
            except UnsourcedIdentityError as exc:
                return _corrections_refusal(str(exc), as_json=as_json)
            if as_json:
                print(  # noqa: T201
                    emit(
                        correction_recorded_document(
                            artist_id=args.artist,
                            citation=args.citation,
                            retrieved_at=retrieved_at,
                            entered_at=today,
                            database=str(args.db),
                        )
                    ),
                    end="",
                )
                return 0
            print(  # noqa: T201
                f"recorded correction for {args.artist}: {args.value!r} ({args.citation})"
            )
            return 0
        corrections = cache.list_corrections()
        if as_json:
            print(  # noqa: T201
                emit(corrections_document(corrections=corrections, database=str(args.db))),
                end="",
            )
            return 0
        if not corrections:
            print("no corrections recorded")  # noqa: T201
            return 0
        for artist_id, evidence, entered_at in corrections:
            print(  # noqa: T201
                f"{artist_id}: {evidence.value!r} — {evidence.citation} "
                f"(retrieved {evidence.retrieved_at}, entered {entered_at})"
            )
    return 0


def _cmd_pending_corrections(args: argparse.Namespace) -> int:
    """List or file human upstream edits awaiting a future refresh."""
    as_json = bool(getattr(args, "json", False))
    path = args.path or str(pending_corrections.default_path(Path(args.db)))
    if args.pending_command == "add":
        edit_url = upstream_edit_url(args.source_kind, args.citation)
        row = pending_corrections.add_correction(
            path,
            artist_id=args.artist,
            source_kind=args.source_kind,
            citation=args.citation,
            current_value=args.current,
            proposed_value=args.proposed,
            note=args.note,
            filed_at=datetime.now(UTC).date().isoformat(),
            edit_url=edit_url,
        )
        if as_json:
            print(emit(pending_correction_filed_document(row=row, path=path)), end="")  # noqa: T201
            return 0
        print(f"filed pending correction for {row.artist_id} ({row.source_kind})")  # noqa: T201
        if row.edit_url:
            print(f"  fix at source: {row.edit_url}")  # noqa: T201
        return 0
    rows = pending_corrections.list_corrections(path)
    if as_json:
        print(emit(pending_corrections_document(rows=rows, path=path)), end="")  # noqa: T201
        return 0
    if not rows:
        print("no pending corrections")  # noqa: T201
        return 0
    for row in rows:
        print(row.describe())  # noqa: T201
    return 0


def _cmd_ingest_from_file(args: argparse.Namespace) -> int:
    """OFFLINE: read a listening history out of a file the listener already has.

    No API key, and — unless ``--enrich`` is passed — no socket at all. Without
    enrichment every artist stays first-class ``UNKNOWN``, which is a normal
    state here rather than a half-finished one.
    """
    today = datetime.now(UTC).date().isoformat()
    try:
        result = read_history(args.from_file, fmt=args.format)
    except FileIngestError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2

    scrobbles = dedupe(result.scrobbles)
    print(result.summary_line())  # noqa: T201
    if len(scrobbles) != len(result.scrobbles):
        print(  # noqa: T201
            f"  {len(result.scrobbles) - len(scrobbles)} exact repeat(s) in the file "
            "collapsed to one play each"
        )
    if not scrobbles:
        print(  # noqa: T201
            "error: no readable plays in that file — nothing was imported",
            file=sys.stderr,
        )
        return 1

    history = ImportedHistory(args.user, scrobbles)
    with Cache(args.db) as cache:
        cache.put_scrobbles(args.user, scrobbles)
        profile = profile_from_cache(cache, args.user)
        cached_plays = len(cache.get_scrobbles(args.user))
        print(  # noqa: T201
            f"{len(profile.play_counts)} artist(s) played, from {cached_plays} "
            f"cached play(s) for {args.user!r}"
        )
        if not args.enrich:
            print(  # noqa: T201
                "identity was not resolved: every artist is unknown until you run "
                f"`lavender ingest --from-file {args.from_file} --user {args.user} --enrich` "
                "(that reaches MusicBrainz/Wikidata) — unknown is first-class here, so "
                "nothing downstream treats it as a gap"
            )
            print(f"next: lavender recommend --user {args.user}")  # noqa: T201
            return 0

        # The same `ingest` the live path runs, with an offline source: the
        # imported plays are already cached, so its incremental fetch is a
        # no-op, and what it does here is exactly the top-N identity resolution.
        enricher = _live_enricher(cache, retrieved_at=today, ttl_days=args.ttl_days)
        print(f"enriching your top {args.enrich_top} artist(s) …", flush=True)  # noqa: T201
        _profile, catalog = ingest(
            args.user,
            history,
            enricher,
            cache=cache,
            fetched_at=today,
            limit=args.page_size,
            enrich_top=args.enrich_top,
        )
    sourced = sum(1 for artist in catalog.values() if artist.identity.is_known)
    print(  # noqa: T201
        f"cached {len(catalog)} artist(s): {sourced} with a cited basis, "
        f"{len(catalog) - sourced} unknown"
    )
    print(  # noqa: T201
        "note: an export carries plays, not Last.fm's tags or its similar-artist graph, "
        "so this world has no content or collaborative signal to rank with yet. Identity "
        "resolved; ranking needs `lavender ingest --user` against a Last.fm account."
    )
    print(f"next: lavender recommend --user {args.user}")  # noqa: T201
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """LIVE: sync one listener's history and resolve identity from upstream sources."""
    if getattr(args, "from_file", None):
        return _cmd_ingest_from_file(args)
    today = datetime.now(UTC).date().isoformat()
    try:
        api_key = _require_api_key()
    except LiveModeError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    with Cache(args.db) as cache:
        source = LastfmClient(api_key, cache)
        enricher = _live_enricher(cache, retrieved_at=today, ttl_days=args.ttl_days)
        print(f"syncing {args.user}'s listening history …", flush=True)  # noqa: T201
        profile, catalog = ingest(
            args.user,
            source,
            enricher,
            cache=cache,
            fetched_at=today,
            limit=args.page_size,
            enrich_top=args.enrich_top,
        )
        attempted = min(args.enrich_top, len(profile.artist_names))
        print(  # noqa: T201
            f"  {len(profile.play_counts)} artist(s) played; "
            f"{len(catalog)} of your top {attempted} enriched",
            flush=True,
        )
        if attempted and not catalog:
            print(  # noqa: T201
                "error: every enrichment attempt failed — check the network and re-run "
                "(your synced scrobbles are already cached, so a re-run resumes)",
                file=sys.stderr,
            )
            return 1
        if attempted > len(catalog):
            print(  # noqa: T201
                f"  {attempted - len(catalog)} skipped after an upstream error — they stay "
                "in your profile, and a re-run retries just those"
            )
        if not args.no_expand:
            found = discover_candidates(
                profile,
                source,
                seeds=args.seeds,
                per_seed=args.similar,
                limit=args.max_candidates,
            )
            print(  # noqa: T201
                f"enriching {len(found)} candidate artist(s) you have not played …",
                flush=True,
            )
            catalog = {
                **catalog,
                **enrich_candidates(found, source, enricher, cache=cache, fetched_at=today),
            }
    sourced = sum(1 for artist in catalog.values() if artist.identity.is_known)
    print(  # noqa: T201
        f"cached {len(catalog)} artist(s): {sourced} with a cited basis, "
        f"{len(catalog) - sourced} unknown"
    )
    print("  (unknown is first-class here — it never down-ranks anyone)")  # noqa: T201
    print(f"next: lavender recommend --user {args.user}")  # noqa: T201
    return 0


def _refuse(command: str, kind: str, message: str, *, as_json: bool) -> int:
    """One refusal, rendered in whichever shape the caller asked for.

    A caller who passed `--json` gets JSON when the run fails too. A human
    sentence on stderr and an empty stdout is exactly the shape that lets a
    script read a refusal as an empty result.
    """
    if as_json:
        print(emit(error_document(command=command, kind=kind, message=message)), end="")  # noqa: T201
        return 2
    print(f"error: {message}", file=sys.stderr)  # noqa: T201
    return 2


def _corrections_refusal(message: str, *, as_json: bool) -> int:
    """A `corrections` refusal, in whichever shape the caller asked for.

    Exit 1, not the 2 `_refuse` uses. These refusals predate the JSON flag and
    a caller's script already branches on that code; adding an output format is
    not a reason to move a published exit status. The `error` document carries
    the machine-switchable reason, so nothing is lost by the difference.
    """
    if as_json:
        print(  # noqa: T201
            emit(error_document(command="corrections", kind="invalid_input", message=message)),
            end="",
        )
        return 1
    print(f"error: {message}", file=sys.stderr)  # noqa: T201
    return 1


def _add_json_flag(parser: argparse.ArgumentParser, document: str) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            f"emit one versioned {document} document on stdout instead of the "
            f"human rendering (schemas/{document}.schema.json). A refusal is "
            "emitted in the same shape, so a script cannot read one as an "
            "empty result."
        ),
    )


def _cmd_recommend(args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    try:
        content_filter = _content_filter(args)
    except FilterSpecError as exc:
        return _refuse("recommend", "invalid_filter", str(exc), as_json=as_json)
    with Cache(args.db) as cache:
        try:
            profile, catalog, source = _load_world(cache, args)
        except LiveModeError as exc:
            return _refuse("recommend", "live_mode", str(exc), as_json=as_json)
        feedbacks = cache.load_feedback(profile.username)
        cache_schema_version = cache.schema_version
        recs = recommend(
            profile,
            catalog,
            source,
            k=args.k,
            lens_strength=args.lens,
            explore=args.explore,
            feedbacks=feedbacks,
            hide_sourced_men=args.hide_sourced_men,
            lens=LENSES[args.lens_name],
            content_filter=content_filter,
        )
    _record_run(
        surface="recommend",
        recs=recs,
        profile=profile,
        feedbacks=feedbacks,
        args=args,
        cache_schema_version=cache_schema_version,
        content_filter=content_filter,
        explore=args.explore,
    )
    if as_json:
        # The filter description and the coverage summary are inside the
        # document rather than printed beside it: a caller parsing stdout must
        # get one JSON value and nothing else.
        print(  # noqa: T201
            emit(
                recommend_document(
                    recommendations=recs,
                    coverage=identity_coverage(recs),
                    listener=profile.username,
                    lens_name=args.lens_name,
                    lens_strength=args.lens,
                    explore=args.explore,
                    hide_sourced_men=bool(args.hide_sourced_men),
                    k=args.k,
                    content_filter_description=content_filter.describe(),
                )
            ),
            end="",
        )
        return 0
    if content_filter.active:
        print(content_filter.describe())  # noqa: T201
        if not recs:
            print(  # noqa: T201
                "no artist in this world matches those filters — an empty result, not an error"
            )
    print(f"Identity coverage: {identity_coverage(recs).summary_line()}")  # noqa: T201
    for rec in recs:
        why = why_this_artist(rec)
        print(f"{rec.rank:>2}. {rec.artist.name:<22} score={rec.score:.3f}")  # noqa: T201
        print(f"    why: {why.headline}")  # noqa: T201
        print(f"    identity: {why.identity_statement}")  # noqa: T201
        print(f"    rank shift: {why.rank_shift}")  # noqa: T201
        # ADR 0011's second axis (#92). Printed only when something was actually
        # sourced: silence here is the normal answer and never a claim.
        for item in why.queer_provenance:
            print(  # noqa: T201
                f"    {QUEER_SOURCES_HEADING}: {item.source_kind} asserted "
                f"'{item.asserted_value}' ({item.citation}, "
                f"retrieved {item.retrieved_at})"
            )
    return 0


def _record_run(
    *,
    surface: str,
    recs: Sequence[Recommendation],
    profile: ListeningProfile,
    feedbacks: Sequence[Feedback],
    args: argparse.Namespace,
    cache_schema_version: int,
    content_filter: ContentFilter | None,
    explore: float,
) -> None:
    """Write this run's manifest, and never let that failure end the run.

    Recording is observability, not the product. A read-only data directory or a
    full disk must not turn a working recommendation into an error, so the
    failure is reported on stderr and the run stands. The converse would be
    worse than not recording at all: a listener whose `recommend` started
    exiting non-zero because of a bookkeeping file.
    """
    try:
        manifest = build_manifest(
            surface=surface,
            recs=list(recs),
            username=profile.username,
            profile_artist_ids=profile.known_artist_ids,
            profile_total_plays=int(sum(profile.play_counts.values())),
            votes=[(f.artist_id, f.vote) for f in feedbacks],
            lens_name=args.lens_name,
            lens_strength=args.lens,
            explore=explore,
            hide_sourced_men=bool(args.hide_sourced_men),
            k=args.k,
            content_filter=content_filter,
            cache_schema_version=cache_schema_version,
        )
        write_manifest(manifest)
    except (OSError, ValueError) as exc:  # pragma: no cover - environment-dependent
        print(f"warning: could not record this run: {exc}", file=sys.stderr)  # noqa: T201


def _cmd_runs(args: argparse.Namespace) -> int:
    if args.runs_action == "prune":
        removed = prune_manifests(args.keep)
        print(f"pruned {len(removed)} run manifest(s); kept the newest {args.keep}")  # noqa: T201
        return 0
    if args.runs_action == "show":
        try:
            manifest = read_manifest(find_manifest(args.run_id))
        except RunManifestError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))  # noqa: T201
        return 0
    paths = list_manifest_paths()
    if not paths:
        # An empty list is the answer, not an error: nothing has been run yet.
        print("no run manifests recorded yet")  # noqa: T201
        return 0
    for path in paths:
        try:
            manifest = read_manifest(path)
        except RunManifestError as exc:
            # Named and skipped rather than fatal: one unreadable file must not
            # hide every readable one, and silence would hide it entirely.
            print(f"{path.stem}\tUNREADABLE\t{exc}")  # noqa: T201
            continue
        print(  # noqa: T201
            f"{manifest.run_id}\t{manifest.surface}\t{manifest.created_at}\t"
            f"lens={manifest.lens_name}@{manifest.lens_strength}\tk={manifest.k}\t"
            f"listener={manifest.listener_digest[:8]}"
        )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Compare two recorded runs, in whichever shape the caller asked for.

    The two refusals are raised as one exception type and are **not** one fact,
    so they are caught separately and reported under different error kinds. A
    run id that resolves to nothing is ``not_found`` and the caller's next move
    is `lavender runs list`; two runs that answer different questions is
    ``invalid_input`` and the caller's next move is ``--allow-mixed``. A single
    kind covering both would tell a script "something was wrong" and leave the
    one fixable case indistinguishable from the one that is not.

    A manifest that is present but unreadable also lands in ``invalid_input``:
    it was found, so ``not_found`` would be a false statement about it, and the
    ``message`` names which of the two it was. Splitting it out would mean
    adding a value to the published ``error.kind`` enum, which is a contract
    change and not this change's to make.
    """
    as_json = bool(getattr(args, "json", False))
    try:
        before_path = find_manifest(args.before)
        after_path = find_manifest(args.after)
    except RunManifestError as exc:
        return _refuse("diff", "not_found", str(exc), as_json=as_json)
    try:
        before = read_manifest(before_path)
        after = read_manifest(after_path)
        result = diff_runs(before, after, allow_mixed=args.allow_mixed)
    except RunManifestError as exc:
        return _refuse("diff", "invalid_input", str(exc), as_json=as_json)
    if as_json:
        print(emit(diff_document(result)), end="")  # noqa: T201
        return 0
    for line in result.summary_lines():
        print(line)  # noqa: T201
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    try:
        content_filter = _content_filter(args)
    except FilterSpecError as exc:
        return _refuse("export", "invalid_filter", str(exc), as_json=as_json)
    with Cache(args.db) as cache:
        try:
            profile, catalog, source = _load_world(cache, args)
        except LiveModeError as exc:
            return _refuse("export", "live_mode", str(exc), as_json=as_json)
        feedbacks = cache.load_feedback(profile.username)
        cache_schema_version = cache.schema_version
        recs = recommend(
            profile,
            catalog,
            source,
            k=args.k,
            lens_strength=args.lens,
            explore=args.explore,
            feedbacks=feedbacks,
            hide_sourced_men=args.hide_sourced_men,
            lens=LENSES[args.lens_name],
            content_filter=content_filter,
        )
    _record_run(
        surface="export",
        recs=recs,
        profile=profile,
        feedbacks=feedbacks,
        args=args,
        cache_schema_version=cache_schema_version,
        content_filter=content_filter,
        explore=args.explore,
    )
    tracks = recommendations_to_tracks(recs)
    export_format = ExportFormat(args.format)
    playlist_name = "Lavender Rotation"
    text = render(tracks, export_format, playlist_name=playlist_name)
    written_to: str | None = None
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        written_to = str(out)
    if as_json:
        # The envelope wraps the rendered file verbatim, so it cannot carry a
        # field the portable format does not already carry.
        print(  # noqa: T201
            emit(
                export_document(
                    export_format=export_format,
                    playlist_name=playlist_name,
                    tracks=tracks,
                    content=text,
                    generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    written_to=written_to,
                )
            ),
            end="",
        )
        return 0
    if written_to is not None:
        print(f"wrote {written_to}")  # noqa: T201
    else:
        print(text)  # noqa: T201
    # On stderr, so it reaches a person watching the run without entering a playlist file
    # that is contracted to carry artist and track names and nothing else.
    if content_filter.active:
        print(content_filter.describe(), file=sys.stderr)  # noqa: T201
    return 0


def _cmd_feedback(args: argparse.Namespace) -> int:
    """Record or replace one listener's vote for an artist."""
    now = datetime.now(UTC)
    feedback = Feedback(
        username=args.user,
        artist_id=args.artist,
        vote=1 if args.up else -1,
        ts=int(now.timestamp()),
    )
    with Cache(args.db) as cache:
        cache.record_feedback(feedback, fetched_at=now.date().isoformat())
    direction = "up" if feedback.vote > 0 else "down"
    print(f"recorded thumbs-{direction} for {args.artist} ({args.user})")  # noqa: T201
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        content_filter = _content_filter(args)
    except FilterSpecError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    with Cache(args.db) as cache:
        try:
            profile, catalog, source = _load_world(cache, args)
        except LiveModeError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        # The same seam the dashboard and the static build use, so the rendered
        # list and the panel measuring it come from one call (#114). This
        # surface exposes no `--explore`, so the default of 0.0 stands.
        feedbacks = cache.load_feedback(profile.username)
        cache_schema_version = cache.schema_version
        recs, panel = observability_inputs(
            profile,
            catalog,
            source,
            current_lens=args.lens,
            k=args.k,
            panel_k=min(3, args.k),
            hide_sourced_men=args.hide_sourced_men,
            lens=LENSES[args.lens_name],
            content_filter=content_filter,
        )
    # `report` exposes no `--explore`, so 0.0 is the value the run actually used
    # rather than a default standing in for one nobody supplied.
    _record_run(
        surface="report",
        recs=recs,
        profile=profile,
        feedbacks=feedbacks,
        args=args,
        cache_schema_version=cache_schema_version,
        content_filter=content_filter,
        explore=0.0,
    )
    html = render_cards_html(
        recs,
        lens_strength=args.lens,
        username=profile.username,
        exposure_panel=panel,
        filters_line=content_filter.describe() if content_filter.active else None,
    )
    privacy_footer = (
        "<footer><p><strong>Privacy note:</strong> this report contains listening "
        "taste and recommendation data. Share it only with people you intend to.</p></footer>"
    )
    html = html.replace("</body>", f"{privacy_footer}</body>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}")  # noqa: T201
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = run_diagnostics(check_upstream=args.check_upstream)
    if getattr(args, "json", False):
        print(  # noqa: T201
            emit(doctor_document(report, upstream_checked=bool(args.check_upstream))),
            end="",
        )
        return 0 if report.ok else 1
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")  # noqa: T201
    print(f"doctor: {'OK' if report.ok else 'FAIL'}")  # noqa: T201
    return 0 if report.ok else 1


def _cmd_census(args: argparse.Namespace) -> int:
    """Aggregate identity coverage over one cached world. Counts only, no network.

    The world is the same one every other surface reads (`_load_world`): the
    demo catalog, or the operator's own cache under `--user`. The population is
    deliberately the *listening history plus the enriched rows*, not just the
    rows: a cache with one enriched artist out of a thousand must not report
    100% sourced coverage.
    """
    from datetime import date

    from pipeline.census import census

    as_of = args.as_of or date.today().isoformat()
    with Cache(args.db) as cache:
        try:
            profile, catalog, _source = _load_world(cache, args)
        except LiveModeError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        is_demo = (getattr(args, "user", DEMO_USER) or DEMO_USER) == DEMO_USER
        # The demo world is a fixture, not a cache: it has no lineage dates, and
        # reading the cache's would attribute another world's timestamps to it.
        fetched_at = (
            {}
            if is_demo
            else {artist_id: cache.artist_fetched_at(artist_id) for artist_id in catalog}
        )
        local_ids = (
            [] if is_demo else [artist_id for artist_id, _ev, _at in cache.list_corrections()]
        )
    pending_path = getattr(args, "pending_corrections", None) or pending_corrections.default_path(
        args.db
    )
    pending_ids = (
        []
        if is_demo
        else [entry.artist_id for entry in pending_corrections.list_corrections(pending_path)]
    )

    report = census(
        catalog,
        as_of=as_of,
        known_artist_ids=[*profile.play_counts, *catalog],
        fetched_at=fetched_at,
        local_correction_ids=local_ids,
        pending_correction_ids=pending_ids,
    )
    rendered = report.to_json() if args.json or args.out else report.to_text()
    # CodeQL follows the taint from the operator's cache into `rendered` and
    # flags both the file write and the print. The flow is real; the leak is
    # not, and the reason is structural rather than careful:
    #
    # `Census.to_dict()` emits integers under fixed keys — the `Gender`,
    # `IdentityBasis` and `SourceKind` member names, the age-bucket labels, and
    # the `unknown_reason` codes, all module constants. The only free-form
    # strings in the whole document are `schema_version`, the unsupported-reason
    # sentence (a constant), and `as_of`, which is the caller's own `--as-of` or
    # today's date. No artist id, name, or citation URL can reach it.
    #
    # That is asserted, not asserted-by-comment:
    # `tests/test_census.py::test_no_identifier_escapes_even_from_an_adversarial_world`
    # runs a world whose every free-form field is a distinctive token and fails
    # if any of them appears in either rendering, and two more sentinels cover
    # the demo world and the committed artifact. Being aggregate-only is the
    # whole reason this command exists in this shape (ideation E2 was rejected
    # for proposing the per-artist version), so if that ever stops being true
    # those tests fail before this suppression matters.
    #
    # The query stays armed everywhere else — the CI gate skips only results
    # CodeQL itself reports as suppressed in source.
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # codeql[py/clear-text-storage-sensitive-data]
        out.write_text(rendered, encoding="utf-8")
        print(f"wrote {out}")  # noqa: T201
        return 0
    # codeql[py/clear-text-logging-sensitive-data]
    print(rendered, end="")  # noqa: T201
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The whole CLI surface, as an inspectable object.

    Split out of :func:`main` so the command and flag set is a *value* something
    can check documentation against, rather than a shape that only exists while
    an invocation is being parsed. ``tests/test_documented_commands.py`` walks
    it: every ``lavender ...`` invocation written in the repo's Markdown has to
    parse against this parser. Both `README.md` and `CONTRIBUTING.md` documented
    a `lavender corrections add` subcommand that has never existed, and nothing
    could have noticed.
    """
    parser = argparse.ArgumentParser(prog="lavender", description=__doc__)
    parser.add_argument(
        "--log-format",
        choices=LOG_FORMATS,
        default="kv",
        help="stderr log line format (default: kv). Both formats are local-only — "
        "logging never gains a network sink (OBS Tier C).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="offline eval vs popularity baseline")
    p_eval.add_argument("--k", type=_positive_int, default=5)
    p_eval.add_argument("--out", default="docs/audits/eval-report.json")
    p_eval.add_argument(
        "--baseline",
        default="docs/audits/eval-baseline.json",
        help="committed baseline metrics to regression-check against (AIEV-26/27)",
    )
    p_eval.set_defaults(func=_cmd_eval)

    p_eval_real = sub.add_parser(
        "eval-real", help="LOCAL ONLY: eval against your cached scrobbles; never CI"
    )
    p_eval_real.add_argument("--user", required=True)
    p_eval_real.add_argument("--scrobbles", required=True, metavar="PATH")
    p_eval_real.add_argument("--k", type=_positive_int, default=10)
    p_eval_real.add_argument("--out", default=None)
    p_eval_real.set_defaults(func=_cmd_eval_real)

    p_ingest = sub.add_parser(
        "ingest",
        help="sync a Last.fm history (LIVE, needs an API key) or read one from a file "
        "(--from-file, offline)",
    )
    p_ingest.add_argument(
        "--user",
        required=True,
        help="Last.fm username to sync, or — with --from-file — the local name to file the "
        "imported history under (no account is contacted)",
    )
    p_ingest.add_argument(
        "--from-file",
        default=None,
        metavar="PATH",
        help="read the listening history out of an export you already have instead of "
        "fetching it. No API key, and no network at all unless --enrich is given.",
    )
    p_ingest.add_argument(
        "--format",
        choices=FILE_FORMATS,
        default="auto",
        help="which export shape --from-file holds (default: auto, which sniffs and "
        "refuses to guess rather than importing under the wrong contract)",
    )
    p_ingest.add_argument(
        "--enrich",
        action="store_true",
        help="with --from-file: also resolve identity against MusicBrainz/Wikidata. "
        "Without it the run opens no socket and every artist stays first-class unknown.",
    )
    p_ingest.add_argument("--db", default=str(DEFAULT_DB_PATH), help="cache database path")
    p_ingest.add_argument(
        "--page-size",
        type=_positive_int,
        default=200,
        help="scrobbles per Last.fm page (the sync is incremental and resumable)",
    )
    p_ingest.add_argument(
        "--enrich-top",
        type=_positive_int,
        default=DEFAULT_ENRICH_TOP,
        help="how many of your own most-played artists to enrich (default: 50)",
    )
    p_ingest.add_argument(
        "--seeds",
        type=_positive_int,
        default=DEFAULT_SEEDS,
        help="top artists used as discovery seeds",
    )
    p_ingest.add_argument(
        "--similar",
        type=_positive_int,
        default=DEFAULT_PER_SEED,
        help="similar artists considered per seed",
    )
    p_ingest.add_argument(
        "--max-candidates",
        type=_positive_int,
        default=DEFAULT_CANDIDATE_LIMIT,
        help="cap on candidate artists enriched in one run",
    )
    p_ingest.add_argument(
        "--no-expand",
        action="store_true",
        help="sync and enrich your own artists only; skip candidate discovery",
    )
    p_ingest.add_argument(
        "--ttl-days",
        type=_nonnegative_int,
        default=DEFAULT_HTTP_TTL_DAYS,
        help="treat cached upstream responses older than this as stale and re-fetch",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_rec = sub.add_parser("recommend", help="print recommendations (demo world unless --user)")
    p_rec.add_argument("--k", type=_positive_int, default=10)
    p_rec.add_argument("--lens", type=_unit_interval, default=0.5)
    p_rec.add_argument(
        "--explore",
        type=_unit_interval,
        default=0.0,
        help="serendipity slider in [0,1]; 0=pure relevance, 1=max tag-space diversity",
    )
    _add_json_flag(p_rec, "recommend")
    _add_world_args(p_rec)
    p_rec.set_defaults(func=_cmd_recommend)

    p_exp = sub.add_parser("export", help="export recommendations to a portable playlist file")
    p_exp.add_argument(
        "--format", choices=[str(f) for f in ExportFormat], default=str(ExportFormat.TEXT)
    )
    p_exp.add_argument("--k", type=_positive_int, default=10)
    p_exp.add_argument("--lens", type=_unit_interval, default=0.5)
    p_exp.add_argument(
        "--explore",
        type=_unit_interval,
        default=0.0,
        help="serendipity slider in [0,1]; 0=pure relevance, 1=max tag-space diversity",
    )
    p_exp.add_argument("--out", default=None, help="write to a file instead of stdout")
    _add_json_flag(p_exp, "export")
    _add_world_args(p_exp)
    p_exp.set_defaults(func=_cmd_export)

    p_feedback = sub.add_parser("feedback", help="record a thumbs vote that tunes future rankings")
    p_feedback.add_argument("--artist", required=True, help="artist_id to vote on")
    p_feedback.add_argument("--user", default=DEMO_USER)
    p_feedback.add_argument("--db", default=str(DEFAULT_DB_PATH))
    feedback_vote = p_feedback.add_mutually_exclusive_group(required=True)
    feedback_vote.add_argument("--up", action="store_true")
    feedback_vote.add_argument("--down", action="store_true")
    p_feedback.set_defaults(func=_cmd_feedback)

    p_report = sub.add_parser(
        "report", help="write a self-contained, accessible HTML discovery report"
    )
    p_report.add_argument("--k", type=_positive_int, default=10)
    p_report.add_argument("--lens", type=_unit_interval, default=0.5)
    p_report.add_argument("--out", default="my-discoveries.html")
    _add_world_args(p_report)
    p_report.set_defaults(func=_cmd_report)

    p_census = sub.add_parser(
        "census",
        help="aggregate identity coverage over a cached world (counts only, no artist list)",
    )
    p_census.add_argument(
        "--json", action="store_true", help="emit the JSON document instead of the text table"
    )
    p_census.add_argument(
        "--out",
        default=None,
        help="write the JSON document to a file (implies --json)",
    )
    p_census.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="the date lineage ages are measured against (default: today). Explicit so a "
        "committed census can be regenerated and compared without today's date changing it.",
    )
    _add_world_args(p_census)
    p_census.set_defaults(func=_cmd_census)

    p_doctor = sub.add_parser("doctor", help="diagnose env, data location, and cache health")
    p_doctor.add_argument(
        "--check-upstream",
        action="store_true",
        help="also probe upstream APIs (opt-in; makes network calls)",
    )
    _add_json_flag(p_doctor, "doctor")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_ref = sub.add_parser(
        "refresh",
        help="re-ask upstream about cached artists (--user), or rewrite the fixture cache",
    )
    p_ref.add_argument("--db", default=str(DEFAULT_DB_PATH), help="cache database path")
    p_ref.add_argument(
        "--user",
        default=None,
        help="re-enrich the catalog this listener ingested, against the live "
        "MusicBrainz/Wikidata sources. Omit for the DEMO-ONLY fixture rewrite.",
    )
    p_ref.add_argument("--artist", default=None, help="refresh only this artist_id")
    p_ref.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_REFRESH_LIMIT,
        help=f"with --user, how many cached artists to re-ask about in one run "
        f"(default: {DEFAULT_REFRESH_LIMIT}). Upstream is ~1 req/s, so a whole "
        f"catalog is many runs; each one resumes from the HTTP cache.",
    )
    p_ref.add_argument(
        "--ttl-days",
        type=_nonnegative_int,
        default=DEFAULT_HTTP_TTL_DAYS,
        help="expire http-cache rows older than this many days before refetching",
    )
    p_ref.add_argument(
        "--pending-corrections",
        default=None,
        help="pending upstream corrections file to reconcile",
    )
    p_ref.set_defaults(func=_cmd_refresh)

    p_corr = sub.add_parser(
        "corrections", help="list the local corrections ledger, or add one (FIX-10)"
    )
    p_corr.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p_corr.add_argument("--artist", default=None, help="artist_id to correct")
    # Derived from the resolver, never transcribed: a help string that repeats the
    # vocabulary by hand goes stale the first time the vocabulary changes, and this
    # is the one place a caller can read the accepted terms before spending a run
    # on a value `normalise_asserted_value` would refuse.
    p_corr.add_argument(
        "--value",
        default=None,
        help=(
            "asserted gender value, e.g. 'woman'. Accepted: " + ", ".join(accepted_gender_values())
        ),
    )
    p_corr.add_argument("--citation", default=None, help="citation (required to add)")
    p_corr.add_argument("--retrieved-at", default=None, help="ISO date; defaults to today")
    _add_json_flag(p_corr, "corrections")
    p_corr.set_defaults(func=_cmd_corrections)

    p_runs = sub.add_parser(
        "runs", help="browse the manifests each recommend/report/export run records"
    )
    runs_sub = p_runs.add_subparsers(dest="runs_action")
    runs_sub.add_parser("list", help="list recorded runs, oldest first")
    p_runs_show = runs_sub.add_parser("show", help="print one run manifest as JSON")
    p_runs_show.add_argument("run_id", help="run id, or an unambiguous prefix of one")
    p_runs_prune = runs_sub.add_parser("prune", help="delete all but the newest N manifests")
    p_runs_prune.add_argument(
        "--keep", type=_nonnegative_int, default=DEFAULT_KEEP, help="how many to keep"
    )
    p_runs.set_defaults(func=_cmd_runs, runs_action="list")

    p_diff = sub.add_parser("diff", help="what changed between two recorded runs, and why")
    p_diff.add_argument("before", help="earlier run id, or an unambiguous prefix")
    p_diff.add_argument("after", help="later run id, or an unambiguous prefix")
    _add_json_flag(p_diff, "diff")
    p_diff.add_argument(
        "--allow-mixed",
        action="store_true",
        help=(
            "compare runs that differ in listener, lens name, or content filter. "
            "Those runs are answers to different questions, so read the result as "
            "such rather than as a change in the same list."
        ),
    )
    p_diff.set_defaults(func=_cmd_diff)

    p_pending = sub.add_parser(
        "pending-corrections", help="list or file pending human upstream edits (EXP-05)"
    )
    p_pending.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p_pending.add_argument("--path", default=None, help="pending JSON file (default: beside --db)")
    _add_json_flag(p_pending, "pending-corrections")
    pending_sub = p_pending.add_subparsers(dest="pending_command")
    p_pending_add = pending_sub.add_parser("add", help="file a pending upstream correction")
    p_pending_add.add_argument("--artist", required=True)
    p_pending_add.add_argument("--source-kind", required=True)
    p_pending_add.add_argument("--citation", required=True)
    p_pending_add.add_argument("--current", default="")
    p_pending_add.add_argument("--proposed", required=True)
    p_pending_add.add_argument("--note", default="")
    p_pending.set_defaults(func=_cmd_pending_corrections)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(log_format=args.log_format)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

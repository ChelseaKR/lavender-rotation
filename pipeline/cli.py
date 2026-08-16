"""Command-line entry point: ``lavender ingest|eval|recommend|export|refresh``.

Argparse glue over the library; omitted from coverage accounting, but the gate
behaviour of ``lavender eval`` (exit codes, regression/fairness blocks) and ``wad
refresh`` is exercised directly by ``tests/test_eval.py`` and
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
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from app.render import render_cards_html
from export.models import ExportFormat
from export.tracklist import recommendations_to_tracks, render
from recommender.coverage import identity_coverage
from recommender.eval import (
    check_regression,
    eval_real,
    evaluate,
    evaluate_worlds,
    fairness_report,
    to_report,
)
from recommender.exposure import observability_panel
from recommender.feedback import Feedback
from recommender.hybrid import recommend
from recommender.lens import LENSES
from recommender.upstream import upstream_edit_url
from recommender.why import why_this_artist

from pipeline import corrections as pending_corrections
from pipeline.cache import DEFAULT_DB_PATH, DEFAULT_HTTP_TTL_DAYS, Cache
from pipeline.demo import DEMO_USER, demo_catalog, demo_profile, demo_scrobbles, demo_source
from pipeline.doctor import run_diagnostics
from pipeline.enrich import MusicBrainzEnricher
from pipeline.http import CachedHttpFetcher, build_user_agent
from pipeline.identity import IdentityEvidence
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
from pipeline.lastfm import CachedLastfm, LastfmClient, ScrobbleSource
from pipeline.logconfig import LOG_FORMATS, configure_logging
from pipeline.models import Artist, ListeningProfile, SourceKind, UnsourcedIdentityError

_BASELINE_METRICS = frozenset({"precision_at_k", "recall_at_k", "map_at_k"})

#: Number of the listener's own most-played artists that a live ingest enriches.
#: See ``pipeline.ingest.ingest``'s ``enrich_top`` for why this is bounded.
DEFAULT_ENRICH_TOP = 50


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


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
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


def _cmd_refresh(args: argparse.Namespace) -> int:
    """Exercise cache refresh with demo fixtures; no upstream enricher is wired."""
    from datetime import date

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
                #   no-identity-in-logs invariant (OBS-11) governs the ``wad.*``
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
    with Cache(args.db) as cache:
        if args.artist or args.value or args.citation:
            if not (args.artist and args.value and args.citation):
                print(  # noqa: T201
                    "error: adding a correction requires --artist, --value, and --citation",
                    file=sys.stderr,
                )
                return 1
            today = datetime.now(UTC).date().isoformat()
            evidence = IdentityEvidence(
                kind=SourceKind.ARTIST_STATEMENT,
                value=args.value,
                citation=args.citation,
                retrieved_at=args.retrieved_at or today,
            )
            try:
                cache.put_correction(args.artist, evidence, entered_at=today)
            except UnsourcedIdentityError as exc:
                print(f"error: {exc}", file=sys.stderr)  # noqa: T201
                return 1
            print(  # noqa: T201
                f"recorded correction for {args.artist}: {args.value!r} ({args.citation})"
            )
            return 0
        corrections = cache.list_corrections()
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
        print(f"filed pending correction for {row.artist_id} ({row.source_kind})")  # noqa: T201
        if row.edit_url:
            print(f"  fix at source: {row.edit_url}")  # noqa: T201
        return 0
    rows = pending_corrections.list_corrections(path)
    if not rows:
        print("no pending corrections")  # noqa: T201
        return 0
    for row in rows:
        print(row.describe())  # noqa: T201
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """LIVE: sync one listener's history and resolve identity from upstream sources."""
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


def _cmd_recommend(args: argparse.Namespace) -> int:
    with Cache(args.db) as cache:
        try:
            profile, catalog, source = _load_world(cache, args)
        except LiveModeError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        feedbacks = cache.load_feedback(profile.username)
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
        )
    print(f"Identity coverage: {identity_coverage(recs).summary_line()}")  # noqa: T201
    for rec in recs:
        why = why_this_artist(rec)
        print(f"{rec.rank:>2}. {rec.artist.name:<22} score={rec.score:.3f}")  # noqa: T201
        print(f"    why: {why.headline}")  # noqa: T201
        print(f"    identity: {why.identity_statement}")  # noqa: T201
        print(f"    rank shift: {why.rank_shift}")  # noqa: T201
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    with Cache(args.db) as cache:
        try:
            profile, catalog, source = _load_world(cache, args)
        except LiveModeError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        feedbacks = cache.load_feedback(profile.username)
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
        )
    tracks = recommendations_to_tracks(recs)
    text = render(tracks, ExportFormat(args.format), playlist_name="Lavender Rotation")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")  # noqa: T201
    else:
        print(text)  # noqa: T201
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
    with Cache(args.db) as cache:
        try:
            profile, catalog, source = _load_world(cache, args)
        except LiveModeError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        recs_by_lens = {
            lens: recommend(
                profile,
                catalog,
                source,
                k=args.k,
                lens_strength=lens,
                hide_sourced_men=args.hide_sourced_men,
                lens=LENSES[args.lens_name],
            )
            for lens in sorted({0.0, 0.25, 0.5, 0.75, 1.0, args.lens})
        }
    panel = observability_panel(recs_by_lens, current_lens=args.lens, k=min(3, args.k))
    html = render_cards_html(
        recs_by_lens[args.lens],
        lens_strength=args.lens,
        username=profile.username,
        exposure_panel=panel,
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
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")  # noqa: T201
    print(f"doctor: {'OK' if report.ok else 'FAIL'}")  # noqa: T201
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
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
        help="LIVE: sync a Last.fm history and resolve identity upstream (needs an API key)",
    )
    p_ingest.add_argument("--user", required=True, help="Last.fm username to sync")
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
    p_rec.add_argument("--lens", type=float, default=0.5)
    p_rec.add_argument(
        "--explore",
        type=float,
        default=0.0,
        help="serendipity slider in [0,1]; 0=pure relevance, 1=max tag-space diversity",
    )
    _add_world_args(p_rec)
    p_rec.set_defaults(func=_cmd_recommend)

    p_exp = sub.add_parser("export", help="export recommendations to a portable playlist file")
    p_exp.add_argument(
        "--format", choices=[str(f) for f in ExportFormat], default=str(ExportFormat.TEXT)
    )
    p_exp.add_argument("--k", type=_positive_int, default=10)
    p_exp.add_argument("--lens", type=float, default=0.5)
    p_exp.add_argument(
        "--explore",
        type=float,
        default=0.0,
        help="serendipity slider in [0,1]; 0=pure relevance, 1=max tag-space diversity",
    )
    p_exp.add_argument("--out", default=None, help="write to a file instead of stdout")
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
    p_report.add_argument("--lens", type=float, default=0.5)
    p_report.add_argument("--out", default="my-discoveries.html")
    _add_world_args(p_report)
    p_report.set_defaults(func=_cmd_report)

    p_doctor = sub.add_parser("doctor", help="diagnose env, data location, and cache health")
    p_doctor.add_argument(
        "--check-upstream",
        action="store_true",
        help="also probe upstream APIs (opt-in; makes network calls)",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_ref = sub.add_parser(
        "refresh", help="DEMO ONLY: rewrite fixture cache; no upstream identity re-enrichment"
    )
    p_ref.add_argument("--db", default=str(DEFAULT_DB_PATH), help="cache database path")
    p_ref.add_argument("--artist", default=None, help="refresh only this artist_id")
    p_ref.add_argument(
        "--ttl-days",
        type=_nonnegative_int,
        default=DEFAULT_HTTP_TTL_DAYS,
        help="expire demo http-cache rows older than this many days",
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
    p_corr.add_argument("--value", default=None, help="asserted gender value, e.g. 'woman'")
    p_corr.add_argument("--citation", default=None, help="citation (required to add)")
    p_corr.add_argument("--retrieved-at", default=None, help="ISO date; defaults to today")
    p_corr.set_defaults(func=_cmd_corrections)

    p_pending = sub.add_parser(
        "pending-corrections", help="list or file pending human upstream edits (EXP-05)"
    )
    p_pending.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p_pending.add_argument("--path", default=None, help="pending JSON file (default: beside --db)")
    pending_sub = p_pending.add_subparsers(dest="pending_command")
    p_pending_add = pending_sub.add_parser("add", help="file a pending upstream correction")
    p_pending_add.add_argument("--artist", required=True)
    p_pending_add.add_argument("--source-kind", required=True)
    p_pending_add.add_argument("--citation", required=True)
    p_pending_add.add_argument("--current", default="")
    p_pending_add.add_argument("--proposed", required=True)
    p_pending_add.add_argument("--note", default="")
    p_pending.set_defaults(func=_cmd_pending_corrections)

    args = parser.parse_args(argv)
    configure_logging(log_format=args.log_format)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

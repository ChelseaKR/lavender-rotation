"""Ingest orchestration: username -> stored listening profile + enriched catalog.

Ties together the :class:`~pipeline.lastfm.ScrobbleSource`, the
:class:`~pipeline.enrich.EnrichmentSource`, the identity resolver, and the local
:class:`~pipeline.cache.Cache`. The result is:

* a :class:`~pipeline.models.ListeningProfile` (per-artist play weights + tags), and
* a catalog of enriched :class:`~pipeline.models.Artist` objects with *sourced*
  identity + composition (defaulting to unknown), each cached with a fetch date.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Optional, overload

from pipeline.cache import Cache
from pipeline.enrich import CareerSpanSource, EnrichmentSource
from pipeline.identity import resolve_composition, resolve_identity, resolve_queer_identity
from pipeline.lastfm import NamedSimilaritySource, ScrobbleSource
from pipeline.models import Artist, IdentityLabel, ListeningProfile, Scrobble, Source

log = logging.getLogger("lavender.ingest")

#: Discovery defaults. A first live ingest is bounded by how long an operator
#: will wait on a 1 req/s upstream, not by how much we could fetch: these
#: numbers put a first run in the minutes, and every response is cached, so a
#: second run costs nothing.
DEFAULT_SEEDS = 15
DEFAULT_PER_SEED = 12
DEFAULT_CANDIDATE_LIMIT = 150


def build_profile(
    username: str,
    scrobbles: list[Scrobble],
    *,
    now_ts: Optional[int] = None,
    half_life_days: Optional[float] = None,
    era_start: Optional[int] = None,
    era_end: Optional[int] = None,
) -> ListeningProfile:
    """Reduce scrobbles into reproducible, optionally time-shaped play weights.

    The inclusive era window is applied first. Recency weighting then uses the
    newest retained scrobble as its reference unless ``now_ts`` is supplied,
    avoiding wall-clock-dependent recommendations.
    """
    if half_life_days is not None and half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    if era_start is not None and era_end is not None and era_start > era_end:
        raise ValueError("era_start must be less than or equal to era_end")

    windowed = [
        s
        for s in scrobbles
        if (era_start is None or s.ts >= era_start) and (era_end is None or s.ts <= era_end)
    ]
    effective_now = now_ts if now_ts is not None else max((s.ts for s in windowed), default=0)
    play_counts: dict[str, float] = {}
    artist_names: dict[str, str] = {}
    for s in windowed:
        key = s.artist_id or s.artist_name
        if not key:
            continue
        weight = 1.0
        if half_life_days is not None:
            age_seconds = max(0, effective_now - s.ts)
            weight = 0.5 ** (age_seconds / (half_life_days * 86400))
        play_counts[key] = play_counts.get(key, 0.0) + weight
        artist_names.setdefault(key, s.artist_name)
    return ListeningProfile(
        username=username,
        play_counts=play_counts,
        artist_names=artist_names,
        tags={},  # filled in during enrichment
    )


def enrich_artist(
    artist_id: str,
    name: str,
    source: ScrobbleSource,
    enricher: EnrichmentSource,
    *,
    listeners: int = 0,
    playcount: int = 0,
    cache: Optional[Cache] = None,
) -> Artist:
    """Build a fully enriched :class:`Artist` with sourced identity + composition.

    When ``cache`` is supplied, any locally-entered corrections (FIX-10) for
    this artist are fed into the resolver alongside the enricher's evidence.
    A correction is itself an ``ARTIST_STATEMENT`` — the resolver's existing
    priority order (``ARTIST_STATEMENT`` highest) is what lets it win, with no
    special-casing in :func:`~pipeline.identity.resolve_identity`.
    """
    tags = source.artist_tags(artist_id)
    evidence = list(enricher.gender_evidence(artist_id))
    evidence.extend(enricher.orientation_evidence(artist_id))
    if cache is not None:
        evidence.extend(cache.get_corrections(artist_id))
    identity = resolve_identity(evidence)
    # The same evidence, read for the second axis (ADR 0011). Both resolvers
    # filter to the source kinds *they* accept, so a P91 claim can never move a
    # gender label and a P21 claim can never move an orientation.
    queer = resolve_queer_identity(evidence)
    fronts, comp_evidence = enricher.composition_evidence(artist_id)
    composition = resolve_composition(fronts, comp_evidence)
    # Optional second protocol (see `enrich.CareerSpanSource`): an enricher that cannot state a
    # start year yields none, which is the same answer as upstream having none. Either way the
    # era filter keeps the artist, so no enricher is penalized for not implementing it.
    career_start_year = (
        enricher.career_start_year(artist_id) if isinstance(enricher, CareerSpanSource) else None
    )
    return Artist(
        artist_id=artist_id,
        name=name,
        tags=tags,
        identity=identity,
        queer=queer,
        composition=composition,
        career_start_year=career_start_year,
        listeners=listeners,
        playcount=playcount,
    )


def ingest(
    username: str,
    source: ScrobbleSource,
    enricher: EnrichmentSource,
    *,
    cache: Optional[Cache] = None,
    fetched_at: str = "1970-01-01",
    limit: int = 200,
    enrich_top: Optional[int] = None,
) -> tuple[ListeningProfile, dict[str, Artist]]:
    """Run the full ingest. Returns the listening profile and an enriched catalog.

    When a ``cache`` is supplied, ingest is paginated and incremental (FIX-02):
    only scrobbles newer than the cache's watermark (``Cache.last_synced_ts``)
    are fetched, merged into the cache (idempotently — refetching the same
    range is harmless since only ``ts > since`` is ever requested), and the
    listening profile is built from the *full* stored history so play counts
    reflect everything synced so far, not just this run's delta. Enriched
    artists are persisted with the given ``fetched_at`` lineage timestamp.

    Without a ``cache``, ingest is a single-page snapshot, as before.

    ``enrich_top`` bounds *enrichment* (not the profile) to the N most-played
    artists. A years-deep listening history holds thousands of distinct artists,
    and enriching every one against a 1 req/s upstream would take hours to
    establish identity for artists that are excluded from recommendation anyway
    — the listener already plays them. The unenriched ones stay in
    ``play_counts``/``artist_names``, which is what keeps them excluded; they
    just contribute no tags to the content profile, where the play-count
    weighting had already made their contribution negligible. ``None`` (the
    default) enriches everything, as before.

    An artist whose enrichment *fails* is skipped rather than fatal; compare
    ``len(catalog)`` against the number selected to see how many were lost.
    """
    ingest_start = time.monotonic()
    log.info("stage=ingest event=start username=%s limit=%d", username, limit)
    fetch_start = time.monotonic()
    try:
        if cache is not None:
            since = cache.last_synced_ts(username)
            fetched = source.scrobbles_since(username, since_ts=since, page_size=limit)
            cache.put_scrobbles(username, fetched)
            scrobbles = cache.get_scrobbles(username)
        else:
            scrobbles = source.recent_scrobbles(username, limit=limit)
    except Exception:
        log.exception(
            "stage=fetch_scrobbles event=failed username=%s source=%s",
            username,
            type(source).__name__,
        )
        raise
    log.info(
        "stage=fetch_scrobbles event=end elapsed=%.3fs count=%d",
        time.monotonic() - fetch_start,
        len(scrobbles),
    )
    profile = build_profile(username, scrobbles)

    catalog: dict[str, Artist] = {}
    tags_by_artist: dict[str, tuple[str, ...]] = {}
    selected = (
        profile.artist_names
        if enrich_top is None
        else {aid: profile.artist_names[aid] for aid in profile.top_artists(enrich_top)}
    )
    for artist_id, name in selected.items():
        try:
            artist = enrich_artist(
                artist_id,
                name,
                source,
                enricher,
                playcount=int(profile.play_counts.get(artist_id, 0)),
                cache=cache,
            )
        except Exception:
            # Skip the artist, keep the run. This used to re-raise, which meant
            # one `ReadTimeout` on one artist's tags discarded an entire live
            # ingest — 95k scrobbles and 48 enriched artists, thrown away nine
            # minutes in, over a blip that a retry now absorbs anyway. A
            # listening history is long enough that *something* will fail, and
            # a skipped artist degrades exactly as an artist with no upstream
            # claim does: they stay in the profile (so they are still excluded
            # from recommendation, which is what matters) and contribute no
            # tags. The count is returned to the caller to report.
            log.exception(
                "stage=enrich event=failed artist_id=%s enricher=%s",
                artist_id,
                type(enricher).__name__,
            )
            continue
        catalog[artist_id] = artist
        tags_by_artist[artist_id] = artist.tags
        if cache is not None:
            cache.put_artist(artist, fetched_at=fetched_at)

    # Re-emit the profile with tags now known (frozen dataclass → rebuild).
    profile = ListeningProfile(
        username=profile.username,
        play_counts=profile.play_counts,
        artist_names=profile.artist_names,
        tags=tags_by_artist,
    )
    log.info(
        "stage=ingest event=end elapsed=%.3fs username=%s artists=%d",
        time.monotonic() - ingest_start,
        username,
        len(catalog),
    )
    return profile, catalog


@dataclass(frozen=True)
class Candidate:
    """An artist the listener has *not* played, reachable from ones they have."""

    artist_id: str
    name: str
    affinity: float


def discover_candidates(
    profile: ListeningProfile,
    source: NamedSimilaritySource,
    *,
    seeds: int = DEFAULT_SEEDS,
    per_seed: int = DEFAULT_PER_SEED,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[Candidate]:
    """Find enrichable candidates from the similar-artist graph around a profile.

    Ingest alone cannot produce a recommendation: it enriches the artists a
    listener *already plays*, and :func:`recommender.hybrid.recommend` excludes
    those by construction, so a catalog built only from a listening history
    yields an empty list. This is the step that makes the live world non-empty —
    the offline demo gets the same thing for free from its fixture catalog.

    Deterministic: seeds are the top-played artists, candidates accumulate
    ``seed_share * similarity`` across seeds, and ties break on the artist key.
    This mirrors :func:`recommender.collaborative.collaborative_scores` on
    purpose — the artists worth *paying an upstream fetch for* are the ones the
    collaborative signal will actually score.
    """
    known = profile.known_artist_ids
    known_names = profile.known_artist_names
    total = sum(profile.play_counts.values()) or 1.0
    affinity: dict[str, float] = {}
    names: dict[str, str] = {}
    for seed_id in profile.top_artists(seeds):
        share = profile.play_counts[seed_id] / total
        for edge in source.similar_artists_named(seed_id)[:per_seed]:
            if edge.artist_id in known or edge.artist_id == seed_id:
                continue
            # Same aliasing guard the re-ranker applies, one step earlier, so an
            # artist the listener already plays does not cost an upstream fetch.
            if edge.name.strip().casefold() in known_names:
                continue
            affinity[edge.artist_id] = affinity.get(edge.artist_id, 0.0) + share * edge.match
            names.setdefault(edge.artist_id, edge.name)
    ordered = sorted(affinity.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [
        Candidate(artist_id=aid, name=names[aid], affinity=round(score, 6))
        for aid, score in ordered
    ]


def enrich_candidates(
    candidates: Sequence[Candidate],
    source: ScrobbleSource,
    enricher: EnrichmentSource,
    *,
    cache: Optional[Cache] = None,
    fetched_at: str = "1970-01-01",
) -> dict[str, Artist]:
    """Enrich discovered candidates into a catalog, skipping ones that fail.

    Unlike :func:`ingest`, one bad artist does not sink the run. A candidate is
    speculative — nobody asked for it by name — so an upstream 404 on the
    fortieth of a hundred candidates should cost that candidate, not the
    ninety-nine already paid for.
    """
    catalog: dict[str, Artist] = {}
    for candidate in candidates:
        try:
            artist = enrich_artist(
                candidate.artist_id, candidate.name, source, enricher, cache=cache
            )
        except Exception:
            log.exception("stage=enrich_candidate event=failed artist_id=%s", candidate.artist_id)
            continue
        catalog[candidate.artist_id] = artist
        if cache is not None:
            cache.put_artist(artist, fetched_at=fetched_at)
    log.info(
        "stage=enrich_candidates event=end requested=%d enriched=%d",
        len(candidates),
        len(catalog),
    )
    return catalog


def profile_from_cache(
    cache: Cache,
    username: str,
    *,
    half_life_days: Optional[float] = None,
    era_start: Optional[int] = None,
    era_end: Optional[int] = None,
) -> ListeningProfile:
    """Rebuild a listening profile from the cache, with no network at all.

    The read half of a live ingest: once ``lavender ingest`` has synced, every later
    command works from local data. Content tags come back off the cached artist
    rows, so the content signal survives a restart.
    """
    scrobbles = cache.get_scrobbles(username)
    profile = build_profile(
        username,
        scrobbles,
        half_life_days=half_life_days,
        era_start=era_start,
        era_end=era_end,
    )
    tags: dict[str, tuple[str, ...]] = {}
    for artist_id in profile.artist_names:
        cached = cache.get_artist(artist_id)
        if cached is not None:
            tags[artist_id] = cached.tags
    return ListeningProfile(
        username=profile.username,
        play_counts=profile.play_counts,
        artist_names=profile.artist_names,
        tags=tags,
    )


def catalog_from_cache(cache: Cache) -> dict[str, Artist]:
    """Every enriched artist the cache holds — the live counterpart of the demo catalog."""
    catalog: dict[str, Artist] = {}
    for artist_id in cache.list_artist_ids():
        cached = cache.get_artist(artist_id)
        if cached is not None:
            catalog[artist_id] = cached
    return catalog


@dataclass(frozen=True)
class LabelChange:
    """An identity label that changed on re-enrichment — the correction ledger row."""

    artist_id: str
    old: IdentityLabel
    new: IdentityLabel


@dataclass(frozen=True)
class IdentityLabelChange:
    """One cited source value/date change observed during re-enrichment."""

    artist_id: str
    source_kind: str
    old_value: str
    new_value: str
    retrieved_at: str


def _identity_sources(artist: Artist) -> tuple[Source, ...]:
    """Every citation this artist carries, on every sourced axis (#93).

    The gender axis, the band-composition axis, **and** ADR 0011's queer axis.
    That last one was missing while this function's callers already described
    themselves as covering "either identity axis": a refresh that came back with
    a freshly-sourced P91 orientation claim and nothing else scored as "nothing
    came back", so the citation was discarded, ``fetched_at`` was not advanced,
    and the operator was told the artist was unconfirmed. On the axis the
    project's own ethics note calls the highest-stakes one, silence and evidence
    were being read as the same thing — the exact conflation
    :class:`RefreshOutcome` exists to refuse.

    Deduplicated, order-preserving: the same :class:`~pipeline.models.Source`
    legitimately appears twice when a P21 claim of ``Q1052281`` is both the
    gender citation and the trans self-identification citation
    (:func:`pipeline.identity.resolve_queer_identity` reads the raw value of the
    former to produce the latter), and one document should be counted once.
    """
    sources = artist.identity.sources
    if artist.composition is not None:
        sources += artist.composition.sources
    sources += artist.queer.sources
    seen: set[Source] = set()
    unique: list[Source] = []
    for source in sources:
        if source not in seen:
            seen.add(source)
            unique.append(source)
    return tuple(unique)


def diff_identity_sources(old: Artist, new: Artist) -> list[IdentityLabelChange]:
    """Report source values or retrieval dates that changed between passes."""
    return _diff_sources(old.artist_id, _identity_sources(old), _identity_sources(new))


def _diff_sources(
    artist_id: str, old_sources: tuple[Source, ...], new_sources: tuple[Source, ...]
) -> list[IdentityLabelChange]:
    # Matched on (kind, citation) first, falling back to kind alone. Kind alone
    # stopped being a unique key once a single kind could carry claims about two
    # different questions: an ``artist-statement`` may be the citation for a
    # gender *and*, from a different document, for an orientation. Matching
    # those two against each other would manufacture a change out of two claims
    # that never disagreed. The kind-only fallback preserves the previous
    # behavior for the ordinary case where a source's citation URL is stable.
    old_by_citation = {(source.kind, source.citation): source for source in old_sources}
    old_by_kind = {source.kind: source for source in old_sources}
    changes: list[IdentityLabelChange] = []
    for new_source in new_sources:
        old_source = old_by_citation.get((new_source.kind, new_source.citation))
        if old_source is None:
            old_source = old_by_kind.get(new_source.kind)
        if old_source is None:
            continue
        if (
            old_source.detail != new_source.detail
            or old_source.retrieved_at != new_source.retrieved_at
        ):
            changes.append(
                IdentityLabelChange(
                    artist_id=artist_id,
                    source_kind=str(new_source.kind),
                    old_value=old_source.detail,
                    new_value=new_source.detail,
                    retrieved_at=new_source.retrieved_at,
                )
            )
    return changes


def diff_identity_labels(
    artist_id: str, old: IdentityLabel, new: IdentityLabel
) -> list[IdentityLabelChange]:
    """Source-level detail for a label-level cache refresh change."""
    return _diff_sources(artist_id, old.sources, new.sources)


def _upstream_sources(sources: Sequence[Source]) -> tuple[Source, ...]:
    """The subset of *sources* that came over the wire (#112).

    :meth:`~pipeline.cache.Cache.get_corrections` hands the operator's local
    corrections ledger to :func:`enrich_artist` as ordinary
    ``ARTIST_STATEMENT`` evidence, and the resolvers turn it into ordinary
    :class:`~pipeline.models.Source` rows on the resolved label. That is
    correct at resolve time — a correction should win on priority, with no
    special-casing in the resolver — and wrong at *refresh* time, where the
    only question being asked is whether the far end spoke.

    Read from the ledger, a correction is present on every run, including the
    run where every upstream lookup timed out. Counting it as a citation makes
    a dead upstream indistinguishable from an answering one on exactly the
    artists an operator cared enough to correct. The model already carries the
    discriminator (:attr:`~pipeline.models.Source.is_local_correction`); this
    is where the refresh path consults it.
    """
    return tuple(source for source in sources if not source.is_local_correction)


def _is_sourced(artist: Artist) -> bool:
    """Whether this artist carries at least one **upstream** citation on any sourced axis.

    Gender, band composition, or the queer axis. The docstring said "either
    identity axis" while the implementation looked at two of three (#93); it
    then said "citation" while the implementation also counted the local
    corrections ledger, which no upstream ever sends (#112).

    Nothing is lost by not counting a correction as proof: it lives in the
    ledger rather than in the cached row, so :func:`enrich_artist` re-applies it
    on every later enrichment and it still wins by priority.
    """
    return bool(_upstream_sources(_identity_sources(artist)))


def _preserve_unanswered_axes(cached: Artist, refreshed: Artist) -> tuple[Artist, tuple[str, ...]]:
    """Keep any sourced axis this refresh came back **silent** about.

    :class:`RefreshOutcome` protects an artist upstream said nothing about at
    all. It did not protect an artist upstream answered *partly* about, and that
    gap is reachable through an ordinary outage rather than a contrived one:
    :class:`~pipeline.enrich.MusicBrainzEnricher` reads a gender claim straight
    out of the MusicBrainz payload, while a P91 orientation claim needs a second
    fetch of the Wikidata entity, and ``_json`` renders any failure there as
    ``None``. Wikidata down while MusicBrainz is up therefore produces an artist
    carrying a fresh gender citation and nothing on the queer axis — which
    ``_is_sourced`` reads as "upstream answered", writes, and thereby erases the
    P91 citation the operator's ingest paid for. ``diff_identity_sources`` walks
    the *new* sources, so an emptied axis has nothing to report: the erasure
    would be silent, which is the exact pairing this module exists to refuse.

    So the protection is per axis. An axis that came back with *upstream*
    citations is written; an axis that came back with none keeps what the cache
    already held.

    "Empty" has to mean "empty of upstream citations" rather than literally
    empty, because the refresh path enriches with ``cache=cache``: the
    operator's corrections ledger is merged into the evidence, so a corrected
    artist's axis is never empty even when nothing at all came back over the
    wire (#112). Reading that as "the axis answered" defeated this guard with
    the very row it exists to protect alongside — and did it on precisely the
    artists someone cared enough to correct. Taking the cached axis drops the
    correction from the written row and nothing more: it lives in the ledger,
    and :func:`enrich_artist` re-applies it, still winning on priority, at the
    next enrichment.

    ``fetched_at`` still advances, and that is deliberate rather than an
    oversight: it is an artist-level claim that this artist was checked today,
    and this artist genuinely was. The finer truth stays exact because a
    preserved claim keeps its own :attr:`~pipeline.models.Source.retrieved_at`,
    which is the date that claim was last actually read.
    """
    kept: list[str] = []

    identity = refreshed.identity
    if not _upstream_sources(identity.sources) and _upstream_sources(cached.identity.sources):
        identity = cached.identity
        kept.append("identity")

    queer = refreshed.queer
    if not _upstream_sources(queer.sources) and _upstream_sources(cached.queer.sources):
        queer = cached.queer
        kept.append("queer")

    composition = refreshed.composition
    refreshed_composition_sources = composition.sources if composition is not None else ()
    cached_composition_sources = (
        cached.composition.sources if cached.composition is not None else ()
    )
    if not _upstream_sources(refreshed_composition_sources) and _upstream_sources(
        cached_composition_sources
    ):
        composition = cached.composition
        kept.append("composition")

    if not kept:
        return refreshed, ()
    return (
        replace(refreshed, identity=identity, queer=queer, composition=composition),
        tuple(kept),
    )


def _record_refresh(
    cache: Cache,
    cached: Artist,
    refreshed: Artist,
    *,
    fetched_at: str,
    changes: list[IdentityLabelChange],
    verified: list[str],
    unverified: list[str],
    protected: list[str],
) -> None:
    """Decide what one re-enriched artist means, and write it if it means anything.

    Split out of :func:`refresh_catalog` so the loop stays a loop and this stays
    the decision. The two outcomes are not symmetric and the asymmetry is the
    point — see :class:`RefreshOutcome`.
    """
    artist_id = cached.artist_id
    if _is_sourced(refreshed):
        verified.append(artist_id)
        # Upstream answered about this artist — but not necessarily about every
        # axis. Whatever it was silent about keeps what the cache already held.
        to_write, kept_axes = _preserve_unanswered_axes(cached, refreshed)
        if kept_axes:
            protected.append(artist_id)
        changes.extend(diff_identity_sources(cached, to_write))
        cache.put_artist(to_write, fetched_at=fetched_at)
        return
    # Nothing came back at all. Do not write, and do not advance the lineage
    # date: ``fetched_at`` is a claim that this artist was checked on that day,
    # and an empty answer is not evidence that anyone answered.
    unverified.append(artist_id)
    if _is_sourced(cached):
        protected.append(artist_id)


@dataclass(frozen=True)
class RefreshOutcome:
    """What a live re-enrichment actually established — and what it could not.

    The source branch needs a vocabulary the dict branch does not, because it is
    the only one that can be *lied to by silence*.
    :class:`~pipeline.enrich.MusicBrainzEnricher` deliberately renders every
    upstream failure — a timeout, a 503, a malformed payload — as "no evidence",
    which is exactly what "upstream holds no claim about this artist" also looks
    like. On the *ingest* path that conflation is correct and conservative: both
    mean the artist stays first-class ``UNKNOWN``, and nothing is lost.

    On the *refresh* path it inverts. Here a cited label already exists, and
    writing back an empty re-enrichment would delete a citation the operator's
    ingest paid for — on the strength of a signal that cannot distinguish
    "Wikidata retracted the claim" from "we never reached Wikidata". A refresh
    run against a dead network would otherwise erase every sourced identity in
    the cache and report zero changes while doing it, because
    :func:`diff_identity_sources` walks the *new* sources and an empty set has
    nothing to report.

    So this type keeps the two apart. Only an artist that came back *carrying
    sources* is written; everything else is counted, named, and reported. The
    cost is that a genuine upstream retraction is not auto-applied — it surfaces
    in ``protected`` for a human to act on via the corrections ledger, which is
    the direction this project already errs in: a label is sourced or absent,
    never inferred, and never dropped on ambiguous evidence.

    The same reasoning runs **per axis**, not only per artist: an artist can
    come back carrying a fresh gender citation and nothing on the queer axis,
    and writing that whole object would erase a P91 claim on the strength of a
    signal that cannot distinguish "Wikidata retracted it" from "we never
    reached Wikidata". See :func:`_preserve_unanswered_axes`.
    """

    attempted: int
    verified: tuple[str, ...]
    unverified: tuple[str, ...]
    #: Artists for which at least one *existing* citation was kept because
    #: upstream said nothing about it on this run. Not disjoint from
    #: :attr:`verified`: an artist upstream answered about on one axis and was
    #: silent about on another appears in both, which is the honest reading —
    #: something was re-sourced, and something was held. See
    #: :func:`_preserve_unanswered_axes`.
    protected: tuple[str, ...]
    failed: tuple[str, ...]
    changes: tuple[IdentityLabelChange, ...]

    @property
    def upstream_answered(self) -> bool:
        """Positive proof that at least one upstream document was actually read.

        Not ``attempted > 0`` and not ``failed == ()``: a run in which every
        lookup returned an empty document tried hard and failed silently. Only a
        citation coming back over the wire proves the far end spoke.
        """
        return bool(self.verified)

    def summary_line(self) -> str:
        """One honest sentence — never reports a clean pass over an unread upstream."""
        if not self.attempted:
            return "no cached artists to refresh"
        if not self.upstream_answered:
            return (
                f"upstream returned nothing for all {self.attempted} artist(s) — "
                "treating this as unreachable, not as agreement; nothing was rewritten"
            )
        bits = [f"{len(self.verified)} of {self.attempted} artist(s) re-sourced from upstream"]
        if self.protected:
            bits.append(f"{len(self.protected)} kept a citation upstream did not answer for")
        if self.unverified:
            bits.append(f"{len(self.unverified)} unconfirmed")
        if self.failed:
            bits.append(f"{len(self.failed)} errored")
        return "; ".join(bits)


@overload
def refresh_catalog(
    cache: Cache, catalog_or_source: dict[str, Artist], *, fetched_at: str
) -> list[LabelChange]: ...


@overload
def refresh_catalog(
    cache: Cache,
    catalog_or_source: ScrobbleSource,
    enricher: EnrichmentSource,
    *,
    fetched_at: str,
    artist_ids: Optional[Sequence[str]] = ...,
) -> RefreshOutcome: ...


def refresh_catalog(
    cache: Cache,
    catalog_or_source: dict[str, Artist] | ScrobbleSource,
    enricher: Optional[EnrichmentSource] = None,
    *,
    fetched_at: str,
    artist_ids: Optional[Sequence[str]] = None,
) -> list[LabelChange] | RefreshOutcome:
    """Re-persist, or re-enrich from upstream, a cached catalog — reporting changes.

    Passing a dict compares and writes already-enriched objects; it performs no
    network fetch, and returns the label-level changes. Passing both a
    ``ScrobbleSource`` and an ``EnrichmentSource`` re-fetches every cached artist
    (or just ``artist_ids``, to bound a run against a rate-limited upstream) and
    returns a :class:`RefreshOutcome` — see there for why the source branch
    cannot report a bare list of changes honestly.

    A lookup that raises costs that artist, not the run: a refresh over a
    real listening history walks thousands of artists at ~1 req/s, and one 503
    nine minutes in must not discard the nine minutes.
    """
    if isinstance(catalog_or_source, dict):
        return _refresh_from_catalog(cache, catalog_or_source, fetched_at=fetched_at)
    if enricher is None:
        raise TypeError("source refresh requires an EnrichmentSource")
    return _refresh_from_upstream(
        cache,
        catalog_or_source,
        enricher,
        fetched_at=fetched_at,
        artist_ids=artist_ids,
    )


def _refresh_from_catalog(
    cache: Cache, catalog: dict[str, Artist], *, fetched_at: str
) -> list[LabelChange]:
    """The offline branch: re-persist already-enriched objects, reporting changes."""
    label_changes: list[LabelChange] = []
    for artist_id, artist in catalog.items():
        cached = cache.get_artist(artist_id)
        if cached is not None and cached.identity != artist.identity:
            label_changes.append(LabelChange(artist_id, cached.identity, artist.identity))
        cache.put_artist(artist, fetched_at=fetched_at)
    return label_changes


def _read_cached_artist(cache: Cache, artist_id: str) -> Artist | Exception | None:
    """The cached row, ``None`` if absent, or the exception that reading it raised.

    Split out so the read is inside the per-artist failure boundary. It used to
    sit outside it, which made :func:`refresh_catalog`'s promise that "a lookup
    that raises costs that artist, not the run" true of the upstream fetch and
    false of the cache read one line above it: a single row whose stored JSON is
    truncated, or carries an enum value this build does not know, aborted a walk
    over thousands of artists that had already paid for their rate-limited
    fetches.
    """
    try:
        return cache.get_artist(artist_id)
    except Exception as exc:  # any read failure costs one artist, never the run
        log.exception("stage=refresh event=unreadable_cache_row artist_id=%s", artist_id)
        return exc


def _refresh_from_upstream(
    cache: Cache,
    source: ScrobbleSource,
    enricher: EnrichmentSource,
    *,
    fetched_at: str,
    artist_ids: Optional[Sequence[str]] = None,
) -> RefreshOutcome:
    """The live branch: re-ask upstream, and never read silence as agreement."""
    targets = list(artist_ids) if artist_ids is not None else cache.list_artist_ids()
    changes: list[IdentityLabelChange] = []
    verified: list[str] = []
    unverified: list[str] = []
    protected: list[str] = []
    failed: list[str] = []
    attempted = 0
    for artist_id in targets:
        cached = _read_cached_artist(cache, artist_id)
        if isinstance(cached, Exception):
            failed.append(artist_id)
            continue
        if cached is None:
            continue
        attempted += 1
        try:
            refreshed = enrich_artist(
                artist_id,
                cached.name,
                source,
                enricher,
                listeners=cached.listeners,
                playcount=cached.playcount,
                cache=cache,
            )
        except Exception:
            log.exception(
                "stage=refresh event=failed artist_id=%s enricher=%s",
                artist_id,
                type(enricher).__name__,
            )
            failed.append(artist_id)
            continue
        _record_refresh(
            cache,
            cached,
            refreshed,
            fetched_at=fetched_at,
            changes=changes,
            verified=verified,
            unverified=unverified,
            protected=protected,
        )
    outcome = RefreshOutcome(
        attempted=attempted,
        verified=tuple(verified),
        unverified=tuple(unverified),
        protected=tuple(protected),
        failed=tuple(failed),
        changes=tuple(changes),
    )
    log.info(
        "stage=refresh event=end attempted=%d verified=%d unverified=%d protected=%d failed=%d",
        outcome.attempted,
        len(outcome.verified),
        len(outcome.unverified),
        len(outcome.protected),
        len(outcome.failed),
    )
    return outcome

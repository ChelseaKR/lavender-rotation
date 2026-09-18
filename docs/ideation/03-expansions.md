# Expansions (2026-07-01)

Last verified: 2026-07-12

## Current disposition

- **Implemented:** EXP-01 through EXP-04, EXP-06, EXP-10, and EXP-11.
- **Partially implemented:** EXP-05 ships edit links and a local pending-correction
  queue, but `wad refresh` only replays fixtures. A real correction round-trip is
  still blocked on the deferred live enricher (FIX-01).
- **Deferred pending provider/live validation:** EXP-07. EXP-08 was reviewed
  and rejected in its current form: ListenBrainz populated playlists require
  MusicBrainz recording MBIDs, while this system recommends artists; claiming
  all artist-name entries were matched would be false.
- **Cross-repository/human-gated:** EXP-09 requires authority and coordinated
  changes in another repository. EXP-13 requires affected-community input on
  consent and revocation before a self-ID convention can be designed.
- **Not accepted into the committed product scope:** EXP-12 and EXP-14 add
  location or multi-person listening data and materially reopen the privacy
  model. They remain ideas, not active roadmap work.

The original horizon descriptions follow for historical context.

Net-new expansion ideas in three horizons. Existing roadmap items are cited,
never restated: ROADMAP already owns *ListenBrainz collaborative signal*,
*thumbs feedback*, *acoustic/content features*, *a discovery report*, and
*additional sourced value lenses (local/indie, BIPOC)* — ideas below that touch
those name them and describe only the beyond-part.
Effort tiers: S (≤1 day), M (2–5 days), L (1–3 weeks), XL (3+ weeks).

---

## Horizon 1 — Deepen the core

### EXP-01 — Fairness observability panel
**Pitch:** Put the exposure metrics (FIX-05) in the user's face: a dashboard
section showing exposure share by identity basis and the unknown-retention
curve as the lens slider moves.
**Impact:** Makes the fairness guarantee *inspectable by the user in real
time* — the strongest possible transparency statement, and a distinctive demo.
**Shape:** New section in `app/dashboard.py` + `app/render.py` (table-first
per the a11y posture: the chart is the equivalent, the table is primary);
data from the FIX-05 exposure module; goes beyond ROADMAP's "Could: discovery
report" by being live and lens-reactive rather than a periodic artifact.
**Effort:** M. **Risks/deps:** FIX-05; a11y gate must stay at 0.
**Excellence bar:** Moving the lens slider visibly changes exposure shares
while the unknown-retention row stays pinned at 100% — screenshot-able proof
of the core claim.

### EXP-02 — Rank-shift transparency in every why-card
**Pitch:** Each card states what the lens actually did: "the values lens moved
this pick from #9 to #4" (or "the lens did not change this pick's position").
**Impact:** Converts the abstract boost-only guarantee into a per-item,
falsifiable statement; deepens §D transparency beyond signals+sources.
**Shape:** `recommender/hybrid.py` already computes both orderings implicitly
(`lens_strength=0` vs applied); compute the counterfactual rank in
`recommend()` and thread it through `Recommendation`/`WhyThisArtist`
(`recommender/why.py`) into all four surfaces.
**Effort:** S–M. **Risks/deps:** Snapshot-test updates; wording review.
**Excellence bar:** 100% of cards carry a rank-shift statement; a test asserts
no unknown-identity card ever shows a negative shift attributable to the lens.

### EXP-03 — First-class lens specification (LensSpec)
**Pitch:** Make the values lens a declared, inspectable object — which sourced
attributes count as aligned, what the boost curve is — instead of constants
scattered in code.
**Impact:** Today `VALUES_ALIGNED_GENDERS` (`pipeline/models.py:59`) excludes
`Gender.OTHER`, so a sourced intersex/third-gender artist gets no boost — a
values decision no document currently states. A LensSpec forces every such
choice to be explicit, rendered in the UI, and testable. It is also the
enabling substrate for ROADMAP's "Could: additional sourced value lenses"
(local/indie, BIPOC) — this idea is the *mechanism*, which the roadmap does
not specify: a lens manifest with name, rationale, aligned-predicate over
sourced fields only, boost bound, and a written harms note.
**Shape:** `LensSpec` dataclass in `recommender/rerank.py` (predicate over
`Artist`'s **sourced** fields only — enforced by a no-inference-style test);
`MAX_BOOST` and the aligned set move into it; the dashboard shows the active
lens's manifest text; the `OTHER` question gets decided and documented either
way.
**Effort:** M. **Risks/deps:** Each new lens needs its own sourcing story
(identity-data-ethics review is the gate, not code).
**Excellence bar:** Zero identity-related constants outside LensSpec; the UI
can answer "what exactly does this lens boost, and why?" without reading code.

### EXP-04 — Serendipity control with provably identity-blind diversification
**Status (2026-09-06):** Implemented, with the excellence bar's *second* clause
recorded as an aspiration rather than a met bar. Identity-blindness is proved —
`tests/test_diversify.py` holds the pass to permutation-not-rescore and asserts
by AST that it never reads `identity`/`composition`. Exposure neutrality is
**not** met: measured at k=3 on the demo world, moving the slider from 0.0 to
1.0 changes which identity segment holds a top-3 slot. Blindness at the input is
not neutrality at the output, and nothing enforced the second clause. See #114;
`tests/test_observability.py::test_the_diversifier_does_move_top_k_exposure_on_this_world`
pins the measurement so the claim cannot be quietly restored.
**Pitch:** An "explore ↔ exploit" control that diversifies results (MMR-style
over tag space) while a test proves diversification never reads identity.
**Impact:** Single-user discovery tools die of staleness; the collaborative
graph (`recommender/collaborative.py`) converges on near-neighbors. This adds
freshness without touching the fairness contract.
**Shape:** A post-rerank diversification pass over movable candidates, keyed only
on `Artist.tags` and similarity; the orchestrator reconstructs the result around
protected unknown slots before top-k selection. An AST/behavioral guard in the spirit of
`tests/test_no_inference.py` asserting the diversifier never accesses
`identity`/`composition`; surfaced as a second explained slider.
**Effort:** M. **Risks/deps:** Re-ordering interacts with rank-shift wording
(EXP-02) — signals must attribute movement to the right cause.
**Excellence bar:** Diversity metric (intra-list tag distance) demonstrably
rises with the slider; identity-segment exposure (FIX-05) statistically
unchanged by the diversifier at any setting.

### EXP-05 — "Fix it at the source" contribution flow
**Status:** Partial — edit links and the queue are implemented; upstream
reconciliation is not, and the excellence-bar real round-trip remains open.
**Pitch:** When a label is missing, stale, or wrong, the UI offers a
pre-filled path to correct it upstream (Wikidata P21 edit page, MusicBrainz
edit, with the citation the user supplies), and logs the pending correction
locally.
**Impact:** Makes the promised correctability (RR-2) real *and* gives back to
the commons the project draws from — the anti-scraping ethos in active form.
**Shape:** Deep links from provenance items in `app/render.py`/dashboard;
a local "pending corrections" list (pairs with FIX-10's ledger) that
`wad refresh` reconciles when the upstream edit lands. No new egress — the
user's browser does the editing.
**Effort:** S–M. **Risks/deps:** FIX-10; must never auto-edit upstream.
**Excellence bar:** One real correction round-trip completed and documented
(local note → Wikidata edit → refresh picks it up with new `retrieved_at`).

### EXP-06 — Temporal taste profiles
**Pitch:** Recommend against a chosen era of your listening ("my 2019 self"),
with optional recency-decay weighting for the default profile.
**Impact:** Full-history ingest (FIX-02) makes decade-scale scrobble data
available; a flat play-count profile (`pipeline/ingest.py::build_profile`)
misrepresents current taste and buries eras. Cheap, delightful, analytically
honest.
**Shape:** Time-window and half-life parameters on `build_profile`; a
window/decay control in the dashboard with plain-language explanation; eval
extension: does decay improve held-out recovery (FIX-06 harness)?
**Effort:** M. **Risks/deps:** FIX-02.
**Excellence bar:** Era-profiles are reproducible (seeded snapshot tests) and
the eval reports whether decay helps, with numbers.

---

## Horizon 2 — Adjacent capabilities, audiences, integrations

### EXP-07 — ListenBrainz as a full alternate ingest backend
**Pitch:** A complete `ScrobbleSource` implementation for ListenBrainz — not
just the similar-artist signal ROADMAP lists as *Should*.
**Impact:** Removes the Last.fm single-point dependency (proprietary API,
key-gated) and aligns the ingestion layer with an open-data commons; opens the
tool to ListenBrainz-first users.
**Shape:** `pipeline/listenbrainz.py` implementing the `ScrobbleSource`
protocol (`pipeline/lastfm.py:24` — the protocol already makes this a
drop-in), same cache/rate-limit pattern; MBID-native, which strengthens FIX-03.
**Effort:** M–L. **Risks/deps:** FIX-01 conventions; tag signal is weaker on
ListenBrainz — content scoring may need MusicBrainz tags/genres instead.
**Excellence bar:** `wad recommend --source listenbrainz` at parity; provider
choice documented; privacy-notes updated with the (equivalent) egress row.

### EXP-08 — Multi-provider playlist export (ListenBrainz playlists first)
**Pitch:** Export beyond Spotify: submit JSPF playlists to ListenBrainz
natively, and add a second commercial provider via the existing transport
pattern.
**Impact:** `export/tracklist.py::to_jspf` already emits ListenBrainz's native
playlist format — the distance to a real integration is unusually short; and a
second provider proves `export/` is genuinely provider-agnostic
(`export/models.py` was designed for it).
**Shape:** `export/listenbrainz.py` (token auth, one endpoint) mirroring
`export/spotify.py`'s injectable `HttpTransport`; provider registry in the
dashboard export panel; each provider adds one row to the privacy-notes egress
table.
**Effort:** M. **Risks/deps:** Each provider = new opt-in egress + review-gate
on privacy notes; FIX-08 patterns should come first so new auth is built right.
**Excellence bar:** Two live providers plus four credential-free formats, all
behind one `PlaylistExport` result type; egress table exhaustive.

### EXP-09 — Extract `values-lens-core`: the shared pattern library
**Pitch:** Lift the sourced-identity model, boost-only rerank, and why-card
into a small reusable package shared with queer-the-stacks.
**Impact:** The two repos independently maintain the same hard pattern
("recommendation with an explicit values lens"). One provable implementation
— `IdentityLabel`-style sourced attributes, `LensSpec` (EXP-03), boost-only
rerank with the invariant tests, `WhyThisArtist` — raises both repos and makes
the portfolio's central idea citable as a single artifact.
**Effort:** L. **Risks/deps:** Cross-repo coordination; versioning discipline
(Release standard); risk of premature abstraction — do it *after* EXP-03
stabilizes the lens model here, and after comparing queer-the-stacks' actual
shapes (not inspected in this pass — uncertainty acknowledged).
**Excellence bar:** Both repos consume the package; the no-inference and
unknown-first-class test patterns ship *in the library* so every future
adopter inherits the guardrails by default.

### EXP-10 — The methods writeup as a reproducible artifact
**Status:** ✅ Implemented — `docs/writeup/methods.md` ties the threat model,
type invariants, fairness evidence, and evaluation limits to reproducible
artifacts; `scripts/writeup-check.py` prevents its reported metrics drifting.
**Pitch:** Write the engineering-ethics piece ROADMAP §9 calls "the artifact"
— "values-aware recommendation without inferring identity" — as a
docs-plus-runnable-demo bundle.
**Impact:** ROADMAP §9 says the writeup is the point; it does
not exist. With FIX-05's metrics and the type-level guardrails, this repo can
back every claim with a command the reader can run offline (`make dev`, demo
mode, no key).
**Shape:** `docs/writeup/` (or a blog-ready md): the threat framing (inference
harms), the type-invariant approach (`pipeline/models.py` excerpts), the
boost-only proof, exposure numbers, honest limits (source sparsity, RR-2).
Not user-facing app code; review-gated for framing.
**Effort:** M. **Risks/deps:** Best after FIX-05/FIX-06 so numbers are real.
**Excellence bar:** Every quantitative claim in the writeup is regenerated by
`make audit`; an outside reader can verify the no-inference claim in under
five minutes.

### EXP-11 — Shareable static discovery report
**Pitch:** One command produces a self-contained, accessible HTML file of your
current picks (why-cards, provenance, exposure summary) you can keep or send —
no server, no account.
**Impact:** `app/render.py` + `app/build_static.py` are 90% of this already;
it turns the a11y artifact into a user feature and gives the tool a shareable
output with zero hosting or egress.
**Shape:** `wad report --out my-discoveries.html` in `pipeline/cli.py`,
reusing `render_cards_html` plus the EXP-01 exposure table; inline styles
already present; date-stamped.
**Effort:** S. **Risks/deps:** None hard; mind that a *shared* file discloses
your taste — add a one-line privacy note in the file footer.
**Excellence bar:** Output passes the same axe gate as the CI artifact; a
recipient with no context understands why each artist appears and on what
identity basis.

### EXP-12 — Live-show lens (opt-in local events)
**Pitch:** Cross your recommendations with upcoming concerts near a location
you type in — surfacing values-aligned artists you could actually go see.
**Impact:** Moves discovery from listening to action; a genuinely different
job-to-be-done for the same engine and lens.
**Shape:** A new opt-in adapter (Bandsintown/Songkick-class API) in its own
package parallel to `export/` — same isolation rationale, new egress row in
privacy-notes (location + artist names is sensitive: location must be
user-typed per query, never stored); results rendered as a separate,
explained section.
**Effort:** L. **Risks/deps:** API terms/keys; the location-privacy note is a
review-gate; keep out of core coverage scope like other live paths.
**Excellence bar:** Zero location persistence (test-asserted, FIX-07 guard);
the feature is entirely absent from the UI until explicitly enabled.

---

## Horizon 3 — Transformative bets

### EXP-13 — A self-sourced artist-identity commons (spec + tooling)
**Pitch:** Define a tiny open convention by which artists *themselves* publish
machine-readable self-identification (e.g. a well-known JSON at their domain
or a linked statement via their MusicBrainz/Wikidata entities), and make this
resolver its first consumer.
**Impact:** The project's deepest constraint — identity data may only come
from cited self-identification — currently depends on sparse third-party
databases (Wikidata P21 "sparse and sometimes wrong", per
`docs/audits/identity-data-ethics.md`). A self-sovereign publication channel
attacks the root cause: it grows *first-party* coverage without anyone
scraping or guessing, and `SourceKind.ARTIST_STATEMENT` (already the
highest-priority source in `pipeline/identity.py::_SOURCE_PRIORITY`) is the
ready-made slot for it.
**Shape:** A short spec (`self-id.json`: statement, scope, date, revocation);
a fetcher honoring robots/consent semantics; verification = the statement
lives at an artist-controlled origin; revocation honored on refresh
(FIX-04 TTLs). Publish the spec for comment before building consumers beyond
this repo. Explicitly *not* a database: the repo still never redistributes.
**Effort:** XL. **Risks/deps:** Adoption is the whole risk; spec design needs
outside voices (human gate); interacts with trans-safety concerns (revocation
and "scope of consent" must be first-class, not bolted on).
**Excellence bar:** Spec published with an honest threat analysis; ≥1 real
artist statement resolved end-to-end; revocation demonstrated (label returns
to unknown on refresh after takedown).

### EXP-14 — Household mode: local multi-profile blending
**Pitch:** Blend 2–5 local profiles (family/housemates) into shared
recommendations — computed entirely on one machine, no server, no accounts.
**Impact:** ROADMAP lists cross-user/social as *Won't (v1)*; this is the
post-v1 revisit in its most privacy-conservative form: multiple usernames,
one local cache, group-fairness questions (whose taste dominates?) that this
codebase is unusually well equipped to answer honestly via FIX-05-style
exposure reporting per member.
**Shape:** Multi-profile support in `pipeline/ingest.py`/cache (username is
already a key throughout); blending strategies (least-misery / proportional)
as explained, selectable objects like EXP-03's LensSpec; per-member
contribution shown on every card (extending `recommender/why.py`).
**Effort:** L–XL. **Risks/deps:** Re-opens RR-3 (still no hosting, so only
partially) and the privacy review (one member can infer another's listening —
requires explicit in-UI consent framing); after FIX-02/03.
**Excellence bar:** Every group rec explains each member's contribution; a
member can be removed and all derived data provably drops (`wad forget
<user>`); the Won't-item revision is recorded as an ADR, not silently done.

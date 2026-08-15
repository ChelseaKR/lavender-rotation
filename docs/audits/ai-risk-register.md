# AI Risk Register

> Instantiates RTF-09 (a single, findable risk register — AI-EVALUATION-STANDARD.md:194 expects
> this repo specifically to own its no-inference risk entry here) plus RTF-10 (AI-system impact
> assessment) and RTF-12 (EU AI Act classification). Consolidates risks already tracked distributed
> across `docs/audits/identity-data-ethics.md`, `docs/audits/fairness-identity.md`, and
> `docs/audits/residual-risk.md` — those files remain authoritative for detail; this is the index
> with owners and mitigation→test links in one place.
> **Last verified: 2026-07-05 · Recheck cadence: per identity-model or recommender change.**

## Risk register

| ID | Risk | Mitigation | Verified by | Owner |
|----|------|------------|-------------|-------|
| AIR-1 | Misgendering an artist by inferring identity from name/voice/image/genre | Identity is sourced-only, cited, and unconstructible otherwise (construction-time type invariant) | `tests/test_no_inference.py` — AST scan over **every** function (incl. `async def`) in **every** `pipeline/*.py`, with named+justified exemptions held to a stricter taint check, a proof the scan *fails* on a genre-inference bypass, and an assertion that `recommender`/`app`/`export` construct no identity objects (#72); plus behavioural proofs | maintainer |
| AIR-2 | An unsourced ("unknown") artist is quietly buried by the values lens or post-rank exploration | Re-rank is boost-only; MMR sees only movable candidates; unknown's score/rank is invariant through final top-k | `tests/test_unknown_first_class.py`, `tests/test_exposure.py::test_unknown_slots_survive_end_to_end_exploration`, `recommender/exposure.py` unknown-retention guarantee | maintainer |
| AIR-3 | Nonbinary identity collapsed into a binary gender model | Nonbinary is a first-class `Gender` member, boosted identically to women when sourced | `tests/test_identity_model.py::test_nonbinary_survives_end_to_end_in_recommendations` | maintainer |
| AIR-4 | "Female-fronted" conflated with an individual member's gender | Band composition is a separate, tri-state, sourced property, never inferred from a person's label | `tests/test_identity_model.py::test_female_fronted_is_distinct_from_member_gender` | maintainer |
| AIR-5 | Allocational bias: the lens over-favours already-popular sourced women within the boosted set | Bounded boost (`MAX_BOOST`); base taste score never includes popularity as an input | `docs/audits/fairness-identity.md` §3; `recommender/exposure.py` popularity×identity cross-tab | maintainer |
| AIR-6 | Building or redistributing a scraped musician-identity dataset | No bulk export path exists; identity is resolved on-demand and cached locally only | `tests/test_privacy.py` (egress confinement); `docs/audits/identity-data-ethics.md` "Non-redistribution" | maintainer |
| AIR-7 | Listening-data privacy: a person's Last.fm history leaking beyond their own machine | Local-first; no telemetry; the only opt-in egress is a user-initiated Spotify export (artist names only) | `tests/test_privacy.py`; `docs/audits/privacy-notes.md` | maintainer |
| AIR-8 | Cached identity claim goes stale or is later corrected upstream, but the app keeps serving the old label | **Open residual risk:** TTL/diff primitives exist, but `wad refresh` is fixture-only and no live enricher calls upstream. Citations and fetch dates surface staleness; FIX-01 must close correction fold-back. | `pipeline/ingest.py::refresh_catalog`, `pipeline/cache.py` TTL/expiry, `docs/ideation/02-large-scale-fixes.md` FIX-01/04 | maintainer |
| AIR-9 | Recommender quality regresses silently release-over-release | Eval gate checks both beats-popularity and regression-vs-committed-baseline | `recommender/eval.py::check_regression`, `docs/audits/eval-baseline.json` | maintainer |

Dependency/security risks (vulnerable packages, secret leakage, supply-chain) are tracked
separately in `docs/audits/residual-risk.md` — not duplicated here, since that register already
has its own STRIDE-lite threat model and closure trail.

## AI-system impact assessment (RTF-10 / ISO 42001 §6.1.4)

Most impact-assessment frameworks center the *user* (the listener, in this case a single person
running their own tool). The more distinctive population here is **the artists the system makes
identity-related decisions about**, most of whom never interact with this software and did not
consent to being included in its candidate pool:

- **Who is affected.** Any artist enriched from MusicBrainz/Wikidata/Discogs during a user's
  session — a much larger and less-controllable population than "users of the app."
- **What decision is made about them.** Whether they carry a sourced gender/composition label at
  all, and (if the values lens is on) a bounded, positive-only boost. No artist's *score* is ever
  reduced, for any identity or its absence (`assert_no_score_reduced`, checked on emitted output).
  *List position* is not equally protected, and saying so is the point: unknown-identity artists
  and artists sourced as `Gender.OTHER` hold their pure-taste position
  (`assert_unknown_retained` / `assert_other_retained`); a sourced man's position can move down,
  because a boosted artist that rises has to pass someone. That single re-allocation is the lens's
  value judgement and is stated in `VALUES_LENS.harms_note`. This bullet previously read "No
  artist is ever penalized, excluded, or down-ranked because of an identity label or its absence",
  which was not true of position (#68).
- **Worst-case harm if the mitigations failed.** Misgendering a real person publicly (reputational
  and dignity harm) or building a queryable "gender of musicians" database that could be reused
  for purposes this project never intended (the "worst misuse" scenario named in
  `docs/RESPONSIBLE-TECH-AUDITS.md` §A). Both are structurally prevented today (AIR-1, AIR-6), not
  merely policy — but "structurally prevented" is a claim this file's linked tests continuously
  re-verify, not a one-time assertion.
- **Redress.** An artist (or anyone on their behalf) can report a wrong or outdated identity claim
  via the same channel as a security report (`SECURITY.md`) and record a cited local correction.
  Automated upstream fold-back is not shipped: `refresh_catalog` has an injectable integration
  seam, while the CLI is fixture-only pending FIX-01.
- **Asymmetry of power.** The maintainer controls the resolver and re-rank; affected artists have
  no direct visibility into or control over this specific tool's output about them (though they
  retain full control over the upstream sources — Wikidata, MusicBrainz — this tool reads from).
  This asymmetry is why "sourced, cited, correctable, never redistributed" is treated as a
  security-severity invariant in `SECURITY.md`, not just a quality one.

## EU AI Act classification (RTF-12)

**Likely out of scope / minimal risk**, for two independent reasons, and this conclusion is
recorded here explicitly rather than left implicit (the framework's requirement, per RTF-13's
note that "not Annex III high-risk" must still be *written down*):

1. **Personal, non-professional use.** The EU AI Act's scope centers on providers and deployers
   acting in a professional/commercial capacity (Art. 2); this is a personal open-source project
   with no commercial deployment, no employer/client affiliation (see `CONTRIBUTING.md`), and a
   single-user operating model.
2. **Not an Annex III high-risk category.** The system does not touch employment, credit, law
   enforcement, education/exam-scoring, migration, essential-services access, or biometric
   categorization — the Annex III list. A music recommender with a values-aware lens does not fit
   any enumerated high-risk use case.
3. **Article 50 transparency obligations** (AI-generated/manipulated content disclosure) are not
   triggered — this system does not generate synthetic media, deepfakes, or emotion-recognition/
   biometric-categorization output; its "AI" surface is a classical ranking algorithm with fully
   inspectable, cited provenance, not a generative or profiling system in the Act's sense.

This is a reasoned, dated conclusion, not an assumption — it should be revisited if the project
ever moves toward commercial deployment, a multi-tenant/hosted mode, or any Annex III-adjacent use
case (none are planned; see `docs/ROADMAP.md` §3 non-goals).

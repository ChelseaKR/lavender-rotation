# Research-Backed Roadmap — Lavender Rotation

Last verified: 2026-07-11

> **What this is.** A research-grounded, persona-triaged backlog that **complements**
> [`docs/ROADMAP.md`](./ROADMAP.md) — it does not replace it. The implementation
> roadmap owns the build plan (M0–M6), the MoSCoW scope, and the quality gates; *this*
> document takes the documented gender gap in music, the literature on recommender
> bias and gender-inference harm, and the synthetic persona panel in
> [`USER-RESEARCH.md`](./USER-RESEARCH.md), and turns them into a sequenced list of
> **remediations** (close gaps in what's shipped) and **expansions** (new capability).
>
> Every item is tagged **[corroborates …]** where it independently re-derives an
> existing roadmap/audit commitment (triangulation is signal, not noise) or
> **[NET-NEW]** where the panel/research surfaced it. No GitHub issue numbers are
> invented; items anchor to *real* artifacts already in the repo — MoSCoW scope
> (`docs/ROADMAP.md §3`), residual risks (RR-1/2/3), milestones (M0–M6), the metric
> gates (`§7`), and the audit docs in `docs/audits/`.

> [!IMPORTANT]
> **Two product constraints are invariant and recur in every item below. Nothing in
> this roadmap may weaken them:**
> 1. **Identity is sourced, never inferred** — only Wikidata P21, the MusicBrainz
>    gender field, or a cited artist self-statement; never name/voice/image/genre.
> 2. **"Unknown" is first-class** — it is the *common* case, it never down-ranks,
>    drops, or penalizes a recommendation, and "female-fronted" is sourced band
>    composition kept distinct from any individual's gender.
> Items that touch identity are marked **⚖︎** as a reminder that they ship *only* if
> they preserve both.

## Current disposition

The June 2026 panel remains a hypothesis-generating snapshot. The engineering
queue was reconciled on 2026-07-11; this table is the current source of truth so
the historical backlog below is not mistaken for open committed work.

| IDs | Disposition |
| --- | --- |
| R1, R4, R5, R6, R9, R10, R11 | Implemented: identity coverage; exposure/retention/rank-shift metrics; explicit popularity-independent wording; artist-statement priority; exact source/runtime egress guards plus identity-free export schemas; trans/intersex end-to-end tests; no-inference documentation and tests. |
| R2 | Partially implemented by the cited local corrections ledger, pending-upstream queue, and edit links. The CLI refresh is fixture-only, so upstream reconciliation and a public artist opt-out/intake route are not shipped. |
| R3, R8 | Human-gated: automated axe checks pass in three color schemes, but a real VoiceOver/NVDA walkthrough and dynamic-announcement judgment cannot be fabricated in code. |
| R7 | Superseded by per-card counterfactual rank-shift wording and the lens-reactive fairness panel, which show what the lens changed without duplicating the whole result set. |
| R12 | Accepted operational follow-up rather than a product feature: dated audit docs carry recheck cadences; upstream changes are reviewed when dependency/API updates land. |
| E1, E3, E4, E7, E8, E10 | Implemented: bounded per-artist feedback; cited artist-statement source; static discovery report; LensSpec extension point and guard tests; respectful unknown explainer; reproducible methods writeup. |
| E9 | Partial: the correction queue and diff/reconciliation helpers exist, but a real re-enrich step is blocked on the deferred live enricher (FIX-01). |
| E2 | Rejected: exporting identity/provenance would conflict with data minimization and the promise not to create a portable musician-identity dataset. Portable exports intentionally contain artist names and non-identity recommendation reasons only. |
| E5 | Human/ethics-gated. New identity-related lenses need affected-community review and a defensible sourced vocabulary before code. No placeholder BIPOC or similar classifier will be invented. |
| E6 | Deferred, not represented as shipped. A ListenBrainz adapter needs a separate provider-contract, privacy, and live-data validation pass. |

---

## 1. Framing — how this complements the implementation roadmap

`docs/ROADMAP.md` answers *"what to build and how to prove it's correct."* It already
encodes the hard guarantees (no-inference test, unknown-never-penalised, provenance,
beats-popularity eval, axe=0) and the MoSCoW scope. What it does **not** carry is the
*external evidence* that justifies the product's premise and the *user-/artist-facing*
gaps between "true in code" and "visible in the product." This roadmap fills exactly
that seam:

- It supplies the **cited evidence base** (the gender gap; recommender amplification;
  inference harm; source sparsity) that the implementation roadmap references only as
  "Research & evidence (§4)."
- It promotes the panel's recurring finding — *the guarantees are mechanical but
  invisible* — into concrete, mostly-cheap work.
- It stays inside the existing scope: most P0/P1 items are **remediations on shipped
  M0–M6 code** or realizations of MoSCoW "Should/Could" items, not new pillars.

---

## 2. Research basis / evidence

High-stakes claims (the gender-gap statistics; the recommender-bias finding) are
cross-checked against ≥2 reputable sources, per the project's evidence discipline.
All URLs accessed **2026-06-30**.

| Key | Finding the product relies on | Sources (cross-checked) |
| --- | --- | --- |
| **[USC]** | Women are a persistent minority of charting music makers: ~22% of Billboard Hot 100 artists across 2012–2023 (a 12-year high of ~35% in 2023, of which ~0.6% non-binary); women songwriters historically ~13%; women producers in the low single digits at **~30 men : 1 woman** across the years studied, with ~94% of songs crediting **zero** women producers. The 2026 edition reports gains *reversing*. | USC Annenberg *Inclusion in the Recording Studio?* [PDF](https://assets.uscannenberg.org/docs/aii-inclusion-recording-studio-2025-01-29-2.pdf) · [Billboard](https://www.billboard.com/business/business-news/usc-annenberg-study-gender-equality-music-industry-1235591929/) · [Annenberg news](https://annenberg.usc.edu/news/research-and-impact/annenberg-inclusion-initiatives-annual-report-popular-music-reveals-little) · [Music Ally 2026](https://musically.com/2026/03/16/usc-annenberg-inclusion-report-womens-place-in-this-business-is-shrinking/) |
| **[Ferraro]** | Collaborative-filtering recommenders **amplify** the gap: on data ~25% women, the first recommended woman lands around **rank 6–7** vs rank 1 for men, and a **feedback loop** worsens underexposure over time; a gradual-exposure re-rank can break it. (Motivates the bounded, boost-only re-rank.) | Ferraro, Serra & Bauer, *Break the Loop* (ACM RecSys 2021), reported via [TechXplore](https://techxplore.com/news/2021-04-gender-bias-music-algorithms.html) · [Utrecht University](https://www.uu.nl/en/news/music-recommendation-algorithms-are-unfair-to-female-artists-but-we-can-change-that) |
| **[MRS-fair]** | Recommender fairness is **multi-stakeholder** (listeners *and* artists), and ranking/choice-model design materially changes gender exposure — fairness must be *measured*, not assumed. | [Fairness in MRS: stakeholder mini-review, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC9353048/) · [*It's Not You, It's Me*, arXiv 2409.03781](https://arxiv.org/html/2409.03781v1) |
| **[FACTS]** | The gap extends to live music: women ~29.8% of electronic-festival bookings in 2022–23 (up from 9.2% in 2012), **non-binary ~2.5%**; UK analysis found ~63% of acts are all-male; larger festivals are *less* balanced, and festivals with female artistic directors are more balanced. | female:pressure FACTS, via [CDM](https://cdm.link/facts-report-women-nonbinary-artists-still-face-inequality-at-festivals/) · [Mixmag](https://mixmag.net/read/63-of-uk-festival-acts-are-male-artists-or-all-male-groups-shares-new-data-news) · [Book More Women](https://www.bookmorewomen.com/data) |
| **[Wikidata-P21]** ⚖︎ | Identity data is **sparse** (≈46% of human Wikidata items carry P21 at all), so "unknown" is the *common* case — and the Wikidata community **explicitly opposes** batch-adding sex/gender from given names, validating sourced-never-inferred. | [*Quantifying the Gap*, ACM](https://dl.acm.org/doi/fullHtml/10.1145/3479986.3479992) · [Wikidata Property talk:P21](https://www.wikidata.org/wiki/Property_talk:P21) |
| **[misgender]** ⚖︎ | Name-/feature-based gender inference **disproportionately misclassifies** women and LGBTQ+/non-binary people; gender cannot be read from observable features, and misgendering carries real harm. The case *against* an inference path. | [Misgendering-algorithms survey, ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0160791X25003008) · [Beyond Binary Gender Labels (LLMs), arXiv 2407.05271](https://arxiv.org/html/2407.05271v1) · [AGR misgendering, arXiv 2506.02017](https://arxiv.org/pdf/2506.02017) |
| **[Spotify-API]** | Spotify **deprecated** Related Artists, Recommendations, Audio Features, and Audio Analysis for new apps on **2024-11-27** — so similarity/content signal must come from elsewhere; the project's lean on Last.fm/ListenBrainz/MusicBrainz is a resilience choice. | [Spotify dev blog](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) · [TechCrunch](https://techcrunch.com/2024/11/27/spotify-cuts-developer-access-to-several-of-its-recommendation-features/) · [Music Ally](https://musically.com/2024/11/28/spotify-removes-features-from-web-api-citing-security-issues/) |
| **[MB-gender]** ⚖︎ | The MusicBrainz gender field is *"the gender the artist identifies with"* (editorial/community-sourced) and supports **non-binary** (and "Not applicable" for non-persons) — a legitimate self-ID source, but rarely populated for non-binary artists. | [MusicBrainz Style/Artist](https://musicbrainz.org/doc/Style/Artist) · [MetaBrainz community: concept of genders](https://community.metabrainz.org/t/about-the-concept-of-genders-2-0/395270) |
| **[ListenBrainz]** | ListenBrainz publishes **open-licensed** crowdsourced listens and a collaborative-filtering recommendation stack — an ethical, non-deprecated alternative to proprietary similarity APIs. | [ListenBrainz recommendation API docs](https://listenbrainz.readthedocs.io/en/latest/users/api/recommendation.html) |
| **[Lastfm-API]** | Last.fm provides the ingest + similarity substrate: `user.getRecentTracks` (≤200/page), `artist.getSimilar`, and tag methods — rate-limited (error 29), so caching + a `RateLimiter` are required, as shipped. | [user.getRecentTracks](https://www.last.fm/api/show/user.getRecentTracks) · [artist.getSimilar](https://www.last.fm/api/show/artist.getSimilar) |

---

## 3. Remediation backlog (close gaps in what exists)

Priority: **P0** now · **P1** next · **P2** soon · **P3** opportunistic.
Effort: **S** ≈ afternoon · **M** ≈ day or two · **L** ≈ week+.

| ID | Remediation | Personas | Pri | Effort | Evidence / tag |
| --- | --- | --- | --- | --- | --- |
| R1 ⚖︎ | **Per-run identity-coverage readout** — "N of K picks have a sourced identity; M surfaced on similarity alone." Makes *unknown-first-class* visible instead of merely true in code; the data is already computed in the re-rank. | A1,A2,B3,C1,D1 | **P0** | S | [Wikidata-P21] (unknown is common); themes #1. **[NET-NEW]** |
| R2 ⚖︎ | **Artist-facing correction / consent path** — surface "how am I labeled here," route a fix/opt-out to the source + local cache, prefer the artist's own statement. | B1,B2,B3,E1 | **P0** | M | RR-2; ROADMAP §9 "correction mechanism." **[corroborates RR-2 / §9]** |
| R3 | **Perform & commit the manual screen-reader walkthrough sign-off** (currently "pending first release") and confirm the unknown state announces respectfully. | A4,E1 | **P0** | S/M | `accessibility-2026-05-31.md` review gate. **[corroborates a11y release gate]** |
| R4 ⚖︎ | **Exposure / rank-fairness metric in eval** — mean rank and top-k share by identity basis (woman / nonbinary / unknown), reported beside precision/recall/MAP. | C1,D1 | **P1** | M | [Ferraro] (first woman ~rank 6–7); fairness audit intent. **[NET-NEW metric · corroborates fairness audit]** |
| R5 ⚖︎ | **Surface popularity-debiasing in the "why" view** — state explicitly "popularity is not an input; this pick is taste + sourced lens," answering the tokenism worry. | A3,B3 | **P1** | S | [Ferraro]; `fairness-identity.md` risk #3. **[partial → deepen]** |
| R6 ⚖︎ | **Self-statement source priority** — when a cited artist statement and a sparse third-party claim disagree, prefer the self-statement; note a known-but-upstream-empty case rather than silently "unknown." | B2,B1,B3 | **P1** | M | [MB-gender] (non-binary rarely populated); [misgender]. **[NET-NEW]** |
| R7 ⚖︎ | **Lens before/after preview** — same seed at lens 0 vs lens N, side by side, so the values nudge is legible (and visibly *bounded*, not erasing taste). | A2,A1 | **P1** | S | themes #1,#3. **[NET-NEW]** |
| R8 | **`aria-live` audit of the lens-slider re-render** — card re-ordering on lens change announces politely; focus is managed. | A4 | **P1** | S | `accessibility` review gate. **[corroborates a11y review gate · NET-NEW detail]** |
| R9 ⚖︎ | **Egress allow-list + export-schema tests** — one test naming *exactly* the two outbound functions (`lastfm` fetch, Spotify export); another asserting **no identity field can appear** in any export format. | D2,B4 | **P1** | S | `privacy-notes.md`; non-redistribution. **[corroborates privacy gates · NET-NEW test]** |
| R10 ⚖︎ | **End-to-end tests for trans & intersex artists** mirroring the existing non-binary end-to-end test, so inclusion is demonstrated, not just permitted. | D1,B2 | **P2** | S/M | `fairness-identity.md`; `identity-data-ethics.md` (trans-inclusion QID map). **[corroborates fairness audit · partial]** |
| R11 ⚖︎ | **Explicit "never batch-infer from names" note** on the basis/attribution, citing the Wikidata community stance. | B4,D1 | **P2** | S | [Wikidata-P21], [misgender]. **[corroborates no-inference · NET-NEW doc]** |
| R12 | **Source-change watch doc** — a short, dated table tracking Last.fm / MusicBrainz / Wikidata / Discogs / Spotify API drift (e.g., the 2024 Spotify deprecation) so a feature can't silently rot. | E1,A5 | **P2** | S | [Spotify-API]; ROADMAP recheck cadence. **[corroborates recheck cadence · NET-NEW]** |

---

## 4. Expansion backlog (new capability)

| ID | Expansion | Personas | Pri | Effort | Evidence / tag |
| --- | --- | --- | --- | --- | --- |
| E1 ⚖︎ | **Thumbs feedback to tune the lens**, *with* a feedback-loop guard (single-user, offline; feedback adjusts lens weight, never re-introduces inference or popularity). | A2,A1 | **P1** | M | ROADMAP "Should: thumbs feedback"; [Ferraro] (guard the loop). **[corroborates "Should"]** |
| E2 ⚖︎ | **Identity-aware export sidecar** — optional CSV/JSPF carrying the *sourced* basis + provenance + fetch date alongside the names, so values context survives the export; never emits an inferred field. | A5 | **P2** | S | extends shipped `export/`. **[NET-NEW]** |
| E3 ⚖︎ | **Artist self-statement intake** — a cited self-ID becomes a first-class permitted `SourceKind`, letting artists (esp. non-binary / indie) be correctly sourced. | B1,B2,B3 | **P2** | M | extends `pipeline/identity.py`; closes R2's supply side. **[extends identity model]** |
| E4 | **Saved "discovery report"** — an account-free, revisitable/shareable artifact of a run (picks + why + coverage). | A1 | **P2** | S | ROADMAP "Could: a discovery report." **[corroborates "Could"]** |
| E5 ⚖︎ | **Additional sourced value lenses** (local/indie, BIPOC) behind the *same* sourced-only, unknown-first-class rules. | C2,A1 | **P2** | M | ROADMAP "Could: additional sourced value lenses." **[corroborates "Could"]** |
| E6 | **Deepen the ListenBrainz collaborative signal** — open-licensed similarity that sidesteps the deprecated Spotify endpoints. | C1,E1 | **P2** | M | ROADMAP "Should: ListenBrainz signal"; [ListenBrainz], [Spotify-API]. **[corroborates "Should"]** |
| E7 ⚖︎ | **"Add a sourced value lens" contributor playbook** + a `SourceKind`/lens extension point with a conformance test that fails if a lens introduces inference. | C2 | **P2** | S/M | extends architecture; ROADMAP §9 contribution guide. **[NET-NEW]** |
| E8 ⚖︎ | **User-facing "why unknown is normal" explainer** — a non-pejorative framing of the unknown bucket (≈46% of P21 missing) so users read it as expected, not failure. | D1,A1 | **P3** | S | [Wikidata-P21]. **[NET-NEW]** |
| E9 | **Maintainer correction queue / re-enrich workflow** — flag → re-enrich → `make verify`, so corrections are routine not heroic. | E1,B1 | **P3** | M | ROADMAP §11 maintenance; powers R2. **[NET-NEW workflow]** |
| E10 | **Public methodology writeup** — "values-aware recommendation *without* inferring identity," carrying the cited gap + bias evidence; the portfolio artifact. | C1,D1 | **P3** | M | ROADMAP §9 "the writeup is the artifact." **[corroborates §9]** |

---

## 5. Sequenced roadmap

A staging that respects the existing M0–M6 build (which is done through Beta) and
layers the research-backed work on top. Each phase keeps both invariants intact.

- **Phase 0 — Make the guarantees visible (now).** R1 (coverage readout), R3 (SR
  walkthrough sign-off), R9 (egress/export-schema tests). All small, all turn an
  *internal* truth into a *demonstrable* one. Highest trust-per-hour.
- **Phase 1 — Measure and explain fairness (next).** R4 (exposure/rank metric), R5
  (popularity-debiasing in the "why"), R7 (lens before/after), R8 (aria-live audit).
  Answers the researcher, the skeptic, and the AT user; extends shipped eval + UI.
- **Phase 2 — Close the artist-side loop.** R2 (correction/consent path) → E3
  (self-statement intake) → E9 (maintainer correction queue). The recurring artist
  ask; sequence it so the workflow lands behind the user-facing path.
- **Phase 3 — Deepen inclusion & resilience.** R6 (self-statement priority), R10
  (trans/intersex end-to-end tests), R11 (no-batch-infer note), R12 (source-change
  watch), E6 (ListenBrainz signal). Inclusion demonstrated; sources future-proofed.
- **Phase 4 — Extend & publish.** E1 (guarded thumbs feedback), E2 (export sidecar),
  E4 (discovery report), E5/E7 (new lenses + contributor playbook), E8 (unknown
  explainer), E10 (methodology writeup). Growth, only after the core is provably fair.

---

## 6. Recommended first sprint (highest-leverage, mostly already-built infra)

The triage and the existing roadmap converge: the engine and its guarantees exist;
what's thin is *making them visible and measured*. Ship these five:

1. **R1 — per-run identity-coverage readout.** The single highest-leverage move: it
   makes *unknown-first-class* legible to every persona (A1, A2, B3, C1, D1) using
   data the re-rank already computes. Afternoon-sized. ⚖︎
2. **R4 — exposure/rank-fairness metric.** Turns "fair by design" into a *reported
   number*, directly answering the researcher (C1) and the published finding that
   recommenders rank women several positions lower [Ferraro]. Extends the shipped
   eval. ⚖︎
3. **R2 — artist-facing correction/consent path.** Closes the loudest artist-side gap
   (B1, B2, B3) and corroborates RR-2 + ROADMAP §9 — the people most affected by the
   labels get a say. ⚖︎
4. **R3 — perform & commit the manual SR walkthrough sign-off.** The a11y story is
   currently "auto-green, review-pending"; doing the pass makes the release-gate claim
   *true* (A4, E1).
5. **R9 — egress allow-list + export-schema tests.** Cheap; makes the
   no-misusable-database promise *enumerable and tested* (D2, B4), converting a
   structural property into a guarantee.

Bundle the afternoon wins alongside: **R5** (popularity-debiasing note), **R7** (lens
before/after), **R11** (no-batch-infer note).

---

## 7. Traceability matrix (persona → findings)

| Persona | Remediations | Expansions |
| --- | --- | --- |
| A1 Chelsea-listener | R1, R5, R7 | E1, E2(via export), E4, E5, E8 |
| A2 Rosa (diversifier) | R1, R7 | E1 |
| A3 Devin (skeptic) | R5 | — |
| A4 Mara (a11y/SR) | R3, R8 | — |
| A5 Theo (export) | R12 | E2 |
| B1 Vera (woman, subject) | R2, R6 | E3, E9 |
| B2 Ash (non-binary) | R2, R6, R10 | E3 |
| B3 Lupe (indie) | R1, R2, R5 | E3 |
| B4 Jonas (MB/WD steward) | R9, R11 | — |
| C1 Dr. Lin (recsys) | R1, R4 | E6, E10 |
| C2 Sam (contributor) | — | E5, E7 |
| D1 Dr. Okonkwo (ethics) | R1, R4, R10, R11 | E8, E10 |
| D2 Iris (privacy/sec) | R9 | — |
| E1 Chelsea-maintainer | R2, R3, R12 | E1, E9 |

---

## 8. Validate with real users / risks

This roadmap is built on **real research** but a **synthetic** panel. Before
committing engineering time, validate with real people — and weight their input above
the personas':

- **Artists as identity-data subjects (B1–B4) are the priority interviews.** Their
  consent and correctness are the project's core ethical claim; a synthetic stand-in
  cannot grant or withhold consent. Ask: would you use a correction path? Do you want
  to be in this at all? Is the sourcing you'd accept the one we permit?
- **Test R1/R4 framing with real listeners.** Does the coverage readout read as honest
  transparency or as a scorecard? Does the exposure metric reassure or alienate the
  skeptic (A3)?
- **Confirm source realities, not assumptions.** Re-verify live what fraction of *your
  own* library resolves to known vs unknown identity; the ~46% Wikidata P21 figure
  [Wikidata-P21] and rare non-binary population [MB-gender] suggest "unknown" will
  dominate — design for that, don't treat it as failure.
- **Risks of acting on this document alone.**
  - *Over-instrumenting fairness* (R4) could turn a respectful tool into a
    quota-counter; keep metrics descriptive, not target-driven.
  - *A correction path (R2/E3) is an identity-data intake* — it must inherit every
    guardrail (sourced, cited, correctable, never redistributed) or it becomes the
    very database the project exists to avoid. ⚖︎
  - *New lenses (E5)* (BIPOC, local/indie) carry the same essentialism/sourcing trap
    as gender; do not ship one without the sourced-only, unknown-first-class
    conformance test (E7). ⚖︎
  - *Source deprecation* (Spotify 2024 [Spotify-API]) shows any upstream can vanish;
    R12 mitigates but cannot prevent a feature going dark.

---

## 9. Honest limits

This is a roadmap derived from cited research **plus** a simulated panel — not from
real discovery. The evidence base (the gender gap, recommender amplification,
inference harm, source sparsity) is real and cross-checked; the *demand* for any
specific feature is not. The exercise over-represents the author's model and the
literature and under-represents what real listeners and — most importantly — real
**artists being labeled** would surprise us with. Treat the priorities as a
*starting hypothesis*: ship the cheap visibility/measurement wins (R1, R3, R4, R9)
that are low-regret regardless, and gate the artist-facing and expansion work behind
real conversations with the B-group. Throughout, the two invariants —
**sourced-never-inferred** and **unknown-is-first-class** — are not negotiable line
items; they are the reason the project exists, and every item above ships only if it
keeps them true.

→ The personas and simulated interviews behind this triage:
[`USER-RESEARCH.md`](./USER-RESEARCH.md). → The build plan and correctness gates this
complements: [`docs/ROADMAP.md`](./ROADMAP.md).

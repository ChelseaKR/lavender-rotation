# User Research — Synthetic Personas & Simulated Interviews

Last verified: 2026-07-11

> [!WARNING]
> **These personas and interviews are synthetic.** They were generated as a
> structured brainstorming device — *not* conducted with real people. No real
> listener, artist, editor, or reviewer said any of this. The panel exists to
> pressure-test the product from many angles at once; it is **not** evidence of
> demand and does **not** substitute for real discovery. Treat every "quote" as a
> hypothesis to validate, not a finding. (This is consistent with how the project
> already labels its non-real data — the bundled `pipeline/demo.py` profile is a
> clearly-marked *demo/synthetic* dataset, not a real user's scrobbles.)
>
> The honest next step is real conversations with ≥1 person per role below —
> especially the **artists as identity-data subjects** (B-group), whose consent and
> correctness this project most directly affects.
> **Last assembled: 2026-06-30.**
>
> This is a dated research snapshot, so some simulated frictions were resolved
> after assembly. Current implementation/defer/reject decisions live in the
> disposition table at the top of `RESEARCH-ROADMAP.md`.

## Why do this at all

This is a values-laden recommender that makes claims *about real people's
identities*. Role-playing the full cast — listeners, the artists being labeled, the
stewards of the upstream data, the reviewers, the researcher, the maintainer —
surfaces gaps a single author misses and forces the question *"who is each guarantee
actually for?"* The synthesis (in [`RESEARCH-ROADMAP.md`](./RESEARCH-ROADMAP.md)) is
tagged so it can't quietly become a wishlist:

- **[shipped]** — already exists in the Beta build (M0–M6).
- **[partial]** — a piece exists; the persona wants it deepened or surfaced.
- **[blocked]** — needs an external/human input (live API creds, a real walkthrough, a label correction from the artist).
- **[new]** — genuinely surfaced here.

## How to read a persona

Each card compresses the simulated interview to five lines — **Goal · Values today
(mapped to real, shipped features) · Gets stuck · Wants next · Adopts/walks.**
"Values today" never references a feature that isn't in the repo; where a persona
wants something new it lives under *Wants next*.

## Method

- **Sampling frame.** Everyone the system touches: people who **listen and discover**
  (the owner-as-listener, the intentional diversifier, the skeptic, the
  assistive-tech user, the export user); the people who get **represented** (an
  established woman artist as a data subject, a nonbinary artist specifically, a
  small/independent artist, and the MusicBrainz/Wikidata steward whose edits are
  consumed); the people who **build & evaluate** (a recsys/ML fairness researcher, an
  OSS contributor); the people who **assure & audit** (a representation/ethics
  reviewer, a privacy/security reviewer); and the person who **operates** it (the
  owner/maintainer).
- **Protocol.** For each persona: a goal, a walkthrough of the surfaces they'd
  actually touch (the Streamlit dashboard, the `wad` CLI, the "Why this artist" view,
  the export path, the audit docs), what worked, where they stalled, and an open
  "what would make this a 10/10" prompt.
- **Synthesis.** Frictions → **R**emediations; wishes → **E**xpansions, triaged with
  value × effort and a persona trace in the roadmap. Two product constraints are held
  fixed and re-checked against every item: **identity is sourced, never inferred**,
  and **"unknown" is first-class and never down-ranks.**
- **Effort scale.** S ≈ an afternoon · M ≈ a day or two · L ≈ a week+.

### Research basis (why these personas have the frictions they do)

The personas aren't free invention — each maps to a documented, cited reality.
Full citation list and the high-stakes cross-checks are in
[`RESEARCH-ROADMAP.md`](./RESEARCH-ROADMAP.md); the load-bearing few:

- **The gender gap the product targets is real and measured.** USC Annenberg's
  *Inclusion in the Recording Studio?* finds women were ~22% of Billboard Hot 100
  artists across 2012–2023 (a 12-year high of ~35% in 2023, with ~0.6% non-binary),
  women songwriters historically ~13%, and women producers in the low single digits
  — roughly **30 men per woman producer** across the years studied, with ~94% of
  songs crediting zero women producers; the 2026 edition reports the gains
  *reversing*.
  [[USC PDF]](https://assets.uscannenberg.org/docs/aii-inclusion-recording-studio-2025-01-29-2.pdf)
  [[Billboard]](https://www.billboard.com/business/business-news/usc-annenberg-study-gender-equality-music-industry-1235591929/)
  [[Annenberg]](https://annenberg.usc.edu/news/research-and-impact/annenberg-inclusion-initiatives-annual-report-popular-music-reveals-little)
  (accessed 2026-06-30)
- **Recommenders amplify, not just mirror, that gap.** Ferraro, Serra & Bauer's
  *Break the Loop* (ACM RecSys 2021) shows a collaborative-filtering recommender on
  data that is ~25% women surfaces the first woman artist only around **rank 6–7**
  while the first man lands at rank 1, and a feedback loop makes it worse over time —
  motivating the project's bounded re-rank.
  [[TechXplore]](https://techxplore.com/news/2021-04-gender-bias-music-algorithms.html)
  [[Utrecht]](https://www.uu.nl/en/news/music-recommendation-algorithms-are-unfair-to-female-artists-but-we-can-change-that)
  (accessed 2026-06-30)
- **Inferring gender harms exactly the people this tool wants to serve.** Name-/
  feature-based gender inference disproportionately misclassifies women and
  LGBTQ+/non-binary people, and the Wikidata community explicitly *opposes*
  batch-adding "sex or gender" from given names — direct support for the
  sourced-never-inferred rule.
  [[Misgendering survey]](https://www.sciencedirect.com/science/article/pii/S0160791X25003008)
  [[Wikidata P21 talk]](https://www.wikidata.org/wiki/Property_talk:P21)
  (accessed 2026-06-30)
- **The identity data is genuinely sparse.** Only ~46% of human Wikidata items carry
  P21 at all, so "unknown" is not an edge case — it's the *common* case, which is why
  the persona group keeps returning to how unknown is surfaced.
  [[Quantifying the Gap, ACM]](https://dl.acm.org/doi/fullHtml/10.1145/3479986.3479992)
  (accessed 2026-06-30)

---

## Persona roster

| # | Persona | Group | Primary goal | Top friction |
| --- | --- | --- | --- | --- |
| A1 | **Chelsea** — owner, using it as a listener | Listen & Discover | Lean into her taste for women/female-fronted *on purpose* | Can't tell at a glance how much of a run is sourced vs unknown |
| A2 | **Rosa** — listener diversifying her rotation | Listen & Discover | Break a male-default rotation without it feeling like medicine | Unsure the picks are taste, not a quota |
| A3 | **Devin** — skeptical listener | Listen & Discover | Trust the picks aren't tokenism or a filter bubble | Wants proof it beats "just popular women" |
| A4 | **Mara** — screen-reader + keyboard user | Listen & Discover | Read *why* each artist surfaced, fully via AT | Manual SR walkthrough sign-off still pending |
| A5 | **Theo** — Spotify-export user | Operate & Export | Take the picks into his daily-driver app | Needs live OAuth creds; wants identity to survive the export |
| B1 | **Vera** — established woman artist, data subject | Represent Correctly | Be labeled correctly and consent to it | Her Wikidata P21 is stale; no path to flag it from her side |
| B2 | **Ash** — non-binary artist | Represent Correctly | Not be collapsed into "woman" or erased into "unknown" | Wants the *basis* tied to their own statement, not a guess |
| B3 | **Lupe** — small/independent artist | Represent Correctly | Get surfaced on merit despite thin metadata | "Unknown" identity must not cost her the slot |
| B4 | **Jonas** — MusicBrainz/Wikidata steward | Represent Correctly | See his edits consumed faithfully, attributed, not weaponized | Worried the cache becomes a de-facto gender database |
| C1 | **Dr. Lin** — recsys/ML fairness researcher | Build & Evaluate | Verify it beats the popularity baseline *and* is fair | Eval reports precision, not exposure/rank fairness |
| C2 | **Sam** — OSS contributor / data engineer | Build & Evaluate | Add a new sourced value lens or data source | Unsure how to add a lens without breaking the no-inference guarantee |
| D1 | **Dr. Okonkwo** — representation/ethics reviewer | Assure & Audit | Confirm non-essentialism and respectful "unknown" handling | Trans/intersex paths asserted in docs; wants them visibly tested end-to-end |
| D2 | **Iris** — privacy/security reviewer | Assure & Audit | Confirm no misusable identity dataset leaks | Wants the export egress and cache scope provably bounded |
| E1 | **Chelsea** — owner / maintainer | Operate & Export | Keep sources fresh, corrections folded in, releases honest | Correcting a wrong label is a manual re-enrich, not a workflow |

---

## Interviews

### Group A — Listen & Discover (users)

#### A1 — Chelsea, owner using it as a listener
- **Goal.** Lean into a taste that already skews toward women and female-fronted bands — *on purpose*, without a tool that either ignores identity or guesses it.
- **Values today.** The hybrid `lavender recommend --lens 0.5` set; the always-visible, explained lens slider; the per-pick "Why this artist" card (signals + identity basis + provenance); local-first, no account. *(the core value prop — [shipped])*
- **Gets stuck.** On a 10-pick run she can't see *how much* of it is sourced-women vs "unknown surfaced on similarity" — the proportion is implicit, so the values lens feels like it's working without proof.
- **Wants next.** A one-line coverage readout per run ("6 of 10 picks have a sourced identity; 4 surfaced on taste alone"); a saved "discovery report" she can revisit.
- **Adopts if.** Each session shows the values lens *and* taste are both honored. **Walks if.** It starts to feel like it's hiding men or padding with already-famous women.

#### A2 — Rosa, listener intentionally diversifying her rotation
- **Goal.** Get out of a rotation that defaults male, and find women/nonbinary artists she'll genuinely keep.
- **Values today.** Recommendations weighted by the values lens but anchored in *her* scrobbles; the "why" reason that names the actual taste signal ("similar to artists you play; tagged dream-pop"). *(the re-rank + why view — [shipped])*
- **Gets stuck.** Without a number, she can't tell the diversification from a placebo; she also wonders whether turning the lens up just buries music she'd like.
- **Wants next.** A before/after view (same seed, lens 0 vs lens 0.7) so she can *see* what the lens changed; lightweight thumbs to teach it.
- **Adopts if.** The lens visibly broadens without degrading taste. **Walks if.** High-lens runs feel worse and she can't tell why. *(thumbs feedback is roadmap "Should" — [partial])*

#### A3 — Devin, skeptical listener ("is this just tokenism?")
- **Goal.** Be convinced the picks are real taste matches, not a quota or a filter bubble.
- **Values today.** The offline eval that shows the hybrid beats a popularity baseline (P@5 0.6 vs 0.2 in `eval-report.json`); the **boost-only, bounded** re-rank (a man keeps his exact base score; nothing is dropped); the explicit `inferred = False` on every card. *(eval + rerank guarantees — [shipped])*
- **Gets stuck.** "Beating popularity on precision" doesn't answer *his* worry — that the lens just re-floats already-famous women. He wants the popularity-debiasing claim made legible.
- **Wants next.** A visible "this pick is *not* here because it's popular — popularity isn't an input" note; a surfacing-of-obscure-artists stat.
- **Adopts if.** He can see taste-first ranking with a transparent, bounded nudge. **Walks if.** It reads as ideological rather than earned.

#### A4 — Mara, screen-reader + keyboard-only user
- **Goal.** Read *why* each artist surfaced — signals, identity basis, sources — entirely through assistive tech.
- **Values today.** Zero `axe` violations on the rendered set; charts shipped with `<table>` + `<caption>` + `th[scope]` equivalents; identity rendered as **text + glyph, never color-only**; skip link, real headings, keyboard-complete lens slider. *(the a11y gate — [shipped])*
- **Gets stuck.** The mechanical gate is necessary, not sufficient: the manual VoiceOver/NVDA walkthrough sign-off is **still "pending first release."** Streamed/re-rendered card updates on lens change may not announce politely.
- **Wants next.** The committed SR walkthrough actually performed and signed; an `aria-live` audit of the lens-slider re-render; confirmation the "unknown" state is announced respectfully, not skipped.
- **Adopts if.** A real SR pass confirms card order (heading → identity → why → sources) reads sensibly. **Walks if.** The slider silently re-orders cards with no announcement. *(manual walkthrough — [blocked: human pass])*

#### A5 — Theo, Spotify-export user
- **Goal.** Push a generated set into Spotify, his daily driver, in one click.
- **Values today.** `lavender export` + dashboard download; **credential-free** formats (plain text / CSV / M3U / JSPF) that need no account and stay fully local; live Spotify via env-only OAuth that sends **only artist names**, never listening history or identity data. *(export package — [shipped])*
- **Gets stuck.** Live export needs his own Spotify app + a browser consent (the `RequestsTransport` path is the one uncovered, live-network surface); the exported playlist loses the *why* and the identity basis — it's just names.
- **Wants next.** A clearer first-run OAuth walkthrough; an optional sidecar (CSV/JSPF) that carries the *sourced* identity basis + provenance so the values context survives the export — without ever emitting an inferred field.
- **Adopts if.** Export is one consent away and keeps the provenance. **Walks if.** Setup is opaque or the export quietly drops the "unknown"/sourced distinction. *(playlist/export was roadmap "Should" — [shipped]; identity sidecar — [new])*

### Group B — Represent Correctly (artists / identity subjects)

#### B1 — Vera, established woman artist (identity-data subject)
- **Goal.** Be represented correctly, with a basis she'd actually consent to.
- **Values today.** Her label is **sourced and cited** (Wikidata P21 / MusicBrainz gender / a quoted statement), shown with the *raw value each source asserted* and a fetch date; the system never guesses from her name, voice, or genre. A cited local correction is supported, but automatic upstream re-enrichment is not shipped. *(sourced-only identity + provenance — [shipped]; live correction fold-back — [open])*
- **Gets stuck.** Her Wikidata P21 is stale/wrong, and there's **no path from her side** to flag it — she'd have to know the tool exists and go edit upstream. She can't see how she's being labeled unless she runs it.
- **Wants next.** An artist-facing "how am I labeled here / request a correction or opt-out" route that routes to the source and the local cache; a way to prefer her own public statement over a third-party claim.
- **Adopts if.** She can see and correct her basis. **Walks if.** A wrong label persists with no recourse. *(correction mechanism is roadmap §9 + RR-2 — [partial])*

#### B2 — Ash, non-binary artist
- **Goal.** Be represented as non-binary — not collapsed into "woman," not flattened to "unknown" because the binary didn't fit.
- **Values today.** `Gender.NONBINARY` is a **first-class** enum member (not "other"), resolvable from a sourced self-statement and boosted by the lens *exactly like women*; proven end-to-end by `test_nonbinary_survives_end_to_end_in_recommendations`; trans women are women / trans men are men in the Wikidata QID map; intersex/third-gender are representable, not flattened. *(non-binary model — [shipped])*
- **Gets stuck.** Upstream reality undercuts it: MusicBrainz/Wikidata rarely populate non-binary (charts data shows ~0.6% non-binary), so Ash is far more likely to land in "unknown" than to be correctly surfaced — an erasure the code can't fix alone.
- **Wants next.** Preference for a **self-statement source** over a sparse third-party claim; a visible note when a known non-binary artist exists but upstream is empty (so the gap is named, not silently "unknown").
- **Adopts if.** Their identity, when sourced, is shown and boosted on equal footing. **Walks if.** They're quietly binarized or the "unknown" bucket erases a known identity. *(self-statement priority — [new])*

#### B3 — Lupe, small/independent artist
- **Goal.** Get discovered on musical merit even though her metadata is thin.
- **Values today.** The re-rank is **boost-only and bounded** — an unknown-identity artist's score is invariant to lens strength and is *never dropped*; the base score is taste-only (popularity is **not** a hybrid input), so an obscure good match can outrank a famous one. *(unknown-first-class + popularity-debiasing — [shipped])*
- **Gets stuck.** Because her identity is "unknown," she gets no values *boost*, so in a high-lens run she can still be out-ordered by sourced-women — surfaced, but lower. The system is fair-by-construction yet she'd love a path to *become* sourced.
- **Wants next.** A self-serve way to add a cited self-statement (which would let her be correctly boosted if she's a woman/nonbinary); reassurance, in the UI, that "unknown" didn't cost her the slot.
- **Adopts if.** Thin metadata never silently buries her. **Walks if.** "Unknown" behaves like a soft penalty in practice. *(self-statement intake — [new]; unknown-never-penalised — [shipped])*

#### B4 — Jonas, MusicBrainz/Wikidata steward
- **Goal.** See the open data he curates consumed faithfully — attributed, rate-limited, and not turned into something harmful.
- **Values today.** Enrichment honors API terms and rate limits (`RateLimiter` + cache); CC0/MusicBrainz attribution; the repo ships **no bulk identity dataset** — labels are resolved on-demand and cached locally (git-ignored), with **no identity export path**. *(non-redistribution + rate-limit respect — [shipped])*
- **Gets stuck.** He's seen "gender of musicians" scrape projects before; he wants assurance the local cache can't become a redistributable database, and that the tool won't back-fill gender from names (the very thing the Wikidata community forbids).
- **Wants next.** A documented, tested guarantee that no identity blob is ever exported; a visible "sourced from MusicBrainz/Wikidata, fetched <date>" attribution on each basis; a clear "we never batch-infer from names" statement citing the Wikidata stance.
- **Adopts if.** His data is used within terms and can't be weaponized. **Walks if.** The cache looks one `--dump` flag away from a gender database. *(non-redistribution — [shipped]; explicit no-batch-infer note — [new])*

### Group C — Build & Evaluate

#### C1 — Dr. Lin, recsys/ML fairness researcher
- **Goal.** Verify two things independently: that the hybrid genuinely beats the popularity baseline, and that "fairness" is measured, not asserted.
- **Values today.** Reproducible, seeded recommendations (snapshot test); a committed `eval-report.json` (precision/recall/MAP@k, hybrid vs popularity); segmented fairness audit (by identity basis, gender, popularity tier); the bounded-boost math in `rerank.py`. *(eval + fairness audit — [shipped])*
- **Gets stuck.** The eval reports **accuracy** (precision@k), not **exposure fairness** — there's no metric for *where* women/nonbinary/unknown artists land in the ranking, which is exactly the harm Ferraro et al. quantify (first woman at rank ~6–7). Single-user offline eval also can't show feedback-loop dynamics.
- **Wants next.** A rank-/exposure-fairness metric (mean rank and share-in-top-k by identity basis) alongside precision; a documented baseline-comparison method; a note on why a single-user offline design avoids the feedback loop the literature warns about.
- **Adopts if.** Fairness is a *measured, reported* number, not a property claim. **Walks if.** "Fair" rests only on the boost-only design with no exposure metric. *(exposure-fairness metric — [new]; beats-popularity — [shipped])*

#### C2 — Sam, OSS contributor / data engineer
- **Goal.** Add a new **sourced** value lens (e.g., local/indie or BIPOC artists) or a new data source, and get it merged.
- **Values today.** Clean package seams (`pipeline` / `recommender` / `export`), type-invariant guardrails (`IdentityLabel.__post_init__` raises on unsourced identity), and the no-inference AST test as a regression backstop; `make verify` as a single green gate. *(architecture + guardrail tests — [shipped])*
- **Gets stuck.** It's not obvious how to add a lens *without* re-introducing inference — e.g., a "BIPOC" lens has the same sourcing/essentialism trap as gender, and there's no contributor playbook for "new sourced lens, same rules."
- **Wants next.** A documented "add a sourced value lens" recipe (permitted sources, unknown default, the tests it must pass); a `SourceKind`/lens extension point with a conformance test.
- **Adopts if.** His first lens PR is mergeable in a day and can't violate the guardrails. **Walks if.** Extending means hand-rolling identity logic that bypasses the invariants. *(additional sourced lenses are roadmap "Could" — [partial]; contributor recipe — [new])*

### Group D — Assure & Audit

#### D1 — Dr. Okonkwo, representation/ethics reviewer
- **Goal.** Confirm the project is genuinely non-essentialist and that "unknown" is handled with respect, not as a failure state.
- **Values today.** The identity-data-ethics doc (permitted sources, no-inference policy, trans-inclusion QID map, correctability); the fairness doc's representational-harm findings; unknown-never-penalised proven in tests; "female-fronted" kept distinct from any member's gender (tri-state, sourced). *(audit docs + tests — [shipped])*
- **Gets stuck.** Trans, intersex, and third-gender support is *asserted in prose and the QID map* but he wants each path **visibly exercised end-to-end** like non-binary already is; he also wants the "unknown is normal" stance to be visible to *users*, not just true in code.
- **Wants next.** End-to-end tests/fixtures for trans and intersex artists mirroring the non-binary one; a user-facing framing that states unknown is expected and non-pejorative; a periodic representation re-review tied to source-schema changes.
- **Adopts if.** Every identity path is demonstrated, not just permitted. **Walks if.** Inclusion is documented but only non-binary is actually tested. *(deepen identity tests — [partial]; user-facing unknown framing — [new])*

#### D2 — Iris, privacy/security reviewer
- **Goal.** Confirm the tool can't leak a misusable identity dataset or the user's listening history.
- **Values today.** Local-first SQLite cache (git-ignored); `test_privacy.py` asserts network egress exists **only** in `lastfm.py`; export is a separate, opt-in boundary outside `pipeline`/`recommender`, isolated to one injectable transport sending only artist names; secrets from env only; `make clean` deletion; cache rows that violate a guardrail **fail closed on load**. *(privacy + security gates — [shipped])*
- **Gets stuck.** She wants the *two* egress paths (Last.fm fetch + Spotify export) enumerated as an explicit, tested allow-list, and confirmation the export can never be coerced into emitting identity fields; RR-1 (pip CVE) is accepted but she wants the recheck cadence honored.
- **Wants next.** An "egress allow-list" test naming exactly the two outbound functions; an export-schema test asserting no identity field can appear in any format; a scheduled RR-1 re-check.
- **Adopts if.** Every outbound byte is enumerated and tested. **Walks if.** A new code path could open a third, unaudited egress. *(egress allow-list / export-schema test — [new]; current isolation — [shipped])*

### Group E — Operate & Export

#### E1 — Chelsea, owner / maintainer
- **Goal.** Keep the sources fresh, fold corrections back in, and ship releases whose claims are all true.
- **Values today.** `make verify` as one green gate (lint, `mypy --strict`, 108 tests @ 94%, dep-audit, secret scan, axe=0, eval-beats-baseline); committed audit artifacts regenerated by the build; the residual-risk register (RR-1/2/3) and recheck cadences. *(CI + audit discipline — [shipped])*
- **Gets stuck.** Correcting a wrong upstream label is a **manual** "fix the source, re-enrich" with no workflow or queue; the **manual SR walkthrough sign-off is still pending**, so the a11y story is "auto-green, review-pending"; source schemas (Spotify's 2024 API deprecation, MusicBrainz gender vocab) drift under her.
- **Wants next.** A lightweight correction queue (flag → re-enrich → verify); to actually perform and commit the SR walkthrough sign-off; a documented "source-change watch" (Last.fm / MusicBrainz / Wikidata / Discogs / Spotify) so deprecations don't silently rot a feature.
- **Adopts if.** Corrections and release sign-offs are routine, not heroic. **Walks if.** Keeping the audits honest stays entirely manual and easy to skip. *(correction workflow + SR sign-off — [partial/blocked])*

---

## Cross-cutting themes (what the cast agrees on)

1. **"Unknown is first-class" is true in code but invisible in the product.** A1, A2,
   B3, C1, and D1 all bump the same wall: the guarantee is *mechanical* (boost-only,
   never dropped, proven in tests) but a user/artist/reviewer can't *see* the
   proportion of sourced-vs-unknown or that unknown didn't cost a slot. The single
   highest-leverage move is to **surface the coverage** the code already computes.
2. **Sourcing is respected; *correction* is not yet a workflow.** B1, B2, B3, B4, and
   E1 converge on the gap between "labels are correctable in principle (fix source →
   re-enrich)" and "there's a path to do it." The artists most affected have the least
   access. A correction/consent route is the recurring artist-side ask.
3. **Fairness is designed-in but under-measured.** C1 (and D1) want exposure/rank
   fairness *reported*, not just accuracy — directly echoing the published finding
   that recommenders rank women several positions lower. The boost-only design is the
   right mechanism; it needs a number.
4. **Inclusion beyond the binary is asserted more than it's demonstrated.** B2 and D1
   note non-binary is genuinely first-class and tested end-to-end, but trans /
   intersex / third-gender support lives in prose + a QID map without the same
   end-to-end proof — and upstream sparsity means even correct identities often fall
   into "unknown."
5. **Egress and non-redistribution are strong but worth making *enumerable*.** B4 and
   D2 both want the "no gender database can leak here" promise expressed as an
   explicit, tested allow-list rather than a property of careful structure.
6. **External data is the substrate and it's shifting.** A5, B2, B4, C1, and E1 all
   touch source fragility — Wikidata P21 is ~46% covered, MusicBrainz rarely fills
   non-binary, and Spotify deprecated its similarity/audio endpoints in 2024 — so the
   project's lean on Last.fm/ListenBrainz/MusicBrainz/Wikidata/Discogs is a *resilience
   choice* that should be documented as one.

## Honest limits of this exercise

This is simulated. It can generate plausible needs and obvious gaps, but it cannot
tell you **which** are real, how many listeners or artists want them, or whether an
artist would actually use a correction path. It over-represents the author's mental
model and the published literature, and under-represents what only real people —
especially the **artists being labeled** — would surprise you with. The personas most
worth replacing with real humans are the **identity-data subjects (B1–B4)**: their
consent and correctness are the project's core ethical claim, and no synthetic panel
can stand in for their actual say. **Do not prioritize off this document alone** — use
it to design the questions for, and lower the cost of, real conversations.

→ Triaged findings, the evidence base, and a sequenced plan that **complements**
[`docs/ROADMAP.md`](./ROADMAP.md) live in
[**`RESEARCH-ROADMAP.md`**](./RESEARCH-ROADMAP.md).

# Identity Data Ethics

> Instantiates RESPONSIBLE-TECH-AUDITS §A/§B for the distinctive risk of this
> project: doing values-aware recommendation without inferring, essentializing,
> or building a misusable gender database.
> **Last verified: 2026-08-16 · Recheck cadence: per identity-source API change.**
>
> *2026-08-16 — ADR 0011 added a second sourced axis (sexual orientation via
> Wikidata P91 or a cited statement, plus trans self-identification read from
> values a gender source already asserted). This is a step-change in
> sensitivity and the commitments below now carry more weight than they did:
> the worst-case artifact is no longer "a gender-of-musicians dataset" but a
> list of who is queer or trans, which is dangerous for real people in much of
> the world. Non-redistribution, no-export, local-only, and correct-at-source
> are unchanged and are now load-bearing rather than precautionary.*

## Permitted identity sources (the *only* ones)

| Source | Establishes | Provenance | Known limits |
|--------|-------------|------------|--------------|
| Wikidata **P21** ("sex or gender") | individual gender | QID claim + entity URL | sparse; occasionally wrong/out of date |
| MusicBrainz **gender** field | individual gender | editorial / self-reported | editorial; "Not applicable" → unknown |
| **Artist statement** (cited) | individual gender | public self-identification + citation | requires a real, linkable source |
| Discogs **lineup** / MusicBrainz **relationship** | band composition (who fronts) | lineup URL | composition only — never a person's gender |

These map 1:1 to `SourceKind` in `pipeline/models.py`. There is deliberately **no**
source kind for a name, voice, image, or genre. Code: `pipeline/identity.py`.

## Policies

- **No inference, ever.** Gender is read only from the sources above. Enforced by
  `IdentityLabel.__post_init__` (a non-unknown gender without an individual-source
  citation raises) and proven by `tests/test_no_inference.py` (vocabulary check +
  AST scan of the resolver + behavioural checks). → metric *Inferred labels = 0*.
- **Unknown is first-class.** Default everywhere; never penalised. See
  `fairness-identity.md` and `tests/test_unknown_first_class.py`.
- **Provenance is mandatory.** Every known label carries its citations + fetch
  date. `tests/test_provenance.py`. → metric *Labels with a cited source = 100%*.
- **Female-fronted ≠ a member's gender, and ≠ any other gender.** It is a sourced,
  tri-state band property (`BandComposition.female_fronted`), kept separate from any
  individual's label, and narrow: `True` only when a front-person's *own* sourced
  gender is `WOMAN`. The unflattened fact is `BandComposition.sourced_front_genders`,
  which every rendered label is written from, so a band fronted only by a sourced
  nonbinary artist is described as such rather than as "female-fronted" (#69).
  `tests/test_identity_model.py::test_female_fronted_is_distinct_from_member_gender`,
  `tests/test_front_person_labels.py`.
- **Trans inclusion.** Trans women are women; trans men are men (Wikidata QID map
  in `pipeline/identity.py`). Intersex/third-gender are represented, not flattened.
- **Correctability.** Labels are cache rows keyed to a citation, and a cited local
  correction can override a stale claim. Automated re-reading of a corrected
  upstream source is not shipped because the CLI has no live enricher; corrupt
  rows that violate a guardrail still fail closed (`tests/test_cache_serde.py`).
  A *pending* correction — a person's filed note that a source has them wrong — is
  never removed without evidence: reconciliation requires an upstream source to
  have been consulted and to now assert the value that was proposed, a date-only
  change clears nothing, and a change to some other value marks the row superseded
  rather than deleting it (#70, 2026-08-14; before that, an ordinary demo refresh
  deleted the row and reported success).
- **Confidence is a tier, never a percentage.** `IdentityLabel.confidence` is an
  internal float used only to order/prioritise sources
  (`pipeline/identity.py::_SOURCE_BASE_CONFIDENCE`: 0.95 artist statement, 0.80
  Wikidata P21, 0.70 MusicBrainz gender); it is never rendered as a number. Any
  surface that shows identity confidence uses the qualitative tier vocabulary
  from `recommender/why.py::_confidence_tier`, derived from the actual citation:

  | Cited source | Rendered tier |
  |---|---|
  | Artist statement | "directly stated by the artist" |
  | Wikidata P21 | "recorded in Wikidata" |
  | MusicBrainz gender | "editorial database entry" |

  The numeric value cannot change the wording; no identity statement ever
  shows an unexplained number. → FIX-14.

## The values lens as a declared manifest (LensSpec), and the `Gender.OTHER` decision

The values lens is not a loose set of constants; it is a declared, inspectable
object: `recommender.lens.LensSpec` (fields: `name`, `aligned_genders`,
`max_boost`, `rationale`, `harms_note`), instantiated once as
`recommender.lens.VALUES_LENS`. `LensSpec.aligned()` evaluates the aligned
predicate over *sourced* fields only (an artist's sourced gender, or the sourced
genders of a band's front-people, intersected with the lens's own aligned set —
never via `female_fronted`, so lens policy can never rewrite a band's sourced
description) and `LensSpec.boost()` returns a bound,
non-negative boost — never a penalty. The dashboard renders the active lens's
`name` and `rationale` directly (`app/dashboard.py`), so "what does this lens
boost, and why" is answerable from the UI, not just from reading code.

**The `Gender.OTHER` question, decided explicitly.** `VALUES_LENS.aligned_genders`
is `{Gender.WOMAN, Gender.NONBINARY}` — it does **not** include `Gender.OTHER`.
This is a deliberate, documented decision, not an oversight:

- `Gender.OTHER` is a *sourced* self-identification outside the common
  vocabulary — a heterogeneous bucket that can include intersex people,
  third-gender identities, and other terms a source used that don't map to
  `WOMAN`/`MAN`/`NONBINARY`. These identities were never unified by anything
  other than "the vocabulary didn't have a better bucket for them."
- Folding that bucket into "aligned with the women-and-nonbinary lens" would
  make an unstated value claim on those artists' behalf — asserting they
  belong to a lens whose stated purpose (surfacing women and nonbinary
  artists) was never scoped to represent them.
- Excluding `OTHER` from this lens's aligned set keeps the lens honest about
  its actual purpose instead of silently expanding to cover identities it was
  never designed for.
- **This is revisable.** A dedicated lens for `OTHER`-sourced artists (or a
  broader "sourced marginalized gender" lens that explicitly opts them in) is
  a legitimate future `LensSpec` — but it is a *new* manifest with its own
  rationale and harms note, gated on a fresh identity-data-ethics review
  (this document), not a silent addition to the existing one.
- **Not boosted, and not displaced either.** Exactly like `UNKNOWN`, an artist
  sourced as `Gender.OTHER` keeps its exact base score *and* its exact pure-taste
  position: zero boost, no down-rank, no exclusion
  (`recommender/rerank.py::RANK_PROTECTED_GENDERS`,
  `recommender/exposure.py::assert_other_retained`).
  `tests/test_lens.py::test_lens_other_excluded`,
  `test_lens_other_is_not_penalised_like_unknown`, and
  `tests/test_rank_protection.py` lock this in.
  **Correction, 2026-08-14 (#68):** this bullet claimed the position half before
  the code did it. The re-rank pinned only `UNKNOWN` slots, so a sourced `OTHER`
  artist could be pushed below a *lower-scoring* unknown one. The ranking was
  changed to match the claim, and `test_lens_other_is_not_penalised_like_unknown`
  — which had asserted only that both boosts were `0.0`, and so stayed green
  throughout — now asserts the rank protection its name describes.

## Non-redistribution

This repo ships **no** bulk musician-identity dataset. Identity — on both axes —
is resolved on-demand from upstream sources and cached locally only
(`data/cache.db`, git-ignored). The second axis does not change the shape of
this commitment, but it raises the stakes: a local cache of who is queer or
trans is exactly the artifact that must never be published, shared, or exported,
and nothing in the codebase provides a path to do so. MusicBrainz/Wikidata content is CC0 (attribution given); Discogs is used
under its API terms. The worst-case misuse — a scraped "gender of musicians"
dataset — is structurally prevented: there is no export of identity data and the
cache is personal/local. See `LICENSE` (data note) and `privacy-notes.md`.

## Enforcement summary

| Commitment | Gate | Where |
|------------|------|-------|
| No inferred labels | auto | `tests/test_no_inference.py` |
| 100% sourced labels | auto | `tests/test_provenance.py` |
| Unknown never penalised | auto | `tests/test_unknown_first_class.py` |
| Female-fronted distinct | auto | `tests/test_identity_model.py` |
| Lens boost bounded, non-negative, OTHER excluded | auto | `tests/test_lens.py` |
| Identity-ethics framing | review | this document, sign-off on change |

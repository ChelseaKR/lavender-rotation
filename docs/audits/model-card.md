# Model Card — Lavender Rotation Recommender

> Instantiates AIEV-22. Consolidates content already committed across `docs/ROADMAP.md` §6-7,
> `docs/audits/identity-data-ethics.md`, and `docs/audits/fairness-identity.md` into one card.
> **Last verified: 2026-07-05 · Recheck cadence: per recommender or identity-model change.**

## Intended use

Given one person's Last.fm listening history, rank candidate artists by musical taste and,
optionally, a values-aware lens that boosts women/nonbinary/female-fronted artists. Intended for
personal, single-user use (see `docs/audits/privacy-notes.md`) — not a general-purpose or
multi-tenant recommendation service, and not a tool for classifying or scoring *people* (it ranks
*artists* for a *listener*, and never assigns an identity label to anyone not already sourced from
a citable, public source).

## Model type

A **classical hybrid recommender**, not a learned/trained model: a convex blend of a
play-weighted collaborative-filtering similarity signal (`recommender/collaborative.py`) and a
tag-cosine content signal (`recommender/content.py`), followed by a bounded, **boost-only**
values-aware re-rank (`recommender/rerank.py`). No LLM, no embeddings model, no gradient-trained
component anywhere in the pipeline — see the AI-evaluation status declaration in
`docs/RESPONSIBLE-TECH-AUDITS.md`.

## Inputs

- The user's Last.fm scrobbles and tags (`pipeline/lastfm.py`, cached locally).
- Enrichment evidence from MusicBrainz, Wikidata, Discogs, and cited artist statements, resolved through
  `pipeline/identity.py` into a sourced, cited `IdentityLabel` or `BandComposition` — never
  inferred from name, voice, image, or genre (see `docs/audits/identity-data-ethics.md`).

## The no-inference guarantee

The recommender's one hard invariant: **identity is never inferred, only sourced and cited.**
Enforced as a construction-time type invariant (`pipeline/models.py::IdentityLabel.__post_init__`
raises `UnsourcedIdentityError` on any non-unknown gender without a citation) and backstopped by an
AST-level regression test (`tests/test_no_inference.py`) that scans the resolver itself, not just
its output. "Unknown" is a first-class, neutral answer, never used to reduce, down-rank, or drop a
recommendation (`recommender/rerank.py`, `tests/test_unknown_first_class.py`) — see
`docs/audits/fairness-identity.md` for the fairness analysis.

## Evaluation

Offline, temporal hold-out evaluation against a popularity baseline
(`recommender/eval.py::evaluate`, `make eval`). Current numbers (demo fixture world, `k=5`; see
`docs/audits/eval-report.json` for the live-generated report and `docs/audits/eval-baseline.json`
for the committed regression baseline):

| Model | precision@5 | recall@5 | MAP@5 |
|-------|------------:|---------:|------:|
| Hybrid | 0.60 | 0.75 | 0.6875 |
| Popularity baseline | 0.20 | 0.25 | 0.0833 |

Two merge-blocking gates: the hybrid must beat the popularity baseline, and it must not regress
more than 10% (relative) below the committed baseline on any of precision/recall/MAP@5
(`recommender/eval.py::check_regression`, AIEV-26/27). A separate, computed exposure/fairness
report (`recommender/exposure.py`) tracks per-identity-segment exposure@k, rank-shift, and
unknown-retention@k (top-k presence, score, and rank) across a sweep of lens strengths — emitted into
`docs/audits/eval-report.json` under `fairness`.

**Caveat on scale:** the eval world today is a small, hand-built demo fixture spanning every
identity basis (`pipeline/demo.py`), not a live user's full history — see `docs/ROADMAP.md`
"Structural debt" notes on the live enrichment path. The metrics above demonstrate the *gate
mechanics* are real and merge-blocking; they are not yet a claim about performance at real-world
scale.

## Limitations

- **No live enrichment client** ships yet (`pipeline/enrich.py` has parsers and a fixture
  enricher, but no class that fetches live from MusicBrainz/Wikidata/Discogs) — see the ideation
  notes in `docs/ideation/` for planned follow-on work. The CLI and dashboard both currently run
  against the demo fixture world only.
- **Identity coverage is inherently sparse.** Wikidata P21 is sparse and sometimes wrong;
  MusicBrainz gender is editorial/self-reported. Many artists will correctly resolve to `unknown`
  — that is the intended, safe default, not a bug, but it does mean the values lens has less to
  work with for less-documented artists.
- **Allocational risk:** the lens is bounded and taste-preserving, but a boost-only design can
  still over-favour already-popular sourced women within the boosted set (see
  `docs/audits/fairness-identity.md` §3 for the mitigation and its limits).

## Environmental footprint

N/A — no training run exists (AIEV-24 N/A-with-reason). The recommender is closed-form arithmetic
over a small candidate set; there is no GPU/training compute to account for.

## Framing-review sign-off

**Pending.** `docs/RESPONSIBLE-TECH-AUDITS.md` §D names a review-gated "framing review"; the
structural transparency guarantees (why + identity basis + raw source value on every
recommendation, `inferred = False` explicit) are code-enforced today, but a dated maintainer
sign-off specifically on this card's framing has not yet been recorded. Track alongside the
accessibility walkthrough sign-off (`docs/audits/accessibility-2026-05-31.md`) as a pre-release
review item.

# 0007. Identity is sourced-only, with unknown as the first-class default

Date: 2026-07-17

## Status

Accepted (backfilled record of the founding 2026-05-31 decision; see ADR 0000 on backfilling)

## Context

This is the repo's founding constraint, until now recorded only in `docs/ROADMAP.md` §6 and the
README rather than as a dated ADR. An identity-aware recommender could obtain gender labels two
ways: infer them (from name, voice, image, or genre signals) or source them (from citable
assertions). Inference is both unethical (misgendering as a systemic output; a de facto gender
classifier over real people) and inaccurate; a sourced-only model instead leaves most artists
unlabeled — Wikidata P21 coverage is sparse — so "unknown" becomes the *common* case and must not
be treated as a defect or a penalty.

## Decision

Identity labels come only from permitted, citable sources (Wikidata P21, the MusicBrainz gender
field, a cited artist self-statement — `PERMITTED_SOURCES` in `pipeline/models.py`); there is
deliberately no inference path. Rejected alternative: any name/voice/image/genre-derived labeling.
The rule is enforced as a *type invariant*, not a convention — an unsourced `IdentityLabel` cannot
be constructed — with merge-blocking proofs in `tests/test_no_inference.py` (vocabulary,
structure, AST, and behavioural legs). "Unknown" is first-class: the values lens is boost-only
(`recommender/rerank.py`), so an unknown-identity pick can never be down-ranked, dropped, or
reduced (`tests/test_unknown_first_class.py`), and `female_fronted` is tri-state (`True`/`None`,
never `False` by inference) and narrow — it asserts only that a front-person's *own* sourced
gender is `WOMAN`. Every other sourced front-person gender is carried, unflattened, by
`BandComposition.sourced_front_genders` (`tests/test_front_person_labels.py`).

## Consequences

- The lens can only ever *add* visibility for sourced identities; taste ordering is preserved for
  everyone else, bounded by `MAX_BOOST`.
- Coverage is honestly limited by upstream sourcing; the product surfaces "unknown — surfaced on
  similarity" as a normal state and reports per-run identity coverage instead of guessing.
- Every feature touching identity inherits this ADR as its acceptance criterion (safety suite runs
  on every PR); the export-schema tests (`tests/test_export_schema.py`) extend the same invariant
  into the export stream so no portable file can carry an identity field.

# 0006. Hybrid recommender over a single-method recommender

Date: 2026-07-17

## Status

Accepted (backfilled record of the 2026-05-31 build decision; see ADR 0000 on backfilling)

## Context

A purely collaborative recommender (Last.fm similar-artist signal) cold-starts badly on sparse
profiles and over-concentrates on popular neighbors; a purely content-based one (tag/genre
similarity) recycles the listener's existing taste and rarely surprises. The original decision is
recorded in prose in `docs/ROADMAP.md` §6 ("Hybrid over single-method — cold-start + serendipity").

## Decision

Score candidates with a hybrid of the collaborative similar-artist signal and content (tag)
similarity (`recommender/hybrid.py`), and keep the values lens as a separate, strictly boost-only
re-rank layer on top (`recommender/rerank.py`) rather than folding identity into the base score.
The hybrid must *prove* it earns its complexity: the merge-blocking offline eval gate
(`make eval`, FIX-06 multi-world design) fails any change where the hybrid stops beating the
popularity baseline on aggregate, and the AIEV-26 regression gate fails silent metric erosion
against the committed baseline (`docs/audits/eval-baseline.json`).

## Consequences

- Cold-start and serendipity weaknesses of each single method offset each other; the demo world
  and eval fixtures exercise both signals independently (`tests/test_eval_worlds.py`).
- Taste scoring and the values lens stay separable, which is what makes the per-pick rank-shift
  counterfactual ("would this artist rank here without the lens?") computable and testable.
- The complexity is permanently on trial: if a simpler method ever beats the hybrid in the eval
  gate, this ADR should be superseded rather than the gate adjusted.

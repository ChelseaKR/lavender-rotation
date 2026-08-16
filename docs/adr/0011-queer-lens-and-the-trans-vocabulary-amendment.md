# 0011. A queer lens, and the trans-vocabulary guardrail this amends

Date: 2026-08-16

## Status

Accepted. **Amends ADR 0007 (sourced-only identity, unknown first-class)** — which stands
unchanged in every other respect — and amends the "no cis/trans distinction exists in the
vocabulary" guardrail in `README.md`, `CLAUDE.md`, and `pipeline/models.py`.

## Context

The project's lens surfaces sourced women and nonbinary artists. The maintainer asked to refocus
it on **queer women and nonbinary people specifically**. That is not a new value in the `Gender`
enum; it is a second identity axis, and building it forces two decisions that the existing
guardrails had settled in the opposite direction.

**The vocabulary deliberately could not express "trans".** `Gender` maps a sourced *trans woman*
to `WOMAN` and stops there. That was the point: "sourced self-identification is the only test, and
no cis/trans distinction exists anywhere in the vocabulary." A model that cannot represent who is
trans cannot be turned into a list of who is trans — the strongest possible defence against the
worst thing this repo could produce.

**Sexual orientation was absent entirely.** No `SourceKind` could carry it. MusicBrainz has no
orientation field and neither does Discogs, so the only upstreams are Wikidata's P91 claim and a
cited public statement by the artist.

A lens for *queer* women and nonbinary people needs both axes. Under the narrower reading —
orientation only — the trans guardrail survives untouched, and a trans lesbian is surfaced for
her sourced orientation while nothing records that she is trans. The maintainer chose the broader
reading: trans self-identification is itself in scope for this lens.

## Decision

**1. A second lens, not a replacement.** `QUEER_LENS` ships alongside `VALUES_LENS`, selected per
run (`--lens-name`). `lens.py` already anticipated this ("a new manifest with its own rationale
and harms note"). The existing, well-covered lens keeps working while the sparser one matures.

**2. Two new sourced facts, both first-class-unknown.** `Orientation` (a controlled vocabulary
over what a permitted source asserted) and `QueerIdentity.trans_self_identified`, a **tri-state**
that is `True` only when sourced and otherwise `None`. It is never `False`, for the same reason
`BandComposition.female_fronted` is never `False`: the absence of a claim is not the negation of
one, and "not recorded as trans" must never be readable as "recorded as cis".

**3. The trans signal is read, not collected.** This is the narrowest form the maintainer's choice
can take, and the reason it is defensible. The raw value each source asserted has always been
retained for provenance (`Source.detail`), so a Wikidata P21 claim of `Q1052281` (*trans woman*)
is *already* in every cache this project has ever written — mapped to `WOMAN` for the label and
otherwise ignored. This change adds no new fetch, no new field upstream, and no new question
asked of anybody. What changes is that a lens may now read a value the cache already held.

`Gender` itself is untouched: a trans woman is still `Gender.WOMAN`, with no cis/trans distinction
drawn *in the gender vocabulary*, and every existing guarantee about her label is unchanged.

**4. Wikidata P91 is admitted, at lower trust, and rendered distinctly.** P91 frequently encodes a
biographer's or journalist's claim rather than the artist's own words. It is accepted for coverage
— self-statements alone would surface almost nobody — but it ranks below `ARTIST_STATEMENT` in the
resolver, and the why-card says "recorded in Wikidata" versus "stated by the artist", with the raw
asserted value shown either way. A reader can always see whether the artist said it.

**5. Nonbinary artists are in scope on gender alone.** No second, rarer disclosure is demanded of
the most sparsely documented group.

**6. Asexuality and demisexuality are recorded but not aligned by default.** This follows the
`Gender.OTHER` precedent exactly: faithfully stored, never folded into a lens whose stated purpose
did not scope them, revisable by a one-line change with its own rationale rather than silently.

## Consequences

**The repo now holds a queerness dataset.** That is the honest description, and it is a
step-change in sensitivity: sexual orientation is GDPR Art. 9 special-category data, and being
catalogued as queer or as trans is dangerous for real people in much of the world. The existing
defences carry over and are now load-bearing rather than precautionary — sourced-only with a
citation, unknown first-class, no export of identity data (`tests/test_export_schema.py`),
local-only cache, corrections ledger. `privacy-notes.md` can no longer claim no special-category
data is stored; it now states what is stored and why.

**The structural defence is weaker than it was.** Before, "we cannot produce that list" was true
of the type system. Now it is true of the *process* — no export path, local cache, no
redistribution — which is a real defence but a weaker kind. The type system no longer refuses.

**Coverage will be sparse and skewed.** Of 354 artists in the maintainer's cache, 48 have a
sourced woman/nonbinary gender; intersecting with sourced queerness leaves single digits. Sparse
coverage skews toward the well-documented — the already-famous, the Anglophone, the living, the
out — which is the opposite of who a discovery tool should favour. `unknown` must therefore never
read as "not queer", in the UI or in anyone's head, and the lens boosts rather than filters so an
unknown artist keeps their place on musical merit.

**Bands are gender-only for now.** `FrontPerson` carries an `IdentityLabel` and no orientation, so
the queer lens reaches a band only through a sourced nonbinary front-person. Extending lineup
resolution to the second axis is deferred, and named here so it is a known gap rather than a
silent one.

## Alternatives considered

**Orientation only, leaving the trans guardrail intact.** Recommended at the time of the decision
and declined. It would have kept "the vocabulary cannot express who is trans" true, at the cost of
a lens that excludes trans women who have not publicly discussed their orientation.

**Filtering instead of boosting.** Rejected for the same reason `--hide-sourced-men` removes only a
positive sourced claim: filtering on "not aligned" deletes every unknown artist, and here that is
most of the catalogue.

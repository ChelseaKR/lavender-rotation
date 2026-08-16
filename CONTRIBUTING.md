# Contributing to Lavender Rotation

Thanks for your interest. Lavender Rotation is an independent personal open-source project
(MIT, unaffiliated with any employer or client). It is a *values-aware* recommender, and the whole
point of the repo is that its responsible-AI posture is **mechanically enforced rather than
asserted**. Please read this before opening an issue or a pull request — one invariant, the
sourced-not-inferred identity rule, is non-negotiable and shapes how code and tests are written.

If you have not yet, read [`README.md`](README.md) for what the project is and why, and
[`SECURITY.md`](SECURITY.md) for how to report a vulnerability. The
[Code of Conduct](CODE_OF_CONDUCT.md) applies to every interaction.

This repo's gate set is derived from [`/STANDARDS`](../STANDARDS/) (see README's "Standards
Conformance" table for the per-standard declaration); `make verify` below is this repo's concrete
instantiation of those gates.

## The identity invariant (read this first)

The recommender attaches identity claims to real artists. Every contribution must respect three
rules; a change that weakens any of them will not be merged:

- **Identity is sourced, never inferred.** An identity label may come *only* from a cited
  self-identification source — an artist statement, a sourced Wikidata `P21` claim, or the
  MusicBrainz gender field — and it must carry that citation through to the UI. Never infer gender
  or identity from a name, a voice, an image, a genre, or any heuristic.
- **"Unknown" is first-class.** An unsourced identity is a normal, neutral answer. It must never
  reduce, down-rank, or drop a recommendation; unknown artists are surfaced on musical merit alone.
- **"Female-fronted" is band-composition metadata**, sourced from lineup/role data — not guessed —
  and kept distinct from any individual's gender.

Practically: new tests assert these properties on the surfaces they touch, and identity data is
kept minimized, cited, and correctable. Do not add or redistribute a scraped musician-identity
dataset.

## Getting set up

The project targets **Python 3.12+**. One command installs everything into a local `.venv`:

```bash
make install        # venv + editable install with dev + app extras
make dev            # run the Streamlit dashboard in demo mode (no API key needed)
```

Run `make help` to see every target. API credentials (Last.fm, Spotify OAuth) are supplied via
**environment variables only** and are never committed.

## The one command that proves it: `make verify`

```bash
make verify         # the full local mirror of the CI gate set
```

`make verify` runs, in order, the same merge-blocking gates CI enforces — a change is not done
until it is green locally:

| Gate | Command | What it checks |
| --- | --- | --- |
| Lint | `make lint` | `ruff format --check` + `ruff check` (incl. the bandit SAST subset) |
| Type | `make typecheck` | `mypy --strict` over `pipeline`, `recommender`, `app`, `export` |
| Test | `make test` | `pytest` with a **≥ 85%** coverage gate on core logic |
| Security | `make security` | `pip-audit` (empty waiver list) + the secret scan |
| A11y | `make a11y` | renders the dashboard and runs the axe gate — **0 violations** |
| Eval | `make eval` | offline eval; fails unless the hybrid **beats the popularity baseline** |

CI re-runs the same `make` targets on Python 3.12–3.13; green locally means green in CI. Useful
extras: `make format` (auto-format) and `make audit` (regenerate the committed responsible-tech
artifacts under `docs/audits/`).

The **accessibility gate is merge-blocking**: any rendered surface must pass axe with zero
violations. The manual screen-reader walkthrough is a review-gated sign-off recorded under
`docs/audits/`.

## Commit style: Conventional Commits + sign-off

This repository uses [Conventional Commits 1.0.0](https://www.conventionalcommits.org/) and
requires a **Developer Certificate of Origin** sign-off on every commit:

```
<type>(<scope>): <imperative summary>

<body — what & why, not how>

Signed-off-by: Your Name <you@example.com>
```

Sign off with `-s` so the certification is on record; `git commit --amend -s` (or
`git rebase --signoff main`) fixes a forgotten trailer:

```bash
git commit -s -m "feat(recommender): weight sourced-identity signal in re-rank"
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `perf`, `revert`. Common
scopes mirror the packages: `pipeline`, `recommender`, `export`, `app`, `eval`, `a11y`, `deps`. By
signing off you certify you wrote the contribution, or have the right to submit it under the
project's MIT license, and that it contains no proprietary or client material.

## Pull requests

Open a PR against `main` (the protected, CI-gated branch; no admin bypass). Before requesting
review:

- [ ] `make verify` is green locally (lint · type · test ≥85% · security · a11y · eval).
- [ ] Tests added or updated for the change, including the identity invariants above where a read
      or ranking path is touched.
- [ ] Every recommendation surface still shows **why + identity basis + source**, with the raw
      value each source asserted (never inferred).
- [ ] The eval report is regenerated if behavior changed (`make eval`) and still beats the
      popularity baseline.
- [ ] Docs updated to match the change.

Keep PRs focused and explain the *why* in the description. Review looks hardest at anything near an
identity label, a ranking signal, or the export egress.

## Fix it at the source

Every identity claim the tool shows already links back to its citation. When a why-card's
provenance is wrong or stale, the honest fix is to correct it **at the source it came from** —
never to quietly override it locally. Where a citation resolves to a known upstream edit surface
(`recommender/upstream.py::upstream_edit_url`), the provenance list carries a labelled **"Fix at
source"** link next to it:

- A Wikidata `P21` ("sex or gender") citation links to the entity's own page, anchored at the P21
  statement (`https://www.wikidata.org/wiki/{Qid}#P21`) — Wikidata has no query-string that opens
  a pre-filled edit form for a specific claim, so this is the safe, honest anchor rather than a
  fabricated deep link.
- A MusicBrainz gender or relationship citation links to that artist's `/edit` page.
- Anything else (a Discogs-lineup-only citation, or a citation that doesn't parse) gets **no**
  link — never a guessed one.

Clicking the link opens the upstream site's own edit UI in your browser; nothing in this project
ever writes to Wikidata or MusicBrainz on your behalf. If you note what you're proposing and why,
file it locally with `lavender corrections add --artist <id> --source-kind <kind> --citation <url>
--proposed <value> --note <why>` (`pipeline/corrections.py`) — a small JSON file next to the local
cache, never sent anywhere. `lavender corrections` lists what's pending.

The round-trip closes itself: make the real edit upstream, then run `lavender refresh`. It re-enriches
the cache, reports any identity-source change it observes (a new `retrieved_at` is the signal an
edit landed), and reconciles — clearing — any pending correction whose `artist_id` + `source_kind`
matches.

**TODO (tracked, not yet done):** a real, documented round-trip — a local note filed against a
genuinely stale Wikidata `P21` claim, the actual edit made on wikidata.org, and a `lavender refresh` run
showing the pending correction reconciled — is EXP-05's excellence bar and is a human follow-up
outside of code (it requires an account and a real edit on live Wikidata). Record it under
`docs/audits/` once completed.

## Reporting bugs and security issues

- **Security or any identity-sourcing / unknown-handling defect:** do **not** open a public issue.
  Follow [`SECURITY.md`](SECURITY.md) for private, coordinated disclosure — those defects are
  treated as first-class security bugs.
- **Ordinary bugs and taste disagreements:** open a normal GitHub issue.

## License

By contributing, you agree that your contributions are licensed under the project's
[MIT](LICENSE) license. You must have the right to release what you contribute.

---

*Maintainer: Chelsea Kelly-Reif · License: MIT*

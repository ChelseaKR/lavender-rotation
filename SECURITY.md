# Security Policy

Women-Artist Discovery is an independent personal open-source project (AGPL-3.0-or-later). It is **local-first**:
your Last.fm listening history stays on your machine, there is no auth and no server-side account,
and the only opt-in egress is a user-initiated Spotify playlist export (artist names only). Because
the app reads a person's listening data and asserts **sourced identity claims about real artists**,
both a conventional software vulnerability *and* an identity-handling defect are in scope here.

## Supported versions

**Release stance (2026-07-05): unreleased pre-1.0 development line.** `pyproject.toml` carries
version `0.1.0` as the in-development source version, but **no git tag and no GitHub release exist
yet** — there is no "0.1.x release line" to support. Until the first tag is cut, only `main` is
supported and security fixes land as new commits.

| Version         | Supported | Notes                                                                 |
|------------------|-----------|------------------------------------------------------------------------|
| `main` (unreleased) | ✅ Yes | Only supported line today; no tagged release exists.                    |
| any `0.1.x` tag  | N/A       | Not applicable yet — will apply once `v0.1.0` is tagged (see CHANGELOG.md). |

Once a `v0.1.0` tag is cut, this repo intends the usual pre-1.0 policy: only the latest minor on
the latest major receives security fixes, shipped forward in a new patch (no re-publish of a
version); this table will be updated in the same release. See `CHANGELOG.md` for what would go
into that first release and `CITATION.cff` for the citation stance in the meantime.

## Reporting a vulnerability

**Please do not open a public GitHub issue, PR, or discussion for a security report.**

Report privately, by either:

1. **GitHub Security Advisory** — open a draft advisory via *Security → Report a vulnerability* on
   the repository (preferred; keeps the report, fix, and GHSA linked), **or**
2. **Email** — `ckellyreif@gmail.com` with subject `SECURITY: women-artist-discovery`.

Please include, as far as you can:

- affected version / commit and the surface (local pipeline, the Streamlit dashboard, the export
  path, or an upstream data-source seam),
- a minimal reproduction or proof-of-concept,
- the impact you believe it has, and
- any suggested remediation.

If you want an encrypted channel, say so in a first low-detail email and we will arrange one.

## What we consider a vulnerability

In addition to the usual (secret exposure, injection, SSRF against the data-source clients, an
export path leaking more than artist names), the following identity-handling defects are
**first-class** security bugs, not merely quality bugs, because the project's core promise is that
identity is *sourced, never inferred*:

- **Any path that assigns or displays an artist identity label without a citation** to a
  self-identification source (an artist statement, a sourced Wikidata `P21` claim, or the
  MusicBrainz gender field) — i.e. any inference from name, voice, image, genre, or heuristic.
- **Any path where an "unknown" identity is used to reduce, down-rank, or drop a recommendation.**
  "Unknown" is a first-class, neutral answer; treating it as a demotion signal is a defect.
- **Any conflation of "female-fronted" band-composition metadata with an individual's gender**, or
  a female-fronted label that is guessed rather than sourced from lineup/role data.
- **Any redistribution of a scraped musician-identity dataset**, or a data path that fails to keep
  identity data minimized, cited, and correctable.

## Our commitments

| Stage                    | Target                                                            |
|--------------------------|------------------------------------------------------------------|
| Acknowledgement & triage | **≤ 72 hours** from receipt (volunteer project — please be patient) |
| Severity assessment      | shared with the triage reply                                      |
| Fix or mitigation plan   | communicated after triage, prioritized by severity                |
| Coordinated disclosure   | by mutual agreement; default embargo up to 90 days                |
| Credit                   | named in the advisory unless you prefer to remain anonymous       |

Identity-sourcing and unknown-handling defects are fixed with the highest priority. A fix ships
*forward* in a new patch release.

## Scope

In scope: the `pipeline`, `recommender`, `export`, and `app` packages; the `make verify` gate set
(lint, `mypy --strict`, tests, `pip-audit`, secret scan, the axe accessibility gate, and the eval);
and the dependency supply chain (`pip-audit` + gitleaks in CI). Out of scope: recommendation
*taste* disagreements and accessibility regressions are quality issues (the latter is a
merge-blocking `axe = 0` gate, not a vulnerability) — file those as normal issues.

## Hardening notes for self-hosters

- Keep the default **demo / local** mode unless you have a reason not to; it needs no API key and
  makes no network calls beyond the data-source lookups you initiate.
- Supply any API credentials (Last.fm, Spotify OAuth) via **environment variables only** — secrets
  are never committed, and the secret scan blocks them in CI.
- Run `make security` (dependency audit + secret scan) before deploying and keep dependencies on
  the pinned `uv.lock`.

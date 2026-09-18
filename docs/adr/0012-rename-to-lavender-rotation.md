# 0012. Rename the project to Lavender Rotation

Date: 2026-08-16

## Status

Accepted

## Context

[ADR 0011](0011-queer-lens-and-the-trans-vocabulary-amendment.md) refocused the project on queer
women and nonbinary artists. "Women-Artist Discovery" — a name that was already more of a
description than a name — had stopped describing it.

Two properties mattered more than cleverness. The name should carry the *mechanism or the values*
rather than the dataset: a repo called something like "queer-musician-database" would advertise
precisely the artifact `identity-data-ethics.md` exists to prevent, and would put a category of
people in a URL that shows up in browser history, CI logs, and anyone's `~/src` listing. And it
should be a name, not a summary — the old one had to be re-read every time to work out whether it
was a product or a topic.

## Decision

**Lavender Rotation.** Lavender has been a queer signifier for a century (the Lavender Menace, the
lavender scare); *rotation* is what a record earns when it is played on repeat. The pairing says
music and says who this is for, without naming anyone's identity in the repository name.

The rename is complete rather than cosmetic, because a half-rename leaves two vocabularies in the
codebase forever:

| Surface | Was | Is |
|---|---|---|
| Project / repo | `women-artist-discovery` | `lavender-rotation` |
| CLI | `wad` | `lavender` (`wad` kept as a deprecated alias) |
| Env vars | `WAD_*` | `LAVENDER_*` (`WAD_DATA_DIR` still read) |
| Log namespace | `wad.*` | `lavender.*` |
| Data directory | `…/wad` | `…/lavender-rotation` |

**The data directory migrates; it does not reset.** A cache holds hours of rate-limited upstream
fetching — on the maintainer's machine at the time of the rename, 95,613 scrobbles and 450
enriched artists. Losing that to a rename would be a self-inflicted wound, and worse, it would mean
re-fetching from MusicBrainz what we had already politely been given once.
`pipeline.paths.migrate_legacy_data_dir` moves the old directory to the new name on first use,
under four conditions that make it safe: only when the new directory does not exist (so a real
cache is never overwritten or merged), only when the old one does, never when an env var names a
path explicitly, and via a same-filesystem rename that is atomic and undone by moving it back. A
failed move logs and degrades to a working empty directory rather than crashing at startup.

**Backward compatibility is time-boxed.** The `wad` console script and the `WAD_DATA_DIR` env var
still work, both marked deprecated, both to be removed at the first tagged release. They exist so
an operator's shell profile, scripts, and muscle memory survive the change — not as a permanent
second spelling.

## Consequences

GitHub redirects the old repository URL, so existing clones, links, and the two open pull requests
keep resolving; a local `git remote set-url` is still worth doing to avoid depending on the
redirect. `CITATION.cff`'s `title` changed, which matters for anyone citing the work — the version
and commit-hash guidance in that file is unchanged, and no release has been tagged, so no published
citation is invalidated.

Pre-rename ADRs and `docs/ideation/` working notes keep the old names. They are dated records of
what was decided when, and rewriting history to match a later decision would make them useless as
history. `CHANGELOG.md`'s existing entries likewise stay as written.

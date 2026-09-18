# Claude Code guide — lavender-rotation

- **Build entrypoint:** [`docs/ROADMAP.md`](./docs/ROADMAP.md) → *Implementation Plan*.
- **Hard guardrails:** the [Guardrails section of the README](./README.md#guardrails)
  is binding — never infer identity from name/voice/image/genre or any heuristic;
  woman includes trans women explicitly (sourced self-identification is the only
  test, and the `Gender` vocabulary draws no cis/trans distinction — a trans
  woman is `Gender.WOMAN`, full stop); "unknown" is first-class and never
  down-ranked; "female-fronted" is band-composition metadata, distinct from any
  individual's gender; every recommendation shows why + identity basis + source;
  never redistribute a scraped musician-identity dataset.
  `tests/test_no_inference.py` is the enforcement centrepiece — never weaken it.
- **Amended by [ADR 0011](./docs/adr/0011-queer-lens-and-the-trans-vocabulary-amendment.md):**
  this guardrail used to read "no cis/trans distinction exists in the
  vocabulary", full stop. It is now narrower and the difference matters. `Gender`
  is unchanged and still cannot express it, but a *separate* sourced axis
  (`QueerIdentity`) records a trans self-identification when a permitted source
  asserted one, for the queer lens. It is tri-state and can never be `False`:
  "not recorded as trans" must never be readable as "recorded as cis". Nothing
  is fetched to populate it — it reads the raw asserted value the cache already
  stored for provenance. Read the ADR before touching either axis.
- **Commands:** `make dev` · `make verify` · `make a11y` · `make eval`.
- **Definition of done:** demo recommendations are explainable and reproducible,
  sourced identity is enforced, unknown is retained, and every local gate is
  green (see `DEFINITION_OF_DONE.md` for the full checklist).
- **Live mode is wired, not deferred.** `lavender ingest --user` (FIX-01, #86)
  syncs a real history end to end, and `lavender refresh --user` re-asks upstream
  and reconciles the corrections ledger. Both are opt-in; without `--user`
  nothing opens a socket. Scheduling ships too (ADR 0013): `make schedule` prints
  a launchd/cron entry that runs `make refresh` weekly on the operator's own
  machine. Never propose a GitHub Actions cron for it — a hosted runner has no
  cache to refresh, and giving it one means uploading a listening history to CI.
- **The refresh invariant:** an upstream that says nothing is not an upstream
  that agrees. `MusicBrainzEnricher` renders every fetch failure as "no
  evidence", so on any path that *overwrites* a stored label, an empty
  re-enrichment must never be written and must never advance `fetched_at`. See
  `pipeline.ingest.RefreshOutcome` — `upstream_answered` is proof a citation came
  back, not proof we tried.

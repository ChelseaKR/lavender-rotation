# Residual-Risk Register

> Instantiates RESPONSIBLE-TECH-AUDITS §F (security narrative + residual risk).
> **Last verified: 2026-07-03 (Spotify OAuth hardening — FIX-08 landed) · Recheck cadence: per dependency / threat-model change.**

## Threat model (STRIDE-lite) of the data flows

`username → Last.fm API → cache → enrichment APIs → resolver → recommender → UI`

| Threat | Vector | Control |
|--------|--------|---------|
| Tampering | cache poisoning via malformed external API data | strict shape validation in all parsers (`pipeline/lastfm.py`, `pipeline/enrich.py`); unrecognised values → unknown, never coerced; `tests/test_adapters.py` |
| Tampering | corrupt cache row asserting an unsourced identity | model invariants re-run on load — fails closed (`tests/test_cache_serde.py::test_corrupt_cache_row_*`) |
| Info disclosure | API key leakage | key from env only; secret scan merge-blocking (`scripts/secret-scan.sh`, CI gitleaks) |
| Info disclosure | listening data exfiltration | local-first; no telemetry; network confined to the API client (`tests/test_privacy.py`) |
| Spoofing / CSRF | authorization-code interception or forged `state` in the Spotify export OAuth flow | PKCE (S256 challenge/verifier, `PkcePair`) binds the token exchange to the party that started the flow; `state` is generated per session and verified on return (`parse_redirect` raises `ExportError("OAuth state mismatch — possible CSRF")` on mismatch — a tested failure path, not just a generated-but-unchecked value); the redirect is captured by a loopback listener bound to `127.0.0.1` (`capture_redirect`) rather than requiring the user to hand-copy a bare code across tabs (`export/spotify.py`, `tests/test_export.py`) |
| DoS / abuse | hammering upstream APIs | `RateLimiter` + response cache (`pipeline/lastfm.py`, `tests/test_adapters.py`) |
| Elevation / supply chain | vulnerable dependency | `pip-audit` merge-blocking; ruff bandit (`S`) SAST subset |

## Accepted residual risks

| ID | Risk | Why accepted | Owner | Review |
|----|------|--------------|-------|--------|
| ~~RR-1~~ **RESOLVED 2026-06-30** | **CVE-2025-8869 / GHSA-4xh5-x5gv-qwph** — pip fallback tar extraction | Cleared by the Python 3.10+ migration: the floor is now ≥3.10, where pip ≥25.3 enforces the PEP 706 tar data filter; the installed pip (≥26.1.2) is no longer reported by `pip-audit`. Un-ignored per this row's own "un-ignore once a fix ships" policy — no longer in `AUDIT_IGNORES` / `ci.yml`. | maintainer | closed (re-open only if a new pip advisory appears) |
| RR-2 | Upstream identity sources can be **wrong or stale** | Surfaced honestly: confidence is hedged on source conflict; basis + citation + fetch date are shown. A cited local correction can override the cache, but automated upstream re-enrichment is still open because `lavender refresh` is demo-only. | maintainer | per source-API change |
| RR-3 | Live performance / load testing **not gated** | This is a local-first, single-user data app with no hosted LLM/API route, so the standard's web/API latency + Lighthouse budgets do not apply. Recorded as a deliberate scope decision. | maintainer | if a hosted multi-user mode is added |
| ~~RR-4~~ **RESOLVED 2026-06-30** | **Python-3.9-EOL advisory cluster** — 19 advisories across `requests`, `urllib3`, `streamlit`, `pillow` (×6), `pyarrow`, `msgpack`, `filelock` (×2), `pytest`, `pip` (×3) | **Remediated by the Python 3.10+ migration** (`requires-python = ">=3.10"`; ruff/mypy `target = py310`; CI matrix 3.10–3.13; 3.9 dropped — EOL 2025-10-31). Each advisory's fix declared `Requires-Python >=3.10`; with the floor moved, a clean resolution now installs every fixed version — `requests>=2.33`, `urllib3>=2.7`, `streamlit>=1.54`, `pillow>=12.2`, `pyarrow>=23`, `msgpack>=1.2.1`, `filelock>=3.20.3`, `pytest>=9.0.3`, `pip>=26.1.2` — pinned reproducibly in `uv.lock`. `pip-audit` reports **0 findings with no `--ignore-vuln` flags**; all 19 IDs were removed from `Makefile` (`AUDIT_IGNORES`), `.github/workflows/ci.yml`, and `docs/audits/vex.json`. The migration was validated on its own (lint, `mypy --strict` on the py310 target, tests + coverage, a11y axe=0, eval) — the sourced-never-inferred-identity and unknown-first-class invariants are unchanged. | maintainer | closed (quarterly re-audit) |

## Enforcement summary

| Commitment | Gate | Where |
|------------|------|-------|
| No high/critical dep vulns (waiver list now **empty** — RR-1 + RR-4 resolved by the Python 3.10+ migration; `pip-audit` passes with 0 findings and no `--ignore-vuln`) | auto | `make security` / CI |
| Input validation on external data | auto | `tests/test_adapters.py` |
| Fail-closed on guardrail-violating cache rows | auto | `tests/test_cache_serde.py` |
| Threat-model sign-off | review | this document |

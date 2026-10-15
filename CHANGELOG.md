# Changelog

All notable changes to **clawflow** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] — 2026-10-16

### Fixed
- **`cryptojobs_sitemap` was discovering the sitemap index but finding zero
  jobs.** CryptoJobsList split their `sitemap.xml` into 9 per-section sitemaps
  in 2026; jobs now live in `sitemap-jobs.xml`. Updated the URL constant.
- **`wordpress_cve` was producing zero leads** because two things changed
  on the upstream side:
  - The old `wpsiapi.com/v1/plugins/{slug}` endpoint went NXDOMAIN. Found
    the new canonical endpoint via DNS probe + shape inspection:
    **`https://www.wpvulnerability.net/plugin/{slug}`** (singular `plugin`,
    no `/api/v1/` prefix).
  - The response body shape changed from a flat `{"vulnerabilities": [...]}`
    to `{"error": 0, "data": {"vulnerability": [...]}}` (singular key, nested
    inside `data`). The parser now unwraps the envelope and looks up the
    CVE id from `source[0].id` (the API moved CVEs from a top-level
    `cve` field into a `source` array per vulnerability).
  - The redirect from `wpvulnerability.net` → `www.wpvulnerability.net` was
    being followed but the JSON-decode step was rejecting the raw 301's
    HTML body. Now uses the `www.` host directly so no redirect happens.

### Verified
- One cycle now captures **234 leads** from `wordpress_cve` alone (Elementor
  alone returns 64 valid vulnerabilities via the new endpoint).
- All 34 tests pass.

## [0.1.1] — 2026-10-16

### Fixed
- **`github_bounties` was producing zero leads** even when the configured
  search returned hundreds of issues. Root causes, all addressed:
  - `clawflow auth github --token X` was saving tokens to `auth.json`, but
    the source only consulted `os.environ.get("GITHUB_TOKEN")` — wires the
    Bearer header through `clawflow.auth.get_secret()` as a fallback.
  - `SourceConfig` is a generic dataclass with an `extra` dict. The source
    used `getattr(cfg, "languages")`, which silently returned the default
    value because the dataclass has no such attribute. Now reads from
    `cfg.extra`.
  - The 3-label `OR` clause `("💎 Bounty" OR bounty OR "help wanted")` is
    treated as AND-of-OR by GitHub search (verified via direct probe: 2
    issues vs 0). Reduced to a single label, post-filtered in scoring.
  - GitHub's GraphQL search drops all results when a `language:` qualifier
    is in the query string. Now does a label-only query and post-filters
    by `repository.primaryLanguage` in code.
  - GraphQL response was silently dropping issues because the body didn't
    select `issueCount`. Without it, GitHub returns `data: null` on union
    errors and the source treated it as zero results.
  - `_node_to_lead` was passing the GraphQL-shaped `{"nodes": [...]}`
    directly to `raw_labels[0]`, raising `KeyError(0)`. Now unwraps both
    REST (`[{"name": ...}]`) and GraphQL shapes.

### Tests
- Updated `test_github_bounties_parses_issue_and_scores_by_bounty` to
  build the right `SourceConfig` shape via `dataclasses.replace()` and
  monkeypatch `clawflow.auth.get_secret` so the test exercises the
  REST path the mock transport expects.

### Verified
- `clawflow run --once` now actually surfaces leads end-to-end.
  Confirmed a live capture: a `💎 Bounty` issue in
  `limitlessprojects/Go-Hybrid` (Rust) made it through dedup and into
  `state.json` + `clawflow leads`.

## [0.1.0] — 2026-10-15

### Added
- Initial public release (vertical slice).
- CLI entry point `clawflow` with subcommands: `init`, `sources list|add|remove`,
  `run` (with `--once`), `status`, `leads`, `dashboard` (stub).
- Pluggable source registry with four built-in sources (off by default):
  - `github_bounties` — GitHub issues via the GraphQL API.
  - `web3_sitemap` — Remote3 RSS + CryptoJobsList sitemap.
  - `wordpress_cve` — WPVulnerability.net per-plugin CVE feed.
  - `hn_freelancer` — Hacker News (Algolia) + Reddit `r/forhire`.
- Three built-in alerters: `console`, `telegram`, `email` (SMTP).
- YAML configuration loader (`clawflow init` generates a documented starter file).
- Atomic JSON lead store (`state.json`) with content-hash deduplication.
- Per-source severity thresholds for the daily digest.
- Async scheduler with graceful SIGINT, two-phase collect/alert cycle,
  and per-cycle time budget.
- Smoke-test suite covering Lead dataclass, source registry, dedup,
  config loader, and CLI `--help`.
- README, docs (`getting-started`, `sources`, `alerters`, `roadmap`),
  GitHub issue/PR templates, MIT license.

### Notes
- All four sources are off by default; users opt in via `clawflow.yml`.
- Dashboard is a stub; the real terminal UI ships in v0.2 (see `docs/roadmap.md`).
- No external network calls happen at import time.

[0.1.2]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.2
[0.1.1]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.1
[0.1.0]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.0
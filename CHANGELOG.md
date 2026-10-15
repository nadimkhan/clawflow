# Changelog

All notable changes to **clawflow** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.0
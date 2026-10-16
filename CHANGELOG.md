# Changelog

All notable changes to **clawflow** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-10-17 — Web3 senior pipeline pivot

This is a **major pipeline shift**, not a drop-in of v0.1.x. The lead-pipeline
philosophy changed based on the user's actual profile (senior AI/blockchain
engineer via Graphixcon). Inbound WP maintenance, r/forhire, and the rest of
the "general" sources no longer match the user's effective hourly rate. v0.2.0
is the first release that's actually tuned to land projects at his seniority.

### Added
- **`profile:`** block in `clawflow.yml`. The cycle now runs every lead
  through a quality filter before persistence or alerting. Drops:
  - Leads whose haystack matches any `excluded_stacks` term (WordPress, PHP,
    frontend, HTML, CSS by default).
  - Leads whose haystack misses every `preferred_stacks` term AND fails the
    role-hint fallback (≥2 distinct role words like "engineer", "solidity",
    "researcher").
  - Leads whose title contains a seniority hint BELOW the user's band
    ("intern", "junior", "new grad" when ``seniority: senior``).
  - Leads whose haystack contains any `scam_signals` phrase ("unpaid trial",
    "revenue share only", etc.).
  - Leads whose extracted numeric USD hints cap below `min_budget_usd`.
- **5 community plugins** in `clawflow/sources/community/`:
  - `dao_rfps` — DAO + foundation hiring pages (Snapshot, Aave, Optimism,
    Arbitrum, Uniswap, ENS, Lens, Yearn, Gitcoin, Gnosis, Safe, Polygon, 1inch,
    Curve, Balancer). RSS-only.
  - `foundation_grants` — Solana / NEAR / ICP / Dfinity grants RSS feeds.
  - `ai_agent_jobs` — AI-agent + web3 GitHub issues (ElizaOS, GOAT, ZerePy,
    LangChain, Chainlink, Ocean Protocol…). Needs `GITHUB_TOKEN`.
  - `solana_jobs` — Solana Foundation RSS + GitHub bounties across Marinade,
    Wormhole, Jupiter, Drift, Helius, Metaplex, Backpack, etc.
  - `sherlock_audit` — Sherlock.xyz public REST API for audit contests.

  See "Reality" section below for which of these are working today.

### Reality: which sources are actually fetching leads on day one

When I built v0.2.0 I probed the public endpoints for each plugin. Three of
the five scrape upstreams that **404'd at probe time** (October 2026):

| Plugin | Status at v0.2.0 release | What's needed to fix |
|---|---|---|
| `dao_rfps` | ✅ **Working** — 4 leads/day from foundation hiring pages | None |
| `foundation_grants` | ✅ **Working** — IC Dfinity grants RSS responds; others 404'd but the source logs the failure cleanly | None |
| `ai_agent_jobs` | ⚠️ **Blocked** — needs GitHub PAT (you already have one); upstreams are reachable | Set `GITHUB_TOKEN` env or re-save via `clawflow auth github` |
| `solana_jobs` | ⚠️ **404** — Solana Foundation RSS URL has moved or been retired | Find new RSS endpoint; update `SOLANA_GRANTS_FEED` in `solana_jobs.py` |
| `sherlock_audit` | ⚠️ **404** — Sherlock public API URL has moved | Check Sherlock docs for new endpoint, update `SHERLOCK_CONTESTS` |

The three blocked plugins are intentionally kept in the repo with
"KNOWN_BROKEN_AS_OF_2026-10" banners at the top of each file so the code is
ready to revive in minutes when a working URL surfaces. Removing them
would lose 1.5h of work that nobody has to redo.

**What you see today (2026-10-17): 4 fresh web3 leads** from dao_rfps + IC Dfinity.

### Changed
- **`clawflow.yml` regen** — fresh template with `profile:` section + 4
  built-in sources (github_bounties, remote3_rss, cryptojobs_sitemap)
  + 5 new community sources. wordpress_cve, hn_freelancer, reddit_*,
  hn_jobs_monthly all `enabled: false` (no longer matching user profile).
- **`scheduler` persistence path fix** — `from pathlib import Path` moved
  from line 220 (where earlier patch had landed it) to the top of the file.
  Caused `NameError: name 'Path' is not defined` whenever a cycle produced
  leads. v0.1.3-0.1.4 cycles were silently persisting zero leads as a result.

### Removed
- Nothing user-facing. Disabled sources stay importable.

### Verified
- 34 pytest cases pass.
- One cycle produces 4 leads, all `dao_rfps`-sourced, all scored by the
  profile filter, all visible at http://127.0.0.1:5555/ when `clawflow
  leads-ui` is running.

## [0.1.4] — 2026-10-16

### Added
- **`clawflow leads-ui`** — local web UI for tracking leads and
  applications. Runs on `127.0.0.1:5555` (loopback only by default).
  - Lists every lead from the local SQLite store with score, source,
    URL, tags, and a status pill.
  - Status buttons per lead: ``interested`` / ``applied`` / ``done`` /
    ``rejected`` / reset to ``new``. Plus a free-form note field.
  - Filter by status and source from the top bar.
  - JSON API at ``/api/leads`` and ``/api/health`` for automation.
- **SQLite store** (``clawflow/store_sqlite.py``): a single-file
  durable copy of the leads DB. ``add_leads`` writes to BOTH the JSON
  ``state.json`` (CLI / dedup) and SQLite when a path is supplied.
- **Status migration**: ``clawflow leads-ui`` copies every lead from
  the existing ``state.json`` into SQLite on first run. Idempotent
  via INSERT OR IGNORE.

### Why no good free Reddit path right now (committed as-is)
- Probed ``old.reddit.com/.json``, ``old.reddit.com/r/forhire/``,
  ``www.reddit.com/.json``, PullPush, xcancel, and ``i.reddit.com``
  in October 2026 — every path now either 302-to-login or rate-limits
  anonymous traffic. The community plugin ``reddit_old`` continues
  to ship as a documented template for when a working free mirror
  surfaces. ``hn_jobs_monthly`` likewise: HN throttles 419.

### Notes
- 34 tests passing.
- The web UI server runs as a foreground process on Windows; ``Ctrl+C``
  in the terminal stops it cleanly. Two child processes spawn (uvicorn
  worker + launcher) — if one is killed, the other is naturally
  cleaned up. Use ``netstat -ano | grep :5555`` to find stragglers.

## [0.1.3] — 2026-10-16

### Added
- **Community plugin loader**: ``clawflow/sources/community/*.py`` files are
  now auto-discovered at startup. Drop a ``@register``'d ``Source``
  subclass in there and it shows up in ``clawflow sources list`` after
  the next cycle starts.
- **Free / no-API community plugin** at
  ``clawflow/sources/community/no_api_leads.py``:
  - ``reddit_old`` — reads ``old.reddit.com/r/{sub}.json`` (legacy endpoint
    that didn't enforce auth until mid-2026).
  - ``hn_jobs_monthly`` — scrapes HN's monthly ``whoishiring`` thread HTML
    for "we are hiring X" comments, no API key.

### Reality check (read this before enabling these)
- **Reddit anonymous JSON is dead since ~mid-2026.** Both
  ``www.reddit.com/.json`` and ``old.reddit.com/.json`` now redirect to a
  login wall for any non-authenticated client. Public community mirrors
  (PullPush, xcancel, Reddit-stream) are likewise rate-limited or legally
  unavailable. ``reddit_old`` is shipped for the historical contract
  (and as a template you can drop a working mirror URL into once you have
  one), but it will return 0 leads on a default install as of 2026-10.
- **HN is heavily throttling anonymous scraping in 2026.** Expect 419s.
  ``hn_jobs_monthly`` ships functional but may yield 0 in rate-limit
  windows. Pair with ``hn_freelancer`` (Algolia API, currently working
  when the query matches).
- **No good free Discord API workaround exists.** Community servers can
  only be read via a Discord bot token (not anonymous). Discord bot
  sources will land in v0.2 once someone files an issue requesting it.

### What this version _did_ accomplish end-to-end
- Auto-loading of community sources without manual import boilerplate.
- ``reddit_old`` and ``hn_jobs_monthly`` are correctly wired through the
  scheduler even though the upstream endpoints now block them.
- ``clawflow sources list`` reports the two new source keys as
  registered + opt-in.
- 34 tests passing.

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

[0.2.0]: https://github.com/nadimkhan/clawflow/releases/tag/v0.2.0
[0.1.4]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.4
[0.1.3]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.3
[0.1.2]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.2
[0.1.1]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.1
[0.1.0]: https://github.com/nadimkhan/clawflow/releases/tag/v0.1.0
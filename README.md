# clawflow

> **One binary, four signals, zero sales-team BS. Built by a freelancer, for freelancers.**

*By [Nadim Khan](mailto:graphixcon@gmail.com) — freelance Solidity + WordPress developer. Maintained in the open because the tools I needed didn't exist.*



`clawflow` is a tiny, dependency-light CLI that aggregates freelance opportunities
from four upstream signals — GitHub bounties, web3 job boards, WordPress CVE
alerts, and the Hacker News / Reddit "seeking freelancer" threads — and pushes
a deduplicated digest to your terminal, Telegram, or email.

No login wall. No SaaS dashboard. No "upgrade to Pro" CTA. Just one
`pipx install clawflow` and a 30-line YAML.

---

## Why clawflow exists

If you're a solo freelancer who writes Solidity **or** ships WordPress
plugins, you've probably been doing what I was doing: juggling six tabs —
GitHub notifications, CryptoJobsList, Remote3, HN's monthly freelancer
thread, a Wordfence email a week too late, and a Telegram channel that
nukes everything older than 24 hours. Harvey-style cold-email is the wrong
shape; clawflow gives you the upstream signal instead. I wanted **one
binary** that watches those signals, dedupes them, and pings me once.
That's clawflow.

---

## How it flows

![clawflow run cycle](docs/screenshot-cycle.png)

```
   ┌────────────────────────────┐
   │  Sources (off by default)  │
   │                            │
   │  github_bounties           │
   │  web3_sitemap              │       ┌─────────┐    ┌──────────┐
   │  wordpress_cve   ──────────┼──────▶│  dedup  │───▶│ alerter  │
   │  hn_freelancer             │       └─────────┘    └──────────┘
   │                            │            │              │
   └────────────────────────────┘            ▼              ▼
                                       state.json      console
                                                      telegram
                                                        email
```

Every cycle: collect → dedupe (by content-hash of `url + title`) → alert
only the **new** leads above your per-source severity threshold.

---

## Install

Requires Python 3.11+. Recommended install via `pipx` so `clawflow` lands
on your PATH without touching your system Python:

```bash
pipx install clawflow
# or, from a clone
git clone https://github.com/nadimkhan/clawflow.git
cd clawflow
pipx install .
```

Verify:

```bash
clawflow --help
```

---

## Quickstart

```bash
# 1. Generate a working config in the current directory
clawflow init

# 2. Edit clawflow.yml — flip the sources you want on, set severity thresholds
$EDITOR clawflow.yml

# 3. Run a single cycle and exit (no daemon)
clawflow run --once

# 4. Run forever, polling every 15 minutes
clawflow run

# 5. Inspect what was collected
clawflow leads --since 24h
clawflow status
```

That's it. No database, no Docker, no `.env.local.example` ceremony.

---

## Configuration

Everything lives in a single `clawflow.yml` (or `~/.config/clawflow/config.yml`).

| Key                       | Type   | Default                                | Notes                                                                                |
| ------------------------- | ------ | -------------------------------------- | ------------------------------------------------------------------------------------ |
| `state_path`              | path   | `./state.json`                         | Atomic JSON lead store.                                                              |
| `interval_minutes`        | int    | `15`                                   | Poll cadence for `clawflow run`.                                                     |
| `cycle_budget_seconds`    | int    | `120`                                  | Hard cap per cycle — sources that overrun are skipped next round.                    |
| `sources`                 | map    | *all off*                              | One block per source id; see `docs/sources.md`.                                      |
| `alerters`                | list   | `[{type: console}]`                    | Multiple destinations supported.                                                     |
| `dedup.fields`            | list   | `["url", "title"]`                     | Fields combined to compute the dedup hash.                                           |
| `dedup.retention_days`    | int    | `30`                                   | Leads older than this are pruned from `state.json` on each cycle.                    |
| `logging.level`           | str    | `INFO`                                 | `DEBUG`, `INFO`, `WARNING`, `ERROR`.                                                 |
| `logging.format`          | str    | `%(asctime)s %(levelname)s %(message)s` | Standard `logging` format string.                                                    |

For alerter-specific fields (`telegram.bot_token`, `email.smtp_host`, etc.)
see `docs/alerters.md`.

---

## Sources matrix

All four sources are **off by default**. Enable them under
`sources.<id>.enabled: true` in `clawflow.yml`.

| Source id          | Signal                                           | Auth required | Rate-limited | Docs                          |
| ------------------ | ------------------------------------------------ | ------------- | ------------ | ----------------------------- |
| `github_bounties`  | Issues labelled `💎 Bounty` on GitHub            | `GITHUB_TOKEN` (PAT) | Yes (5000/h) | `docs/sources.md`         |
| `web3_sitemap`     | Remote3 RSS + CryptoJobsList sitemap             | none          | polite       | `docs/sources.md`             |
| `wordpress_cve`    | Per-plugin CVE feed from WPVulnerability.net      | none          | none         | `docs/sources.md`             |
| `hn_freelancer`    | HN monthly "Freelancer?" thread + r/forhire      | none          | polite       | `docs/sources.md`             |

Add your own via `clawflow sources add my_source` (scaffold) or drop a
Python file into `clawflow/sources/community/` and re-export.

---

## Roadmap

- **v0.1 (this release)** — vertical slice, four sources, three alerters,
  CLI stable.
- **v0.2** — interactive terminal dashboard (`clawflow dashboard`),
  richer filtering (`--tag`, `--min-score`, `--since`), plug-in
  hot-reload.
- **v0.3** — AI re-ranking (heuristic-only at first; LLM later, opt-in
  via local Ollama).
- **v0.4** — multi-account (one binary, many `clawflow.yml`s on a shared
  host).

Full plan: [`docs/roadmap.md`](docs/roadmap.md).

---

## Contributing

PRs welcome. Please read [`CONTRIBUTING.md`](.github/CONTRIBUTING.md) (or
the PR template) and open an issue first for non-trivial changes so we
can agree on scope. Bug reports → `.github/ISSUE_TEMPLATE/bug_report.md`.

---

## License

MIT — see [LICENSE](LICENSE). © 2026 Nadim Khan.

<!-- Built in the open: source at github.com/nadimkhan/clawflow — safe to fork. -->
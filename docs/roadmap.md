# Roadmap

`clawflow` ships today as a vertical-slice v0.1. Below is what's next.
Each milestone is gated on real user feedback; if v0.2 doesn't solve
your problem, file an issue before v0.3 starts.

## v0.1 — Vertical slice (shipped 2026-10-15)

- Four sources (GitHub bounties, web3 sitemap, WP-CVE, HN/Reddit).
- Three alerters (console, Telegram, email).
- Single-file YAML config with documented defaults.
- Atomic JSON lead store, content-hash dedup.
- Smoke tests + GitHub issue/PR templates.

**Non-goals for v0.1:** interactive dashboard, plug-in hot-reload,
LLM re-ranking.

## v0.2 — Dashboard & filtering — Q4 2026

- `clawflow dashboard` — a real terminal UI (curses / textual) with
  source health, recent leads, and on-the-fly filter editing.
- `clawflow leads --tag solidity --min-score 40 --since 6h` style filters.
- Hot-reload of `clawflow.yml` on `SIGHUP` (no need to restart).
- Source community index (`clawflow sources registry`).

## v0.3 — Smart ranking — Q1 2027

- Heuristic re-ranking: cross-source dedup, language match, time-of-day
  weighting. No LLM by default.
- *Optional* LLM re-rank via local Ollama (off by default; opt-in per
  cycle to keep cost at zero).
- Per-source noise profile, learned from your past decisions
  ("M-`source` leads I never act on get auto-suppressed").

## v0.4 — Multi-tenant — Q2 2027

- One binary, many `clawflow.yml`s on a shared host.
- Per-tenant rate-limit budget + isolated state files.
- Headless RPC mode for embedding clawflow in other automation.

## Ideas parked, not promised

- Webhook alerter (Slack-compatible incoming webhooks).
- Discord alerter.
- Browser-extension overlay that surfaces leads while you read GH issues.
- An "I'm available" outbound mode that flips the polarity — clawflow
  posts *your* availability into the same channels. (Spec only; not
  scheduled.)

If something here matters to you sooner, open an issue with the label
`roadmap-priority` and we'll re-sequence.
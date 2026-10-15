# clawflow — Project Roadmap

> Living document. Updated as we ship. Most recent at top.

---

## Current — v0.1.0 (vertical slice)

**Goal:** A single-binary CLI that runs locally, polls four sources, and
pushes a deduplicated daily digest to console / Telegram.

**Scope:**

| Source | Status |
|--------|--------|
| `github_bounties` | ships in v0.1 |
| `web3_sitemap` | ships in v0.1 |
| `wordpress_cve` | ships in v0.1 |
| `hn_freelancer` | ships in v0.1 |

| Alerter | Status |
|---------|--------|
| `console` | ships in v0.1 |
| `telegram` | ships in v0.1 |
| `email` (SMTP) | ships in v0.1 |

**Explicitly NOT in v0.1** (deliberately deferred):

- Database backend (still JSON state file)
- Web dashboard (still CLI + Telegram)
- Community source plugin loader
- Multi-account / multi-user
- HTTPS-only mode
- Async chaining across sources
- Caching layer beyond the JSON file

**Ship target:** Single-PR, ≤ 2,500 LOC total (incl. docs).

---

## v0.2.0 — first feedback round (4-6 weeks after v0.1)

**Trigger:** At least 10 unique cloners OR 5 GitHub issues OR 3 PRs.

**Candidates:**

| Feature | Why deferred to v0.2 |
|---------|---------------------|
| Web dashboard (Flask, single-file) | Need real users to know what's useful |
| `clawflow leads export --format csv` | Most-requested in early feedback |
| Engagement tracking (mark interesting, follow up in 3 days) | Need real user behavior to confirm |
| Reddit r/slavelabour, r/jobbit sub-sources | Spam-management learnings from v0.1 |
| `clawflow config doctor` | Had real-config issues in v0.1 first-time setup |

**Risk:** Don't ship a v0.2 until v0.1 has been idle for at least 4
weeks. Premature iteration burns maintainer energy.

---

## v0.3.0 — community source plugins (8-12 weeks)

**Trigger:** Anyone external files an issue saying "I want to add X
source".

**Goal:** Make the source loader explicit + documented enough that
someone who has never read clawflow's code can write a plugin in
under 30 minutes.

**Deliverables:**

- `clawflow/sources/community/` (auto-loaded from
  `~/.local/share/clawflow/sources/*.py`)
- `clawflow source init <name>` (scaffold a new source stub)
- `docs/source-authoring.md` (the Source protocol)
- `examples/community/superteam_earn.py` (the reference plugin)
- PyPI publish (so `pip install clawflow` works alongside `pip install -e .`)

**Out of scope for v0.3:** Plugin signature / verification (deferred to
v1.0).

---

## v0.4.0 — observability & ops (12-16 weeks)

**Trigger:** Anyone using clawflow across multiple machines, or the
Telegram digest getting too noisy.

**Candidates:**

- `clawflow metrics` (Prometheus textfile output)
- `clawflow logs --since 24h` (structured log querying)
- `clawflow backfill --days 7` (re-run with fresh dedup for missed leads)
- Webhook alerter (for Slack, Discord, anything-POST)

---

## v0.5.0 — optional cloud sync (16-20 weeks)

**Trigger:** Real demand signal, not anticipated.

- Optional encrypted cloud sync of state.json (so you can run clawflow
  on laptop + a server)
- Cost: probably $0 — use a GitHub Gist as the sync target (encrypted
  client-side with age)

**Hard rule:** The cloud sync must remain OPTIONAL. Local-only must
always be a first-class path. Anyone asking for it as the default has
misunderstood the project.

---

## v1.0.0 — stable API (6-12 months)

**Trigger:** The Source protocol hasn't changed in 3 releases AND the
state file format is documented AND 100+ stars.

**Promises of v1.0:**

- API stability (breaking changes only in v2.x)
- Plugin signature (optional but recommended)
- Audited dependency set (no surprise supply-chain)
- Docs site (mkdocs or docusaurus)

---

## Things we will NOT build

This project is intentionally small. The following are out of scope FOREVER
(or until a major rewrite, whichever comes first):

| Idea | Why not |
|------|---------|
| Sending cold emails on your behalf | Tools like Harvey exist for this; clawflow is the upstream signal |
| CRM features | Plenty of CRMs; clawflow just tracks leads |
| Public web dashboard for everyone | n8n / Apify / Zapier all do this; clawflow stays CLI |
| AI-generated replies | Per-lead LLM calls burn budget fast and remove the human from the loop |
| Telegram group bot | Single-chat only — multi-user is operational complexity this project can't carry |
| Web3 wallet auth | clawflow is read-only against public APIs; auth doesn't add capability |
| Discord/Slack-only modes | Telegram + email + console are enough; chat-app fragmentation is a graveyard |

If you're considering a PR that adds one of these, please open an issue
first to discuss. We may be wrong; if you feel strongly, we'd love to
hear why.

---

## Open questions

Things I genuinely don't know yet:

1. **Python 3.13 support** — async + httpx behavior may shift. Test
   before bumping the `requires-python` floor in pyproject.toml.
2. **Windows Task Scheduler reliability** — does it actually wake from
   sleep? Need field reports.
3. **GitHub `💎 Bounty` label stability** — Algora, the platform behind
   most of these, has changed product line twice in 2024-2025. Watch
   for label changes that break filters.
4. **CryptoJobsList sitemap reliability** — if the site starts blocking
   scrapers, we need a fallback API (RapidAPI key, etc).
5. **Reddit API changes** — Reddit has been hostile to scraping since
   2024. If the JSON endpoint disappears, this source dies. Worth
   noting in the README.

---

## Decision log

| Decision | When | Why |
|----------|------|-----|
| No SaaS, no DB at v0.1 | v0.1 planning | Maximize "does it work?" signal, defer infra |
| JSON state file (not SQLite) | v0.1 planning | One binary, one fs path; SQLite adds a dep |
| Async generators for sources | v0.1 planning | Lets us stream, dedup progressively, and isolate per-source errors |
| argparse (not click/typer) | v0.1 planning | Zero new dep; v0.3 can swap if needed |
| feedparser (not atoma) | v0.1 planning | One library, handles Atom + RSS, mature |
| Telegram as primary alerter | v0.1 planning | Free, push, mobile-friendly, single-chat simple |
| MIT license | v0.1 planning | Maximally compatible with forking, contributions |
| NOT bundling LLM scoring | v0.1 planning | Per-call cost, results in user hands |
| `clawflow` name | pre-v0.1 | Short, memorable, CLI-friendly; one-word brand |

---

*Edited: Nadim Khan / clawflow maintainers. Open a PR to edit.*

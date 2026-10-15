# Getting Started

This guide walks you from a fresh `pip install` to your first digest.

## 1. Install

```bash
pipx install clawflow
```

(`pip install --user clawflow` also works; `pipx` just keeps it isolated.)

## 2. Generate a config

```bash
cd ~/projects  # wherever you want the state file
clawflow init
```

This writes a documented `clawflow.yml` to the current directory. The
generated file has every source **disabled** — clawflow never talks to
the network without your explicit opt-in.

## 3. Enable at least one source

Open `clawflow.yml` and flip the switches you care about. The four
built-ins:

```yaml
sources:
  github_bounties:
    enabled: true
    min_bounty_usd: 100
    languages: [solidity, rust, typescript]

  web3_sitemap:
    enabled: true
    boards: [remote3, cryptojobslist]

  wordpress_cve:
    enabled: true
    plugins: [elementor, woocommerce, wordpress-seo, wpforms-lite]
    min_severity: medium   # low | medium | high | critical

  hn_freelancer:
    enabled: true
    reddit_subreddits: [forhire]
    hn_query: "freelancer OR contractor"
```

## 4. Configure an alerter

Default is `console`. For Telegram or email, see
[`docs/alerters.md`](alerters.md). Secrets come from environment
variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_PASSWORD`,
etc.) — never commit them.

## 5. First cycle

```bash
clawflow run --once
```

You'll see a short log line per source ("collected N leads, M new"),
followed by your alerter firing for everything new above the threshold.

## 6. Go live

```bash
clawflow run                 # foreground, ctrl-c to stop
# or, as a managed background task
nohup clawflow run >> clawflow.log 2>&1 &
```

State is persisted to `state.json` (or wherever `state_path` points) —
delete it to start fresh, back it up to migrate machines.

## What's next?

- Browse the source matrix → [`docs/sources.md`](sources.md)
- Configure alerters → [`docs/alerters.md`](alerters.md)
- Read the roadmap → [`docs/roadmap.md`](roadmap.md)
# Security policy

## Reporting a vulnerability

Email **graphixcon@gmail.com** with the subject `[clawflow]`. Don't
open a public issue for unreleased security bugs.

We aim to acknowledge within 48 hours and ship a fix or mitigation
within 14 days, depending on severity.

## What we consider in scope

- Anything that exfiltrates a `clawflow.yml`, `state.json`, or
  environment variable without your knowledge.
- A source or alerter that silently fails *open* (drops a lead instead
  of erroring) in a way that masks an outage.
- Supply-chain: a malicious or compromised dependency published under
  our name.

## Out of scope

- Bugs in upstream services (GitHub, WPVulnerability, Reddit, HN,
  Telegram, SMTP providers).
- Phishing or social-engineering against the *user* — clawflow never
  asks for credentials it doesn't need for the source you enabled.

## Token handling

- `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `SMTP_PASSWORD`, etc. are read
  from environment variables only. They are never written to
  `state.json` or `clawflow.yml`.
- `state.json` contains only the public `url`, `title`, and score of
  each lead — never tokens, never your config.
- If you accidentally commit a token, rotate it immediately. GitHub's
  PAT revocation page is at
  `https://github.com/settings/tokens`.
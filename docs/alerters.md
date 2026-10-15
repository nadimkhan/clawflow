# Alerters

Alerters receive a batch of `Lead` objects after each `clawflow run`
cycle. The default config uses `console` only.

## Common fields

```yaml
alerters:
  - type: console
    severity_threshold: 0       # 0..100, leads below are suppressed
    max_items_per_cycle: 50     # safety cap so a noisy source doesn't spam
```

| Key                     | Type | Default | Notes                                          |
| ----------------------- | ---- | ------- | ---------------------------------------------- |
| `type`                  | str  | —       | `console`, `telegram`, `email`.                |
| `severity_threshold`    | int  | `0`     | Per-alerter; sources also have their own.      |
| `max_items_per_cycle`   | int  | `50`    | Hard cap on items per cycle.                    |

---

## `console`

Prints a colourised table to stdout. No extra config.

```yaml
alerters:
  - type: console
```

Disable with `severity_threshold: 101` if you only want remote delivery.

---

## `telegram`

Posts each lead as a separate message to a chat you control. Set the
bot token and chat id via env vars (recommended) or inline config.

**Setup:**

1. Talk to [@BotFather](https://t.me/BotFather), create a bot, copy the token.
2. Send any message to your bot, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id`.
3. Export both:

```bash
export TELEGRAM_BOT_TOKEN="123456:abc..."
export TELEGRAM_CHAT_ID="987654321"
```

**Config:**

```yaml
alerters:
  - type: telegram
    severity_threshold: 30
    parse_mode: markdown        # markdown | html | plain
    include_score: true
```

---

## `email`

SMTP send via stdlib `smtplib`. Useful as a daily-digest fallback when
Telegram is down.

**Config:**

```yaml
alerters:
  - type: email
    severity_threshold: 50
    smtp:
      host: smtp.gmail.com
      port: 587
      username: you@gmail.com    # or $SMTP_USERNAME
      password_env: SMTP_PASSWORD
      use_tls: true
    from_addr: clawflow@graphixcon.in   # any address you own; SPF/DKIM/DMARC recommended
    to_addrs: [you@gmail.com]
    subject_template: "[clawflow] {new_leads} new leads"
```

Notes:

- App passwords (Gmail, Outlook) work better than account passwords.
- All `*_env` keys resolve at send time, not at parse time, so you can
  rotate credentials without restarting.
- A failed send is logged at WARNING; other alerters still fire.

---

## Per-source severity

Each source block also has its own `severity_threshold`. A lead only
reaches an alerter if **both** thresholds allow it through. This is
how you get a "daily digest" feel: noisy sources (Reddit) get a high
threshold, low-noise sources (GitHub bounties) get a low one.

```yaml
sources:
  hn_freelancer:
    enabled: true
    severity_threshold: 70      # only HN posts scoring >= 70
  wordpress_cve:
    enabled: true
    severity_threshold: 20      # even low-severity CVEs page me
```

Score normalisation (0..100) is source-defined — see the source
implementation for its scoring function.
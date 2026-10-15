# Sources

Every source is a self-contained async Python module in
`clawflow/sources/`. The registry auto-loads anything that subclasses
`Source`; drop a file into `clawflow/sources/community/` and it shows up
in `clawflow sources list` after a restart.

All sources are **off by default**. Enable them per-id in `clawflow.yml`.

## Common fields

Every source block shares this shape:

```yaml
sources:
  <source_id>:
    enabled: true|false        # default false
    severity_threshold: 0..100 # leads below are kept but not alerted
    timeout_seconds: 30        # per-request httpx timeout
    # ... source-specific keys below
```

---

## `github_bounties`

Watches GitHub issues carrying the `💎 Bounty` label (the Algora-style
dollar bounty label that has become the de-facto standard for paid
open-source work).

**Endpoint:** GitHub GraphQL `https://api.github.com/graphql` (POST).

**Auth:** `GITHUB_TOKEN` env var (a fine-grained PAT with public-repo
read is enough). Without a token you still get the public REST search,
but you'll hit the unauthenticated 60 req/h cap in minutes.

**Config keys:**

| Key                | Type        | Default                                | Notes                                                                  |
| ------------------ | ----------- | -------------------------------------- | ---------------------------------------------------------------------- |
| `min_bounty_usd`   | int         | `0`                                    | Drop issues whose `$XX` label parses below this.                       |
| `languages`        | list[str]   | `[]`                                   | Restrict to issues on repos whose primary language matches.            |
| `exclude_labels`   | list[str]   | `["spam", "honeypot"]`                 | Extra labels to filter out.                                            |
| `max_pages`        | int         | `3`                                    | GitHub paginates 50 per page; 3 ≈ 150 issues per cycle.                |

**Behaviour:**

- Walks pagination until `max_pages` or an empty `pageInfo.hasNextPage`.
- Honours `X-RateLimit-Reset` — backs off and skips the cycle if the
  token is exhausted.
- Raises a clear `SourceAuthError` on 401/403 so you can fix the token
  without a stacktrace.

**Dry-run:** `clawflow sources add github_bounties --dry-run` prints the
exact GraphQL variables it would send, then exits 0 — no network call.

---

## `web3_sitemap`

Two sub-sources wired together:

1. **Remote3** — RSS at `https://www.remote3.co/feed.xml` (verified
   public feed of remote web3 roles).
2. **CryptoJobsList** — XML sitemap at `https://cryptojobslist.com/sitemap.xml`,
   walked through `feedparser`'s sitemap support.

**Auth:** none. Both endpoints are public.

**Config keys:**

| Key             | Type      | Default                  | Notes                                       |
| --------------- | --------- | ------------------------ | ------------------------------------------- |
| `boards`        | list[str] | `[remote3, cryptojobslist]` | Subscribed feeds.                       |
| `keywords`      | list[str] | `[]`                     | If non-empty, only items whose title matches. |
| `max_age_hours` | int       | `48`                     | Drop items older than this from the digest. |

---

## `wordpress_cve`

Polls `https://www.wpvulnerability.net/plugin/{slug}/` (public, no key)
for each plugin in your list. When a vuln is reported **above your
severity threshold**, clawflow emits a Lead — so this isn't a job feed
in the traditional sense; it's a *service offering* signal. If you ship
plugins for Elementor or WooCommerce and a high-severity CVE drops,
clients are about to need help.

**Auth:** none.

**Config keys:**

| Key            | Type        | Default                                                                              | Notes                                              |
| -------------- | ----------- | ------------------------------------------------------------------------------------ | -------------------------------------------------- |
| `plugins`      | list[str]   | `[elementor, woocommerce, wordpress-seo, wpforms-lite, yoast-seo]`                   | Plugin slugs to watch.                             |
| `min_severity` | str         | `medium`                                                                             | One of `low`, `medium`, `high`, `critical`.        |

---

## `hn_freelancer`

Two sub-sources:

1. **Hacker News** via the Algolia HN Search API
   (`https://hn.algolia.com/api/v1/search?query=...&tags=story`).
   Useful for the monthly *"Freelancer? Seeking freelancer?"* mega-thread
   that has run for over a decade.
2. **Reddit** via the public JSON endpoint
   (`https://www.reddit.com/r/forhire/new.json?limit=25`). No auth;
   Reddit asks for a polite `User-Agent`.

**Config keys:**

| Key                 | Type        | Default          | Notes                                                            |
| ------------------- | ----------- | ---------------- | ---------------------------------------------------------------- |
| `hn_query`          | str         | `freelancer`     | Algolia query string.                                            |
| `reddit_subreddits` | list[str]   | `[forhire]`      | Subreddits to scrape.                                            |
| `min_hn_points`     | int         | `5`              | Drop HN stories below this score.                                |
| `lookback_hours`    | int         | `24`             | Only items newer than this are kept.                             |

---

## Adding your own

Two options:

1. **Scaffold** — `clawflow sources add my_id` writes a starter module
   in `clawflow/sources/community/my_id.py` with the protocol and a
   TODO. You fill it in.
2. **Drop-in** — copy a built-in module, change the class, drop the
   file under `clawflow/sources/community/`. The registry picks it up
   on next launch.

Either way, the contract is:

```python
class MySource(Source):
    id = "my_id"
    async def collect(self) -> AsyncIterator[Lead]: ...
```

No base class needs to be imported — the registry only checks for the
duck-typed `id` attribute and a `collect()` coroutine.
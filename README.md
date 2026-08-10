# NEPSE Market Bot

A self-hosted Telegram bot for live Nepal Stock Exchange data — market updates every 30 minutes, circuit breaker alerts, broker floorsheet analysis, and ML-ranked buy signals, delivered straight to a group chat.

Runs entirely on Cloudflare Workers + GitHub Actions free tiers, so hosting cost is **Rs 0/month**. Market hours are Monday–Friday, 11:00 AM – 3:00 PM NST, and most automation is scoped to that window.

A companion web dashboard — **[GHANTAGHAR](https://ghantaghar.pages.dev)** — ships alongside it, with charts, technical indicators, and a browser-local watchlist. Code's in [`site/`](site/).

For architecture and internals, see [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

---

## What it does

**Scheduled, automatic:**
- Live index + gainers/losers/turnover every 30 minutes during market hours
- Morning open summary and end-of-day recap
- Circuit breaker alerts (±10%) and volume spike alerts (2× the 30-day average)
- Daily market insights — streaks, day-of-week patterns, year-ago comparisons
- ML-based pre-market anomaly scan and a daily buy-signal ranking across all listed stocks
- IPO/rights notices, dividend announcements, official NEPSE notices

**On demand, via commands:**
- `/stock NABIL` — OHLC, 52-week range, RSI, SMA20/50/200, MACD, pivot points, P/B, 1-year return, plain-language verdict
- `/broker NABIL` — floorsheet breakdown: top buyers/sellers, net flow, concentration ratio
- `/scan`, `/top`, `/sector`, `/check` — anomaly scan, top movers, sector performance, live snapshot

**Group moderation** — auto-delete links from non-admins, a 3-strike warn system, mute/ban/kick.

Full command list is below and in `/help` once the bot is running.

---

## Architecture, briefly

A Cloudflare Worker answers every Telegram command instantly and manages state in Cloudflare KV. It can't reach NEPSE's API directly, though — that requires an auth token computed by NEPSE's own WASM bundle, which only a Python library can do. So anything that needs live data gets dispatched to a Python script running on GitHub Actions, which fetches, computes, and sends the result back via Telegram's API.

See [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for the full breakdown — data sources, KV layout, and how a command flows end to end.

---

## Commands

**Market data**
- `/check` or `/now` — live NEPSE index + gainers/losers
- `/top` — top 5 gainers, losers, turnover leaders
- `/sector` — sector-wise performance
- `/stock NABIL` (or `/stock NABIL,NICA`) — full technical deep-dive

**Broker & scans**
- `/broker NABIL` — floorsheet breakdown, net flow, concentration
- `/scan` — pre-market anomaly scan

**Group & admin**
- `/start`, `/help`, `/rules` — everyone
- `/setup` — admins, checks bot permissions
- `/warn` (reply to a message), `/ban`, `/kick`, `/mute 1h` (30m/1h/1d/7d), `/unmute`, `/unban` — admins only

---

## Schedule (Mon–Fri, NST)

| Time | What sends |
|---|---|
| 9:00 AM | Market insights + ML buy-signal picks |
| 10:00 AM | IPO / rights notices |
| 10:30 AM | Pre-market anomaly scan |
| 11:00 AM | Open summary + sector outlook |
| 11:30 AM – 3:00 PM | Live update every 30 min |
| 6:00 PM | NEPSE notices |
| 7:00 PM | Dividend announcements |
| 8:00 PM | End-of-day recap + close summary |

---

## Setup

Requires a Telegram bot token (via @BotFather), a free Cloudflare account, and a free GitHub account.

1. Fork and clone the repo
2. Deploy `worker/` with Wrangler — create a KV namespace, set it in `wrangler.toml`, then `wrangler deploy`
3. Set Worker secrets: `TELEGRAM_TOKEN`, `ALLOWED_CHAT_ID`, `GITHUB_TOKEN`, `GITHUB_REPO`
4. Register the Telegram webhook to point at the deployed Worker URL
5. Add the same `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` plus `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID`, `CF_API_TOKEN` as GitHub Actions secrets — required for dedup state to persist across scheduled runs
6. Trigger any workflow manually from the Actions tab to confirm it reaches your group

Full architecture and internals: [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

---

## Repo layout

```
nepse-telegram-bot/
├── .github/workflows/    one YAML per scheduled/on-demand job
├── nepse/                shared package — KV client, ShareSansar client, common helpers
├── scripts/              every runnable bot script (each works standalone with --print)
├── worker/               Cloudflare Worker: webhook handler + command routing
├── site/                 GHANTAGHAR dashboard — static, no build step
├── requirements.txt
├── HOW_IT_WORKS.md
└── README.md
```

Each script under `scripts/` maps to one workflow and can run locally with `--print` to preview output instead of hitting Telegram, e.g. `python scripts/stock_info.py NABIL --print`.

## Contributing

- Test scripts locally with `--print` before opening a PR
- New command → add routing in `worker/index.js`
- New scheduled script → add a workflow in `.github/workflows/`
- No hardcoded secrets — everything comes from env vars or GitHub secrets

## Data sources

- [nepalstock.com.np](https://nepalstock.com.np) — live prices, index, floorsheet, via `nepse-scraper`
- [ShareSansar's static API](https://omitnomis.github.io/ShareSansarScraper/api/) — long-range price history, EOD recap
- [Nepse-All-Scraper](https://github.com/SamirWagle/Nepse-All-Scraper) — floorsheet CSVs, notices, dividend data

## License

MIT

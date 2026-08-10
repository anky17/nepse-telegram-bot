# Architecture

Technical reference for the bot's internals. [README.md](README.md) covers setup.

## Two layers

A Cloudflare Worker (JavaScript) handles every Telegram webhook and responds in under a second — routing commands, moderating the group, reading/writing Cloudflare KV. It can't fetch live NEPSE data directly, though: NEPSE's API requires an auth token computed by a WASM bundle embedded in their website, and the only library that's reverse-engineered this (`nepse-scraper`) is Python-only.

So anything needing live data is dispatched from the Worker to a Python script on GitHub Actions (via `workflow_dispatch`), which does the actual fetching, computation, and sends the result back to Telegram directly. GitHub Actions runners are free and disposable — they spin up, run one script, and disappear.

In short: Worker = instant responses and state, GitHub Actions = data fetching and computation.

## Data sources

**nepalstock.com.np**, via `nepse-scraper`. The only source for live data — current prices, index, floorsheet. Handles WASM auth automatically. Key methods: `get_nepse_index()`, `get_today_price()`, `get_top_stocks()`, `get_ticker_info()`, `get_ticker_price_history()` (~90 days max), plus a generic `call_endpoint()` for market/sector summaries and notices.

**ShareSansar's static JSON API** (`omitnomis.github.io/ShareSansarScraper/api/`), no auth required. Used for long-range history — `history/{SYMBOL}.json` goes back 860+ trading days, which is what SMA200 and golden/death-cross calculations need since NEPSE's own API only returns ~90 days. Also publishes a daily EOD recap with breadth and top movers pre-computed. History rows are `[d, o, h, l, c, ltp, vwap, vol, to, dp]`, oldest first.

**[Nepse-All-Scraper](https://github.com/SamirWagle/Nepse-All-Scraper)**, for floorsheet CSVs, notices, and dividend history. Floorsheet data isn't available until NEPSE publishes it (typically 5–8 PM), which is why broker analysis is on-demand rather than scheduled at close.

## The Worker

`worker/index.js`, config in `worker/wrangler.toml`. Every Telegram message arrives as a webhook POST and routes through:

- Wrong chat ID → ignored (only responds in its configured group)
- New member → welcome message
- Non-command message with a URL, from a non-admin → deleted, user warned
- Command → routed to the matching workflow: `/check`/`/now` → `nepse_bot.yml`, `/stock` → `stock_info.yml`, `/broker` → `broker_floorsheet.yml`, `/scan` → `smart_scan.yml`, `/top`/`/sector` → `top_stocks.yml`, `/warn` increments a KV counter (auto-ban at 3), `/ban`/`/kick`/`/mute`/`/unmute`/`/unban` hit the Telegram API directly

Two cron jobs also run here: a market-open reminder at 10:55 AM NST, and a freshness watchdog that checks every 10 minutes whether the scheduled 30-minute update has fallen stale (GitHub's scheduler can delay under load) and re-dispatches it if so, with a cooldown to avoid piling up dispatches.

## Scripts

Every script in `scripts/` follows the same pattern: fetch, compute, format, send — and supports a `--print` flag for local testing without hitting Telegram.

- **`nepse_bot.py`** — the core 30-minute update: index, breadth, volume spikes, circuit breakers. Also writes the day's price snapshot to KV, which is what keeps the website's board live during market hours.
- **`stock_info.py`** — powers `/stock`. Combines live OHLC/52-week range from NEPSE with long-range history from ShareSansar to compute SMA20/50/200, RSI, MACD, and pivot points, then rolls everything into a scored verdict. Stocks in NEPSE's compliance groups (Z/D) are automatically flagged regardless of score.
- **`watchlist_signal.py`** — scans every active equity daily, builds a technical feature set (RSI, MACD, Bollinger Bands, volume, momentum, volatility regime), and trains a gradient-boosted classifier on 10-day forward returns using walk-forward splits to avoid leakage.
- **`smart_scan.py`** — an anomaly detector (Isolation Forest) that flags stocks behaving unusually versus their own recent pattern, run pre-market.
- **`market_open.py` / `eod_recap.py` / `broker_analysis.py`** — open/close summaries.
- **`market_insights.py`** — streaks and day-of-week patterns from full history.
- **`notices.py` / `dividend_alert.py` / `ipo_alert.py`** — poll for new announcements, dedupe against KV state.
- **`broker_floorsheet.py`** — aggregates the daily floorsheet per broker for net flow and a concentration ratio.
- **`top_stocks.py`** — powers `/top` and `/sector`.

## Shared package (`nepse/`)

- `common.py` — timezone constant, scraper factory, formatting helpers
- `kv.py` — REST client for Cloudflare KV. If the `CF_*` secrets are missing in GitHub Actions, this fails silently — reads return defaults, writes no-op — and the symptom is dedup-dependent scripts resending things they already sent
- `sharesansar.py` — client for the ShareSansar static API
- `telegram.py` — `sendMessage` wrapper, chunks messages over Telegram's 4096-char limit

## Cloudflare KV

| Key | Set by | Read by | Contents |
|---|---|---|---|
| `alerted_circuits` | `nepse_bot.py` | `nepse_bot.py` | Circuit-breaker dedup, reset daily |
| `seen_notices_v2` | `notices.py` | `notices.py` | Notice dedup set |
| `seen_notices` | `ipo_alert.py` | `ipo_alert.py` | IPO notice dedup set |
| `dividend_seen` | `dividend_alert.py` | `dividend_alert.py` | Symbol → last-seen fiscal year |
| `warns_{userId}` | Worker | Worker | Warn count per user |
| `watchdog_last_dispatch` | Worker | Worker | Cooldown timestamp for the freshness watchdog |
| `site_board` | `nepse_bot.py` | Website | Live price snapshot for the dashboard |

`watch_stocks`, `alerts`, and `portfolio` keys are read by `nepse_bot.py` but nothing currently writes them — no command exists yet to populate a personal watchlist through Telegram.

## Secrets

| Secret | Lives in | Used by |
|---|---|---|
| `TELEGRAM_TOKEN` | Cloudflare + GitHub | Worker + all Python scripts |
| `ALLOWED_CHAT_ID` | Cloudflare | Worker |
| `TELEGRAM_CHAT_ID` | GitHub | Python scripts |
| `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID`, `CF_API_TOKEN` | GitHub | `nepse/kv.py` |
| `GITHUB_TOKEN` | Cloudflare | Worker, triggers `workflow_dispatch` |
| `GITHUB_REPO` | Cloudflare | Worker |

`ALLOWED_CHAT_ID` and `TELEGRAM_CHAT_ID` must be the same value, set in two places because the Worker and the scripts read secrets from different platforms.

## Web dashboard (GHANTAGHAR)

`site/` is a static, client-side dashboard with no backend or build step. During market hours it reads live board/index/sector data from KV, written by the same scripts that feed Telegram. Outside market hours it falls back to ShareSansar's static JSON, which also powers the per-symbol charts and long-range indicators regardless of time of day. Broker floorsheet and dividend history are fetched on demand.

The watchlist is the only persisted state, stored in the browser's `localStorage` — nothing touches a server. Deployed on Cloudflare Pages, auto-deployed on every push to `main` that touches `site/**`.

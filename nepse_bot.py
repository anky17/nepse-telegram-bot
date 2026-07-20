import os
import sys
import requests
from datetime import datetime
import pytz
from nepse_scraper import NepseScraper

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_KV_NAMESPACE_ID = os.environ.get("CF_KV_NAMESPACE_ID", "")

NST = pytz.timezone("Asia/Kathmandu")
DIV = "─────────────────"


def load_watch_stocks() -> list[str]:
    if CF_ACCOUNT_ID and CF_API_TOKEN and CF_KV_NAMESPACE_ID:
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
            f"/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/watch_stocks"
        )
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"}, timeout=10)
            if r.ok and r.text:
                return [s.strip().upper() for s in r.text.split(",") if s.strip()]
        except Exception as e:
            print(f"[WARN] Could not read KV watchlist: {e}")
    return [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


def fval(d: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


def ival(d: dict, *keys, default=0) -> int:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return int(float(v))
            except (ValueError, TypeError):
                pass
    return default


def tag(value: float) -> str:
    sign = "+" if value >= 0 else ""
    icon = "▲" if value >= 0 else "▼"
    return f"{icon} {sign}{value:.2f}"


def build_index_section(scraper: NepseScraper) -> str:
    try:
        indices = scraper.get_nepse_index()
        # Main NEPSE index is typically the first entry or identifiable by name
        main = next(
            (i for i in indices if str(i.get("index", "")).upper() == "NEPSE"
             or str(i.get("indexName", "")).upper() == "NEPSE"),
            indices[0] if indices else None
        )
        if not main:
            return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ No data"

        current = fval(main, "currentValue", "value", "indexValue")
        change = fval(main, "change", "absoluteChange", "pointChange")
        pct = fval(main, "perChange", "percentageChange", "changePercent")
        icon = "🟢" if change >= 0 else "🔴"
        return "\n".join([
            f"📊 <b>NEPSE Index</b>",
            DIV,
            f"{icon}  <b>{current:,.2f}</b>   {tag(change)} pts  ({tag(pct)}%)",
        ])
    except Exception as e:
        print(f"[WARN] Index error: {e}")
        return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ Unavailable"


def build_summary_section(scraper: NepseScraper) -> str:
    try:
        raw = scraper.call_endpoint("market_summary_api")
        d = raw if isinstance(raw, dict) else (raw[0] if raw else {})
        turnover = fval(d, "totalTurnover", "turnover")
        shares = ival(d, "totalTradedShares", "tradedShares", "totalShares")
        txns = ival(d, "totalTransactions", "transactions")
        return "\n".join([
            f"\n💼 <b>Market Summary</b>",
            DIV,
            f"  Turnover      Rs {turnover / 1_000_000:.2f}M",
            f"  Shares Traded {shares:,}",
            f"  Transactions  {txns:,}",
        ])
    except Exception as e:
        print(f"[WARN] Summary error: {e}")
        return ""


def build_gainers_losers_section(scraper: NepseScraper) -> str:
    lines = []

    def section(category: str, header: str, icon: str):
        try:
            items = scraper.get_top_stocks(category=category)
            rows = [f"\n{icon} <b>{header}</b>", DIV]
            for i, s in enumerate(items[:5], 1):
                sym = s.get("symbol") or s.get("stockSymbol") or "?"
                ltp = fval(s, "lastTradedPrice", "ltp", "closePrice")
                pct = fval(s, "percentageChange", "perChange", "pointChange")
                sign = "+" if pct >= 0 else ""
                rows.append(f"  {i}.  <b>{sym:<10}</b> {ltp:>8.1f}   <code>{sign}{pct:.2f}%</code>")
            return rows
        except Exception as e:
            print(f"[WARN] {category} error: {e}")
            return [f"\n{icon} <b>{header}</b>", DIV, "⚠️ Unavailable"]

    lines += section("top_gainer", "Top Gainers", "🟢")
    lines += section("top_loser", "Top Losers", "🔴")
    return "\n".join(lines)


def build_watchlist_section(scraper: NepseScraper, watch_stocks: list[str]) -> str:
    if not watch_stocks:
        return ""

    lines = [f"\n👁 <b>Your Watchlist</b>", DIV]
    for symbol in watch_stocks:
        try:
            info = scraper.get_ticker_info(symbol)
            # get_ticker_info can return the dict directly or nested under 'security'
            d = info.get("security", info) if isinstance(info, dict) else {}
            sec = info  # also check top-level fields

            ltp = fval(sec, "lastTradedPrice", "ltp") or fval(d, "lastTradedPrice", "ltp")
            pct = fval(sec, "percentageChange", "perChange") or fval(d, "percentageChange", "perChange")
            change = fval(sec, "change", "pointChange") or fval(d, "change", "pointChange")
            vol = ival(sec, "tradedShares", "totalTradedShares", "volume") or ival(d, "tradedShares", "volume")

            icon = "🟢" if change >= 0 else "🔴"
            sign = "+" if change >= 0 else ""
            lines.append(
                f"  {icon} <b>{symbol:<10}</b> {ltp:>8.2f}   <code>{sign}{pct:.2f}%</code>   Vol {vol:,}"
            )
        except Exception as e:
            print(f"[WARN] Watchlist {symbol} error: {e}")
            lines.append(f"  ⚠️ {symbol}: unavailable")
    return "\n".join(lines)


def build_broker_section(scraper: NepseScraper, watch_stocks: list[str]) -> str:
    if not watch_stocks:
        return ""

    lines = [f"\n🏦 <b>Broker Activity</b>", DIV]
    found = False

    for symbol in watch_stocks:
        try:
            stat = scraper.get_security_daily_trade_stat(symbol)
            # stat is a dict; look for broker fields
            buyers = stat.get("topBuyers") or stat.get("buyerBrokers") or []
            sellers = stat.get("topSellers") or stat.get("sellerBrokers") or []

            if not buyers and not sellers:
                continue

            found = True
            lines.append(f"\n  <b>{symbol}</b>")
            if buyers:
                b_str = "   ".join(
                    f"B{b.get('brokerNumber', b.get('memberCode', '?'))} <code>{int(b.get('quantity', 0)):,}</code>"
                    for b in buyers[:3]
                )
                lines.append(f"  🟢 Buy  — {b_str}")
            if sellers:
                s_str = "   ".join(
                    f"B{s.get('brokerNumber', s.get('memberCode', '?'))} <code>{int(s.get('quantity', 0)):,}</code>"
                    for s in sellers[:3]
                )
                lines.append(f"  🔴 Sell — {s_str}")
        except Exception as e:
            print(f"[WARN] Broker stat {symbol} error: {e}")

    return "\n".join(lines) if found else ""


def main():
    watch_stocks = load_watch_stocks()
    now_nst = datetime.now(NST)
    time_str = now_nst.strftime("%a, %d %b %Y  %I:%M %p NST")

    scraper = NepseScraper(verify_ssl=False)

    if not scraper.is_market_open():
        print("Market is closed. Skipping.")
        sys.exit(0)

    print(f"[{time_str}] Market is open. Fetching NEPSE data...")

    sections = [
        f"📈 <b>NEPSE Market Update</b>",
        f"<i>{time_str}</i>",
        build_index_section(scraper),
        build_summary_section(scraper),
        build_gainers_losers_section(scraper),
        build_watchlist_section(scraper, watch_stocks),
        build_broker_section(scraper, watch_stocks),
        f"\n<i>Next update in 30 min</i>",
    ]

    message = "\n".join(s for s in sections if s)
    send_telegram(message)
    print("Update sent to Telegram.")


if __name__ == "__main__":
    main()

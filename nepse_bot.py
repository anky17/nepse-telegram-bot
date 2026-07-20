import os
import sys
import requests
from datetime import datetime
import pytz

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_KV_NAMESPACE_ID = os.environ.get("CF_KV_NAMESPACE_ID", "")


def load_watch_stocks() -> list[str]:
    """Read watchlist from Cloudflare KV; fall back to WATCH_STOCKS env var."""
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
    # Fallback for local testing
    return [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]


WATCH_STOCKS = load_watch_stocks()

NEPSE_BASE = "https://nepalstock.com.np/api/nots"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NEPSEBot/1.0)",
    "Referer": "https://nepalstock.com.np/",
    "Accept": "application/json",
}

NST = pytz.timezone("Asia/Kathmandu")


def is_market_open() -> bool:
    now = datetime.now(NST)
    if now.weekday() in (5, 6):  # Saturday=5, Sunday=6 are closed
        return False
    market_start = now.replace(hour=11, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=0, second=0, microsecond=0)
    return market_start <= now <= market_end


def fetch(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] Failed to fetch {url}: {e}")
        return None


DIV = "─────────────────"


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=10)
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


def tag(value: float, fmt: str = ".2f") -> str:
    sign = "+" if value >= 0 else ""
    icon = "▲" if value >= 0 else "▼"
    return f"{icon} {sign}{value:{fmt}}"


def build_index_section() -> str:
    data = fetch(f"{NEPSE_BASE}/nepse-data/index")
    if not data:
        return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ Unavailable"

    index = data if isinstance(data, dict) and "index" in data else data.get("data", data)
    try:
        current = float(index.get("currentValue") or index.get("index") or 0)
        change = float(index.get("change") or index.get("absoluteChange") or 0)
        pct = float(index.get("perChange") or index.get("percentageChange") or 0)
        icon = "🟢" if change >= 0 else "🔴"
        return "\n".join([
            f"📊 <b>NEPSE Index</b>",
            DIV,
            f"{icon}  <b>{current:,.2f}</b>   {tag(change)} pts  ({tag(pct)}%)",
        ])
    except Exception as e:
        print(f"[WARN] Index parse error: {e} | raw: {data}")
        return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ Parse error"


def build_market_summary_section() -> str:
    data = fetch(f"{NEPSE_BASE}/market-open")
    if not data:
        return ""
    try:
        d = data.get("data") or data
        turnover = float(d.get("totalTurnover") or 0)
        shares = int(d.get("totalTradedShares") or 0)
        txns = int(d.get("totalTransactions") or 0)
        return "\n".join([
            f"\n💼 <b>Market Summary</b>",
            DIV,
            f"  Turnover      Rs {turnover / 1_000_000:.2f}M",
            f"  Shares Traded {shares:,}",
            f"  Transactions  {txns:,}",
        ])
    except Exception as e:
        print(f"[WARN] Summary parse error: {e}")
        return ""


def build_gainers_losers_section() -> str:
    gainers_data = fetch(f"{NEPSE_BASE}/top25GainerLoser/top25Gainer")
    losers_data = fetch(f"{NEPSE_BASE}/top25GainerLoser/top25Loser")

    def parse_list(data, header, icon, top_n=5):
        if not data:
            return [f"\n{icon} <b>{header}</b>", DIV, "⚠️ Unavailable"]
        items = data.get("data") or data if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = list(items.values())[0] if items else []
        rows = [f"\n{icon} <b>{header}</b>", DIV]
        for i, s in enumerate(items[:top_n], 1):
            sym = s.get("symbol") or s.get("stockSymbol") or "?"
            pct = float(s.get("pointChange") or s.get("percentageChange") or 0)
            ltp = float(s.get("lastTradedPrice") or s.get("ltp") or 0)
            sign = "+" if pct >= 0 else ""
            rows.append(f"  {i}.  <b>{sym:<10}</b> {ltp:>8.1f}   <code>{sign}{pct:.2f}%</code>")
        return rows

    lines = parse_list(gainers_data, "Top Gainers", "🟢")
    lines += parse_list(losers_data, "Top Losers", "🔴")
    return "\n".join(lines)


def build_watch_stocks_section() -> str:
    if not WATCH_STOCKS:
        return ""

    lines = [f"\n👁 <b>Your Watchlist</b>", DIV]
    for symbol in WATCH_STOCKS:
        data = fetch(f"{NEPSE_BASE}/security/symbol/{symbol}")
        if not data:
            lines.append(f"  ⚠️ {symbol}: unavailable")
            continue
        try:
            d = data.get("data") or data
            if isinstance(d, list):
                d = d[0]
            ltp = float(d.get("lastTradedPrice") or d.get("ltp") or 0)
            change = float(d.get("change") or d.get("pointChange") or 0)
            pct = float(d.get("perChange") or d.get("percentageChange") or 0)
            vol = int(d.get("totalTradeQuantity") or d.get("volume") or 0)
            icon = "🟢" if change >= 0 else "🔴"
            sign = "+" if change >= 0 else ""
            lines.append(
                f"  {icon} <b>{symbol:<10}</b> {ltp:>8.2f}   <code>{sign}{pct:.2f}%</code>   Vol {vol:,}"
            )
        except Exception as e:
            print(f"[WARN] {symbol} parse error: {e}")
            lines.append(f"  ⚠️ {symbol}: parse error")
    return "\n".join(lines)


def build_broker_floorsheet_section() -> str:
    if not WATCH_STOCKS:
        return ""

    lines = [f"\n🏦 <b>Broker Activity</b>", DIV]

    for symbol in WATCH_STOCKS:
        sec_data = fetch(f"{NEPSE_BASE}/security/symbol/{symbol}")
        if not sec_data:
            continue
        try:
            d = sec_data.get("data") or sec_data
            if isinstance(d, list):
                d = d[0]
            sec_id = d.get("id") or d.get("securityId")
        except Exception:
            continue

        if not sec_id:
            continue

        floor_data = fetch(f"{NEPSE_BASE}/floorsheet/{sec_id}?&startDate=&endDate=&page=0&size=50&sort=contractId,desc")
        if not floor_data:
            continue

        try:
            items = floor_data.get("floorsheets", {}).get("content") or []
            if not items:
                lines.append(f"\n  <b>{symbol}</b>: no trades yet today")
                continue

            broker_buy: dict[str, float] = {}
            broker_sell: dict[str, float] = {}
            for row in items:
                buyer = str(row.get("buyerMemberId") or row.get("buyerBrokerNo") or "?")
                seller = str(row.get("sellerMemberId") or row.get("sellerBrokerNo") or "?")
                qty = float(row.get("contractQuantity") or 0)
                broker_buy[buyer] = broker_buy.get(buyer, 0) + qty
                broker_sell[seller] = broker_sell.get(seller, 0) + qty

            top_buyers = sorted(broker_buy.items(), key=lambda x: x[1], reverse=True)[:3]
            top_sellers = sorted(broker_sell.items(), key=lambda x: x[1], reverse=True)[:3]

            lines.append(f"\n  <b>{symbol}</b>")
            lines.append("  🟢 Buy  — " + "   ".join(f"B{b} <code>{q:,.0f}</code>" for b, q in top_buyers))
            lines.append("  🔴 Sell — " + "   ".join(f"B{s} <code>{q:,.0f}</code>" for s, q in top_sellers))
        except Exception as e:
            print(f"[WARN] Floorsheet parse error for {symbol}: {e}")

    return "\n".join(lines) if len(lines) > 2 else ""


def main():
    now_nst = datetime.now(NST)
    time_str = now_nst.strftime("%a, %d %b %Y  %I:%M %p NST")

    if not is_market_open():
        print("Market is closed. Skipping.")
        sys.exit(0)

    print(f"[{time_str}] Market is open. Fetching NEPSE data...")

    sections = [
        f"📈 <b>NEPSE Market Update</b>",
        f"<i>{time_str}</i>",
        build_index_section(),
        build_market_summary_section(),
        build_gainers_losers_section(),
        build_watch_stocks_section(),
        build_broker_floorsheet_section(),
        f"\n<i>Next update in 30 min</i>",
    ]

    message = "\n".join(s for s in sections if s is not None)
    send_telegram(message)
    print("Update sent to Telegram.")


if __name__ == "__main__":
    main()

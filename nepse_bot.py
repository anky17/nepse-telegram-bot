import os
import sys
import requests
from datetime import datetime
import pytz

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Add comma-separated symbols here, e.g. "NABIL,NICA,SANIMA"
WATCH_STOCKS = [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]

NEPSE_BASE = "https://nepalstock.com.np/api/nots"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NEPSEBot/1.0)",
    "Referer": "https://nepalstock.com.np/",
    "Accept": "application/json",
}

NST = pytz.timezone("Asia/Kathmandu")


def is_market_open() -> bool:
    now = datetime.now(NST)
    if now.weekday() in (4, 5):  # Friday=4, Saturday=5 are holidays in Nepal
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


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=10)
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


def format_change(value: float) -> str:
    arrow = "🟢" if value >= 0 else "🔴"
    sign = "+" if value >= 0 else ""
    return f"{arrow} {sign}{value:.2f}"


def build_index_section() -> str:
    data = fetch(f"{NEPSE_BASE}/nepse-data/index")
    if not data:
        return "⚠️ <b>NEPSE Index:</b> unavailable\n"

    # The response wraps data inside keys — handle both shapes
    index = data if isinstance(data, dict) and "index" in data else data.get("data", data)
    try:
        current = float(index.get("currentValue") or index.get("index") or 0)
        change = float(index.get("change") or index.get("absoluteChange") or 0)
        pct = float(index.get("perChange") or index.get("percentageChange") or 0)
        lines = [
            "📊 <b>NEPSE Index</b>",
            f"  Value : <b>{current:.2f}</b>",
            f"  Change: {format_change(change)} ({format_change(pct)}%)",
        ]
        return "\n".join(lines)
    except Exception as e:
        print(f"[WARN] Index parse error: {e} | raw: {data}")
        return "⚠️ <b>NEPSE Index:</b> parse error\n"


def build_market_summary_section() -> str:
    data = fetch(f"{NEPSE_BASE}/market-open")
    if not data:
        return ""
    try:
        d = data.get("data") or data
        turnover = float(d.get("totalTurnover") or 0)
        shares = int(d.get("totalTradedShares") or 0)
        txns = int(d.get("totalTransactions") or 0)
        lines = [
            "\n💼 <b>Market Summary</b>",
            f"  Turnover    : Rs {turnover:,.0f}",
            f"  Traded Shares: {shares:,}",
            f"  Transactions : {txns:,}",
        ]
        return "\n".join(lines)
    except Exception as e:
        print(f"[WARN] Summary parse error: {e}")
        return ""


def build_gainers_losers_section() -> str:
    gainers_data = fetch(f"{NEPSE_BASE}/top25GainerLoser/top25Gainer")
    losers_data = fetch(f"{NEPSE_BASE}/top25GainerLoser/top25Loser")

    lines = []

    def parse_list(data, label, top_n=5):
        if not data:
            return [f"⚠️ <b>{label}:</b> unavailable"]
        items = data.get("data") or data if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = list(items.values())[0] if items else []
        result = [f"\n{'🟢' if 'Gainer' in label else '🔴'} <b>{label} (Top {top_n})</b>"]
        for i, stock in enumerate(items[:top_n], 1):
            symbol = stock.get("symbol") or stock.get("stockSymbol") or "?"
            pct = float(stock.get("pointChange") or stock.get("percentageChange") or 0)
            ltp = float(stock.get("lastTradedPrice") or stock.get("ltp") or 0)
            sign = "+" if pct >= 0 else ""
            result.append(f"  {i}. <b>{symbol}</b>  LTP: {ltp:.1f}  ({sign}{pct:.2f}%)")
        return result

    lines += parse_list(gainers_data, "Top Gainers")
    lines += parse_list(losers_data, "Top Losers")
    return "\n".join(lines)


def build_watch_stocks_section() -> str:
    if not WATCH_STOCKS:
        return ""

    lines = ["\n👁 <b>Your Watchlist</b>"]
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
            sign = "+" if change >= 0 else ""
            icon = "🟢" if change >= 0 else "🔴"
            lines.append(
                f"  {icon} <b>{symbol}</b>  LTP: {ltp:.2f}  {sign}{change:.2f} ({sign}{pct:.2f}%)  Vol: {vol:,}"
            )
        except Exception as e:
            print(f"[WARN] {symbol} parse error: {e}")
            lines.append(f"  ⚠️ {symbol}: parse error")
    return "\n".join(lines)


def build_broker_floorsheet_section() -> str:
    if not WATCH_STOCKS:
        return ""

    lines = ["\n🏦 <b>Broker Activity (Today's Trades)</b>"]

    for symbol in WATCH_STOCKS:
        # Get security ID first
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

        floor_data = fetch(f"{NEPSE_BASE}/floorsheet/{sec_id}?&startDate=&endDate=&page=0&size=10&sort=contractId,desc")
        if not floor_data:
            continue

        try:
            items = floor_data.get("floorsheets", {}).get("content") or []
            if not items:
                lines.append(f"\n  <b>{symbol}</b>: no trades yet today")
                continue

            lines.append(f"\n  <b>{symbol}</b> — recent broker trades:")
            # Aggregate buy/sell by broker
            broker_buy: dict[str, float] = {}
            broker_sell: dict[str, float] = {}
            for row in items[:50]:
                buyer = str(row.get("buyerMemberId") or row.get("buyerBrokerNo") or "?")
                seller = str(row.get("sellerMemberId") or row.get("sellerBrokerNo") or "?")
                qty = float(row.get("contractQuantity") or 0)
                broker_buy[buyer] = broker_buy.get(buyer, 0) + qty
                broker_sell[seller] = broker_sell.get(seller, 0) + qty

            top_buyers = sorted(broker_buy.items(), key=lambda x: x[1], reverse=True)[:3]
            top_sellers = sorted(broker_sell.items(), key=lambda x: x[1], reverse=True)[:3]

            buy_str = "  ".join(f"B{b}:{q:,.0f}" for b, q in top_buyers)
            sell_str = "  ".join(f"B{s}:{q:,.0f}" for s, q in top_sellers)
            lines.append(f"    🟢 Buyers : {buy_str or 'none'}")
            lines.append(f"    🔴 Sellers: {sell_str or 'none'}")
        except Exception as e:
            print(f"[WARN] Floorsheet parse error for {symbol}: {e}")

    return "\n".join(lines) if len(lines) > 1 else ""


def main():
    now_nst = datetime.now(NST)
    time_str = now_nst.strftime("%I:%M %p NST, %a %d %b %Y")

    if not is_market_open():
        print("Market is closed. Skipping.")
        sys.exit(0)

    print(f"[{time_str}] Market is open. Fetching NEPSE data...")

    sections = [
        f"<b>NEPSE Update</b> | {time_str}",
        "",
        build_index_section(),
        build_market_summary_section(),
        build_gainers_losers_section(),
        build_watch_stocks_section(),
        build_broker_floorsheet_section(),
    ]

    message = "\n".join(s for s in sections if s is not None)
    send_telegram(message)
    print("Update sent to Telegram.")


if __name__ == "__main__":
    main()

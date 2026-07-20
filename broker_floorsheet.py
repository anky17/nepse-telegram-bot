"""Broker analysis using Nepse-All-Scraper floorsheet CSV — reliable, no broken API."""
import os
import sys
import csv
import io
import requests
import urllib3
from datetime import datetime, timedelta
import pytz

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PRINT_MODE = "--print" in sys.argv
ARGS = [a for a in sys.argv[1:] if a != "--print"]
TELEGRAM_TOKEN = "" if PRINT_MODE else os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = "" if PRINT_MODE else os.environ["TELEGRAM_CHAT_ID"]
NST = pytz.timezone("Asia/Kathmandu")
DIV = "─────────────────"
BASE_URL = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/floorsheet"

SYMBOLS = (
    [s.strip().upper() for s in ARGS if s.strip()]
    or [s.strip().upper() for s in os.environ.get("SYMBOLS", "").split(",") if s.strip()]
)


def send_telegram(msg: str):
    if PRINT_MODE:
        print(msg)
        print()
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )


def fetch_latest_floorsheet() -> tuple[str | None, list[dict]]:
    """Try last 5 weekdays to find the most recent floorsheet."""
    today = datetime.now(NST).date()
    for i in range(7):
        date = today - timedelta(days=i)
        if date.weekday() >= 5:  # skip weekends
            continue
        url = f"{BASE_URL}/floorsheet_{date}.csv"
        try:
            r = requests.get(url, timeout=15)
            if r.ok:
                reader = csv.DictReader(io.StringIO(r.text))
                rows = list(reader)
                if rows:
                    print(f"Loaded floorsheet for {date} ({len(rows):,} rows)")
                    return str(date), rows
        except Exception as e:
            print(f"[WARN] {date}: {e}")
    return None, []


def analyze_symbol(symbol: str, rows: list[dict], date: str) -> str:
    trades = [r for r in rows if r.get("stock_symbol", "").strip().upper() == symbol]
    if not trades:
        return f"⚠️ <b>{symbol}</b>: No trades found in floorsheet for {date}"

    buy: dict[str, float] = {}
    sell: dict[str, float] = {}
    total_qty = 0.0

    for t in trades:
        try:
            qty = float(t.get("quantity", 0) or 0)
            buyer = str(t.get("buyer", "")).strip()
            seller = str(t.get("seller", "")).strip()
            if buyer:
                buy[buyer] = buy.get(buyer, 0) + qty
            if seller:
                sell[seller] = sell.get(seller, 0) + qty
            total_qty += qty
        except (ValueError, TypeError):
            continue

    if total_qty == 0:
        return f"⚠️ <b>{symbol}</b>: No valid trade data"

    # Net position per broker
    all_brokers = set(buy) | set(sell)
    net = {b: buy.get(b, 0) - sell.get(b, 0) for b in all_brokers}

    top_buyers = sorted(buy.items(), key=lambda x: x[1], reverse=True)[:5]
    top_sellers = sorted(sell.items(), key=lambda x: x[1], reverse=True)[:5]
    accumulators = sorted([(b, q) for b, q in net.items() if q > 0], key=lambda x: x[1], reverse=True)[:3]
    distributors = sorted([(b, q) for b, q in net.items() if q < 0], key=lambda x: x[1])[:3]

    top3_buy_vol = sum(v for _, v in top_buyers[:3])
    concentration = (top3_buy_vol / total_qty * 100) if total_qty else 0
    conc_flag = "🔴" if concentration > 40 else "🟡" if concentration > 25 else "🟢"

    lines = [
        f"🏦 <b>Broker Analysis — {symbol}</b>",
        f"<i>{date}  ·  {int(total_qty):,} shares traded  ·  {len(trades):,} contracts</i>",
        DIV,
        "",
        "📥 <b>Top Buyers</b>",
    ]
    for broker, qty in top_buyers:
        pct = qty / total_qty * 100
        lines.append(f"  B{broker:<4}  {int(qty):>8,} shares  ({pct:.1f}%)")

    lines += ["", "📤 <b>Top Sellers</b>"]
    for broker, qty in top_sellers:
        pct = qty / total_qty * 100
        lines.append(f"  B{broker:<4}  {int(qty):>8,} shares  ({pct:.1f}%)")

    if accumulators:
        lines += ["", "🟢 <b>Net Accumulators</b>"]
        for broker, qty in accumulators:
            lines.append(f"  B{broker:<4}  +{int(qty):>8,} net")

    if distributors:
        lines += ["", "🔴 <b>Net Distributors</b>"]
        for broker, qty in distributors:
            lines.append(f"  B{broker:<4}  {int(qty):>8,} net")

    lines += [
        "",
        f"{conc_flag} <b>Concentration</b>: top 3 brokers = {concentration:.1f}% of volume",
        "<i>Source: Nepse-All-Scraper floorsheet</i>",
    ]
    return "\n".join(lines)


def main():
    if not SYMBOLS:
        print("No symbols provided. Set SYMBOLS env var.")
        sys.exit(1)

    date, rows = fetch_latest_floorsheet()
    if not rows:
        send_telegram("⚠️ Could not load floorsheet data. Please try again later.")
        return

    for symbol in SYMBOLS:
        msg = analyze_symbol(symbol, rows, date)
        send_telegram(msg)
        print(f"Sent broker report for {symbol}")


if __name__ == "__main__":
    main()

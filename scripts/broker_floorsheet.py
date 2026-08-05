"""Broker analysis using Nepse-All-Scraper floorsheet CSV."""
import os
import sys
import csv
import io
import requests
from datetime import datetime, timedelta
from nepse.common import DIV, NST
from nepse.telegram import send

BASE_URL = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/floorsheet"
ARGS = [a for a in sys.argv[1:] if a != "--print"]
SYMBOLS = (
    [s.strip().upper() for s in ARGS if s.strip()]
    or [s.strip().upper() for s in os.environ.get("SYMBOLS", "").split(",") if s.strip()]
)


def fetch_latest_floorsheet() -> tuple[str | None, list[dict]]:
    today = datetime.now(NST).date()
    for i in range(7):
        date = today - timedelta(days=i)
        if date.weekday() >= 5:
            continue
        try:
            r = requests.get(f"{BASE_URL}/floorsheet_{date}.csv", timeout=15)
            if r.ok:
                rows = list(csv.DictReader(io.StringIO(r.text)))
                if rows:
                    print(f"Loaded floorsheet for {date} ({len(rows):,} rows)")
                    return str(date), rows
        except Exception as e:
            print(f"[WARN] {date}: {e}")
    return None, []


def analyze(symbol: str, rows: list[dict], date: str) -> str:
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

    net = {b: buy.get(b, 0) - sell.get(b, 0) for b in set(buy) | set(sell)}
    accumulators = sorted([(b, q) for b, q in net.items() if q > 0], key=lambda x: x[1], reverse=True)[:3]
    distributors = sorted([(b, q) for b, q in net.items() if q < 0], key=lambda x: x[1])[:3]

    activity = {b: buy.get(b, 0) + sell.get(b, 0) for b in set(buy) | set(sell)}
    top3_vol = sum(v for _, v in sorted(activity.items(), key=lambda x: x[1], reverse=True)[:3])
    concentration = top3_vol / (total_qty * 2) * 100
    conc_flag = "🔴" if concentration > 40 else "🟡" if concentration > 25 else "🟢"

    total_accum   = sum(q for _, q in accumulators)
    total_distrib = abs(sum(q for _, q in distributors))
    if total_accum > total_distrib * 1.5:
        verdict = "🟢 <b>Bullish lean</b> — institutions are quietly <b>accumulating</b>"
    elif total_distrib > total_accum * 1.5:
        verdict = "🔴 <b>Bearish lean</b> — institutions are <b>offloading</b> shares"
    else:
        verdict = "🟡 <b>Neutral</b> — buying and selling are roughly balanced"

    lines = [
        f"🏦 <b>Broker Analysis — {symbol}</b>",
        f"<i>{date}  ·  {int(total_qty):,} shares  ·  {len(trades):,} contracts</i>",
        "<i>Each 'Broker' is a licensed stockbroker firm registered with NEPSE</i>",
        "",
        "📊 <b>Broker Positions</b>  <i>— net shares bought vs. sold today</i>",
        "<i>Net = Buy − Sell. Positive = building a position, negative = exiting one.</i>",
        DIV,
    ]
    for broker, qty in accumulators + distributors:
        sign = "+" if qty >= 0 else "-"
        lines.append(
            f"  Broker {broker:<4}  Buy {int(buy.get(broker, 0)):>7,}  "
            f"Sell {int(sell.get(broker, 0)):>7,}  Net {sign}{int(abs(qty)):>7,}"
        )

    if concentration > 50:
        conc_note = "🚨 Very HIGH — 3 brokers handled over half the trading. Could be institutional or manipulative."
    elif concentration > 25:
        conc_note = "⚠️ MODERATE — a few big players dominate. Worth watching closely."
    else:
        conc_note = "✅ LOW — trading spread across many brokers. Healthy and normal."

    lines += [
        "",
        f"{conc_flag} <b>Market Concentration</b>",
        DIV,
        f"  Top 3 brokers handled <b>{concentration:.1f}%</b> of all {symbol} trading (buy + sell combined).",
        f"  {conc_note}",
        "",
        "🧠 <b>Bottom Line</b>",
        DIV,
        f"  {verdict}",
        "",
        "<i>Source: Nepse-All-Scraper floorsheet</i>",
    ]
    return "\n".join(lines)


def main():
    if not SYMBOLS:
        print("No symbols provided. Set SYMBOLS env var or pass as CLI args.")
        sys.exit(1)

    date, rows = fetch_latest_floorsheet()
    if not rows:
        send("⚠️ Could not load floorsheet data. Please try again later.")
        return

    for symbol in SYMBOLS:
        send(analyze(symbol, rows, date))
        print(f"Sent broker report for {symbol}")


if __name__ == "__main__":
    main()

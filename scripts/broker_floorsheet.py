"""Broker analysis using Nepse-All-Scraper floorsheet CSV."""
import os
import sys
import csv
import io
import requests
from datetime import datetime, timedelta
from nepse.common import DIV, NST
from nepse.telegram import send, PRINT_MODE

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
    top_buyers = sorted(buy.items(), key=lambda x: x[1], reverse=True)[:5]
    top_sellers = sorted(sell.items(), key=lambda x: x[1], reverse=True)[:5]
    accumulators = sorted([(b, q) for b, q in net.items() if q > 0], key=lambda x: x[1], reverse=True)[:3]
    distributors = sorted([(b, q) for b, q in net.items() if q < 0], key=lambda x: x[1])[:3]

    top3_vol = sum(v for _, v in top_buyers[:3])
    concentration = top3_vol / total_qty * 100
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
        "📥 <b>Top Buyers</b>  <i>— who bought the most shares today</i>",
        DIV,
    ]
    for i, (broker, qty) in enumerate(top_buyers, 1):
        pct = qty / total_qty * 100
        bar = "▓" * min(int(pct / 2), 10)
        lines.append(f"  {i}. Broker {broker:<4}  {int(qty):>8,} shares  {bar} {pct:.1f}%")

    lines += [
        "",
        "📤 <b>Top Sellers</b>  <i>— who sold the most shares today</i>",
        DIV,
    ]
    for i, (broker, qty) in enumerate(top_sellers, 1):
        pct = qty / total_qty * 100
        bar = "▓" * min(int(pct / 2), 10)
        lines.append(f"  {i}. Broker {broker:<4}  {int(qty):>8,} shares  {bar} {pct:.1f}%")

    if accumulators:
        lines += [
            "",
            "🟢 <b>Real Buyers</b>  <i>— bought MORE than they sold (net holders)</i>",
            "<i>These brokers ended the day with extra shares — a bullish signal</i>",
            DIV,
        ]
        for broker, qty in accumulators:
            lines.append(f"  Broker {broker:<4}  kept  +{int(qty):>7,} extra shares")

    if distributors:
        lines += [
            "",
            "🔴 <b>Real Sellers</b>  <i>— sold MORE than they bought (net exiters)</i>",
            "<i>These brokers ended the day with fewer shares — a bearish signal</i>",
            DIV,
        ]
        for broker, qty in distributors:
            lines.append(f"  Broker {broker:<4}  shed   {int(abs(qty)):>7,} shares net")

    if concentration > 50:
        conc_note = "🚨 Very HIGH — 3 brokers control over half the volume. Could be institutional or manipulative."
    elif concentration > 25:
        conc_note = "⚠️ MODERATE — a few big players dominate. Worth watching closely."
    else:
        conc_note = "✅ LOW — trading spread across many brokers. Healthy and normal."

    lines += [
        "",
        f"{conc_flag} <b>Market Concentration</b>",
        DIV,
        f"  Top 3 brokers controlled <b>{concentration:.1f}%</b> of all {symbol} trading.",
        f"  {conc_note}",
        "<i>High concentration can mean a big institution is making a move.</i>",
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

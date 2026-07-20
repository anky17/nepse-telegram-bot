import os
import sys
import urllib3
import requests
from datetime import datetime
import pytz
from nepse_scraper import NepseScraper
from kv_utils import kv_get, kv_put_json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NST = pytz.timezone("Asia/Kathmandu")

# --print flag: print to console instead of sending to Telegram (for local testing)
PRINT_MODE = "--print" in sys.argv
args = [a for a in sys.argv[1:] if a != "--print"]

TELEGRAM_TOKEN = "" if PRINT_MODE else os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = "" if PRINT_MODE else os.environ["TELEGRAM_CHAT_ID"]

# If specific symbols passed via CLI (from /broker command), use them
CLI_SYMBOLS = [s.strip().upper() for s in args if s.strip()]

DIV = "─────────────────"


def send_market_close_summary(scraper: NepseScraper) -> None:
    """Full-day recap sent once at 3:15 PM NST."""
    now = datetime.now(NST)
    date_str = now.strftime("%a, %d %b %Y")
    lines = [f"🔔 <b>Market Closed — {date_str}</b>", DIV]

    # Index final value
    try:
        indices = scraper.get_nepse_index()
        idx = next((i for i in indices if i.get("id") == 58), {})
        current = float(idx.get("currentValue") or 0)
        change = float(idx.get("change") or 0)
        pct = float(idx.get("perChange") or 0)
        prev = float(idx.get("previousClose") or idx.get("close") or 0)
        icon = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        lines += [
            f"📊 NEPSE Index",
            f"   {icon} <b>{current:,.2f}</b>  ({sign}{pct:.2f}%)  open {prev:,.2f}",
        ]
    except Exception:
        pass

    # Market summary
    try:
        rows = scraper.call_endpoint("market_summary_api")
        lookup = {r["detail"]: r["value"] for r in rows if "detail" in r}
        turnover = float(lookup.get("Total Turnover Rs:", 0))
        shares = int(lookup.get("Total Traded Shares", 0))
        txns = int(lookup.get("Total Transactions", 0))
        lines += [
            "",
            f"💼 Today's Market",
            f"   Turnover      Rs {turnover / 1_000_000:.2f}M",
            f"   Shares Traded {shares:,}",
            f"   Transactions  {txns:,}",
        ]
    except Exception:
        pass

    # Top gainer and loser of the day
    try:
        gainers = scraper.get_top_stocks(category="top_gainer")
        losers = scraper.get_top_stocks(category="top_loser")
        if gainers:
            g = gainers[0]
            lines.append(f"\n🏆 Best:  <b>{g.get('symbol')}</b>  +{g.get('percentageChange', 0):.2f}%  LTP {g.get('ltp', 0):.1f}")
        if losers:
            l = losers[0]
            lines.append(f"📉 Worst: <b>{l.get('symbol')}</b>  {l.get('percentageChange', 0):.2f}%  LTP {l.get('ltp', 0):.1f}")
    except Exception:
        pass

    lines.append("\n<i>Broker report for your watchlist follows below.</i>")

    send_telegram("\n".join(lines))

    # Reset daily circuit breaker tracking
    kv_put_json("alerted_circuits", {})


def load_watch_stocks() -> list[str]:
    if CLI_SYMBOLS:
        return CLI_SYMBOLS
    text = kv_get("watch_stocks", "")
    if text:
        return [s.strip().upper() for s in text.split(",") if s.strip()]
    return [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]


def send_telegram(message: str):
    if PRINT_MODE:
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


def analyze_floorsheet(scraper: NepseScraper, symbol: str) -> str:
    try:
        scraper._get_security_map()
        sec_id = scraper._security_map.get(symbol)
        if not sec_id:
            return f"⚠️ <b>{symbol}</b>: symbol not found"

        # Fetch all floorsheet pages for today
        all_rows = []
        page = 0
        while True:
            scraper.register_endpoint(
                f"_fs_{symbol}",
                "/api/nots/nepse-data/floorsheet",
                method="GET"
            )
            result = scraper.call_endpoint(
                f"_fs_{symbol}",
                params={"securityId": sec_id, "page": page, "size": 500, "sort": "contractId,desc"}
            )

            # Handle both list and paginated dict response
            if isinstance(result, list):
                rows = result
                has_more = False
            elif isinstance(result, dict):
                content = result.get("floorsheets", result.get("content", result.get("data", [])))
                if isinstance(content, dict):
                    rows = content.get("content", [])
                else:
                    rows = content or []
                total_pages = result.get("totalPages", 1)
                has_more = page < total_pages - 1
            else:
                rows = []
                has_more = False

            if not rows:
                break
            all_rows.extend(rows)
            if not has_more or page >= 19:  # cap at 10,000 rows
                break
            page += 1

        if not all_rows:
            return f"⚠️ <b>{symbol}</b>: no floorsheet data yet\n<i>NEPSE publishes floorsheet data in the evening (~5–8 PM NST). Try /broker later.</i>"

        # Aggregate by broker
        buy: dict[str, dict] = {}
        sell: dict[str, dict] = {}
        total_qty = 0

        for row in all_rows:
            buyer = str(row.get("buyerMemberId") or row.get("buyerBrokerNo") or "?")
            seller = str(row.get("sellerMemberId") or row.get("sellerBrokerNo") or "?")
            qty = float(row.get("contractQuantity") or row.get("quantity") or 0)
            amt = float(row.get("contractAmount") or row.get("amount") or 0)
            total_qty += qty

            if buyer not in buy:
                buy[buyer] = {"qty": 0, "amt": 0}
            buy[buyer]["qty"] += qty
            buy[buyer]["amt"] += amt

            if seller not in sell:
                sell[seller] = {"qty": 0, "amt": 0}
            sell[seller]["qty"] += qty
            sell[seller]["amt"] += amt

        if total_qty == 0:
            return f"⚠️ <b>{symbol}</b>: no trades found in floorsheet"

        # Net position: accumulated (bought > sold) vs distributed (sold > bought)
        all_brokers = set(buy) | set(sell)
        net: dict[str, float] = {
            b: buy.get(b, {}).get("qty", 0) - sell.get(b, {}).get("qty", 0)
            for b in all_brokers
        }

        top_buyers = sorted(buy.items(), key=lambda x: x[1]["qty"], reverse=True)[:5]
        top_sellers = sorted(sell.items(), key=lambda x: x[1]["qty"], reverse=True)[:5]
        top_accum = sorted([(b, v) for b, v in net.items() if v > 0], key=lambda x: x[1], reverse=True)[:3]
        top_distrib = sorted([(b, v) for b, v in net.items() if v < 0], key=lambda x: x[1])[:3]

        # Concentration: top 3 buyers' share of total volume
        top3_qty = sum(v["qty"] for _, v in top_buyers[:3])
        concentration = (top3_qty / total_qty * 100) if total_qty else 0
        conc_flag = "🚨" if concentration > 50 else ("⚠️" if concentration > 30 else "✅")

        lines = [
            f"🏦 <b>Broker Analysis — {symbol}</b>",
            f"<i>Total Traded: {int(total_qty):,} shares | {len(all_rows):,} transactions</i>",
            "",
            f"🟢 <b>Top Buyers</b>",
            DIV,
        ]
        for broker, data in top_buyers:
            pct = data["qty"] / total_qty * 100
            lines.append(f"  B{broker:<4}  {int(data['qty']):>8,} shares  ({pct:.1f}%)")

        lines += ["", f"🔴 <b>Top Sellers</b>", DIV]
        for broker, data in top_sellers:
            pct = data["qty"] / total_qty * 100
            lines.append(f"  B{broker:<4}  {int(data['qty']):>8,} shares  ({pct:.1f}%)")

        lines += ["", f"📥 <b>Net Accumulators</b> (bought more than sold)", DIV]
        for broker, qty in top_accum:
            lines.append(f"  B{broker:<4}  +{int(qty):>8,} shares net")

        lines += ["", f"📤 <b>Net Distributors</b> (sold more than bought)", DIV]
        for broker, qty in top_distrib:
            lines.append(f"  B{broker:<4}  {int(qty):>8,} shares net")

        lines += [
            "",
            f"{conc_flag} <b>Concentration</b>: top 3 brokers = {concentration:.1f}% of volume",
        ]
        if concentration > 50:
            lines.append("  <i>High concentration — possible manipulation or institutional play</i>")
        elif concentration > 30:
            lines.append("  <i>Moderate concentration — worth monitoring</i>")
        else:
            lines.append("  <i>Distributed trading — normal activity</i>")

        return "\n".join(lines)

    except Exception as e:
        print(f"[ERROR] {symbol} broker analysis failed: {e}")
        return f"⚠️ <b>{symbol}</b>: analysis failed — {e}"


def main():
    scraper = NepseScraper(verify_ssl=False)

    # Send market close summary only on the scheduled daily run (not on /broker command)
    if not CLI_SYMBOLS:
        send_market_close_summary(scraper)

    symbols = load_watch_stocks()
    if not symbols:
        print("No symbols to analyze.")
        sys.exit(0)

    print(f"Analyzing broker activity for: {', '.join(symbols)}")

    for symbol in symbols:
        print(f"  → {symbol}")
        report = analyze_floorsheet(scraper, symbol)
        send_telegram(report)

    print("Done.")


if __name__ == "__main__":
    main()

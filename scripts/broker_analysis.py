"""Market close summary and broker floorsheet analysis via NEPSE API."""
import os
import sys
from datetime import datetime
from nepse_scraper import NepseScraper
from nepse.common import get_scraper, DIV, NST
from nepse.telegram import send
from nepse.kv import get as kv_get, put_json as kv_put_json

args = [a for a in sys.argv[1:] if a != "--print"]
CLI_SYMBOLS = [s.strip().upper() for s in args if s.strip()]


def send_market_close_summary(scraper: NepseScraper) -> None:
    now = datetime.now(NST)
    date_str = now.strftime("%a, %d %b %Y")
    lines = [f"🔔 <b>Market Closed — {date_str}</b>", DIV]

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
    send("\n".join(lines))

    kv_put_json("alerted_circuits", {})


def load_watch_stocks() -> list[str]:
    if CLI_SYMBOLS:
        return CLI_SYMBOLS
    text = kv_get("watch_stocks", "")
    if text:
        return [s.strip().upper() for s in text.split(",") if s.strip()]
    return [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]


def analyze_floorsheet(scraper: NepseScraper, symbol: str) -> str:
    try:
        scraper._get_security_map()
        sec_id = scraper._security_map.get(symbol)
        if not sec_id:
            return f"⚠️ <b>{symbol}</b>: symbol not found"

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
            if not has_more or page >= 19:
                break
            page += 1

        if not all_rows:
            return f"⚠️ <b>{symbol}</b>: no floorsheet data yet\n<i>NEPSE publishes floorsheet data in the evening (~5–8 PM NST). Try /broker later.</i>"

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

        all_brokers = set(buy) | set(sell)
        net: dict[str, float] = {
            b: buy.get(b, {}).get("qty", 0) - sell.get(b, {}).get("qty", 0)
            for b in all_brokers
        }

        top_accum = sorted([(b, v) for b, v in net.items() if v > 0], key=lambda x: x[1], reverse=True)[:3]
        top_distrib = sorted([(b, v) for b, v in net.items() if v < 0], key=lambda x: x[1])[:3]

        activity = {b: buy.get(b, {}).get("qty", 0) + sell.get(b, {}).get("qty", 0) for b in all_brokers}
        top3_qty = sum(v for _, v in sorted(activity.items(), key=lambda x: x[1], reverse=True)[:3])
        concentration = (top3_qty / (total_qty * 2) * 100) if total_qty else 0
        conc_flag = "🚨" if concentration > 50 else ("⚠️" if concentration > 30 else "✅")

        # Verdict: compare total accumulated vs distributed
        total_accum_qty  = sum(qty for _, qty in top_accum)
        total_distrib_qty = abs(sum(qty for _, qty in top_distrib))
        if total_accum_qty > total_distrib_qty * 1.5:
            verdict = "🟢 <b>Bullish lean</b> — institutions are quietly <b>accumulating</b> (holding onto shares)"
        elif total_distrib_qty > total_accum_qty * 1.5:
            verdict = "🔴 <b>Bearish lean</b> — institutions are <b>offloading</b> (getting rid of shares)"
        else:
            verdict = "🟡 <b>Neutral</b> — buying and selling are roughly balanced"

        lines = [
            f"🏦 <b>Broker Analysis — {symbol}</b>",
            f"<i>{int(total_qty):,} shares traded across {len(all_rows):,} transactions</i>",
            "<i>Each 'Broker' is a licensed stockbroker firm registered with NEPSE</i>",
            "",
            "📊 <b>Broker Positions</b>  <i>— net shares bought vs. sold today</i>",
            "<i>Net = Buy − Sell. Positive = building a position, negative = exiting one.</i>",
            DIV,
        ]
        for broker, qty in top_accum + top_distrib:
            sign = "+" if qty >= 0 else "-"
            buy_qty = buy.get(broker, {}).get("qty", 0)
            sell_qty = sell.get(broker, {}).get("qty", 0)
            lines.append(
                f"  Broker {broker:<4}  Buy {int(buy_qty):>7,}  "
                f"Sell {int(sell_qty):>7,}  Net {sign}{int(abs(qty)):>7,}"
            )

        if concentration > 50:
            conc_note = "🚨 Very HIGH — just 3 brokers handled over half the trading. Could be institutional or manipulative."
        elif concentration > 30:
            conc_note = "⚠️ MODERATE — a few big players dominate. Worth watching."
        else:
            conc_note = "✅ LOW — trading is spread across many brokers. Healthy and normal."

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
        ]

        return "\n".join(lines)

    except Exception as e:
        print(f"[ERROR] {symbol} broker analysis failed: {e}")
        return f"⚠️ <b>{symbol}</b>: analysis failed — {e}"


def main():
    scraper = get_scraper()

    if not CLI_SYMBOLS:
        send_market_close_summary(scraper)

    symbols = load_watch_stocks()
    if not symbols:
        print("No symbols to analyze.")
        sys.exit(0)

    print(f"Analyzing broker activity for: {', '.join(symbols)}")

    for symbol in symbols:
        print(f"  → {symbol}")
        send(analyze_floorsheet(scraper, symbol))

    print("Done.")


if __name__ == "__main__":
    main()

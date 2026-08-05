"""Market close summary."""
from datetime import datetime
from nepse_scraper import NepseScraper
from nepse.common import get_scraper, DIV, NST
from nepse.telegram import send
from nepse.kv import put_json as kv_put_json


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

    send("\n".join(lines))

    kv_put_json("alerted_circuits", {})


def main():
    scraper = get_scraper()
    send_market_close_summary(scraper)
    print("Done.")


if __name__ == "__main__":
    main()

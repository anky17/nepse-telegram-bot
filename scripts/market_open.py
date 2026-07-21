"""Sent at 11:00 AM NST when market opens."""
from datetime import datetime
from nepse.common import get_scraper, DIV, NST
from nepse.telegram import send


def main():
    scraper = get_scraper()
    date_str = datetime.now(NST).strftime("%a, %d %b %Y")

    try:
        indices = scraper.get_nepse_index()
        idx = next((i for i in indices if i.get("id") == 58), {})
        prev = float(idx.get("previousClose") or idx.get("close") or 0)
        current = float(idx.get("currentValue") or 0)
        change = float(idx.get("change") or 0)
        pct = float(idx.get("perChange") or 0)
        icon = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        index_line = f"{icon} <b>{current:,.2f}</b>  ({sign}{pct:.2f}%)  prev close {prev:,.2f}"
    except Exception:
        index_line = "⚠️ Index unavailable"

    try:
        sectors = scraper.call_endpoint("sectorwise_summary_api")
        top = max(sectors, key=lambda s: float(s.get("percentageChange") or 0))
        bot = min(sectors, key=lambda s: float(s.get("percentageChange") or 0))
        tp = float(top.get("percentageChange") or 0)
        bp = float(bot.get("percentageChange") or 0)
        sector_lines = [
            f"  Best   {top.get('sectorName','?'):<20} +{tp:.2f}%",
            f"  Worst  {bot.get('sectorName','?'):<20} {bp:.2f}%",
        ]
    except Exception:
        sector_lines = []

    lines = [
        f"🔔 <b>Market Open — {date_str}</b>",
        DIV,
        f"NEPSE Index  {index_line}",
        "Market Hours  11:00 AM – 3:00 PM NST",
    ]
    if sector_lines:
        lines += ["", "📂 <b>Sector Outlook</b>"] + sector_lines
    lines += ["", "<i>Updates every 30 min. Use /check for live data anytime.</i>"]

    send("\n".join(lines))
    print("Market open summary sent.")


if __name__ == "__main__":
    main()

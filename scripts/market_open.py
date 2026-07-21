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
        securities = scraper.get_all_securities()
        sym_sector = {s["symbol"]: s.get("sectorName", "Others") for s in securities}
        prices = scraper.get_today_price()
        bucket: dict = {}
        for p in prices:
            sym = p.get("symbol") or ""
            prev_p = p.get("previousDayClosePrice") or 0
            ltp = p.get("lastUpdatedPrice") or 0
            to = p.get("totalTradedValue") or 0
            if not prev_p or not ltp:
                continue
            pct_p = (ltp - prev_p) / prev_p * 100
            sector = sym_sector.get(sym, "Others")
            bucket.setdefault(sector, []).append((pct_p, to))
        sector_avg = {}
        for sector, items in bucket.items():
            total_to = sum(t for _, t in items)
            sector_avg[sector] = (
                sum(p * t for p, t in items) / total_to if total_to else
                sum(p for p, _ in items) / len(items)
            )
        best = max(sector_avg, key=sector_avg.get)
        worst = min(sector_avg, key=sector_avg.get)
        sector_lines = [
            f"  Best   {best:<20} +{sector_avg[best]:.2f}%",
            f"  Worst  {worst:<20} {sector_avg[worst]:.2f}%",
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

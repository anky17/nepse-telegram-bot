"""Sent at 11:00 AM NST when market opens."""
from datetime import datetime
from nepse.common import get_scraper, DIV, NST, compute_sector_avg
from nepse.telegram import send
from nepse.kv import put_index_snapshot, put_json as kv_put_json


def main():
    scraper = get_scraper()
    now_nst = datetime.now(NST)
    date_str = now_nst.strftime("%a, %d %b %Y")

    try:
        indices = scraper.get_nepse_index()
        idx = next((i for i in indices if i.get("id") == 58), {})
        prev = float(idx.get("previousClose") or idx.get("close") or 0)
        current = float(idx.get("currentValue") or 0)
        change = float(idx.get("change") or 0)
        pct = float(idx.get("perChange") or 0)
        high = float(idx.get("high") or 0)
        low = float(idx.get("low") or 0)
        icon = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        index_line = f"{icon} <b>{current:,.2f}</b>  ({sign}{pct:.2f}%)  prev close {prev:,.2f}"
        put_index_snapshot(
            current=current, change=change, pct=pct, high=high, low=low,
            date_str=now_nst.strftime("%Y-%m-%d"), updated_at=now_nst.isoformat(),
        )
    except Exception:
        index_line = "⚠️ Index unavailable"

    try:
        securities = scraper.get_all_securities()
        prices = scraper.get_today_price()
        sector_avg = compute_sector_avg(securities, prices)
        best = max(sector_avg, key=sector_avg.get)
        worst = min(sector_avg, key=sector_avg.get)
        sector_lines = [
            f"  Best   {best:<20} +{sector_avg[best]:.2f}%",
            f"  Worst  {worst:<20} {sector_avg[worst]:.2f}%",
        ]
        kv_put_json("site_sectors", {
            "date": now_nst.strftime("%Y-%m-%d"),
            "updatedAt": now_nst.isoformat(),
            "sectors": [{"name": k, "pct": v} for k, v in sorted(sector_avg.items(), key=lambda kv: -kv[1])],
        })
        kv_put_json("site_sector_map", {
            s["symbol"]: s.get("sectorName", "Others") for s in securities if s.get("symbol")
        })
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

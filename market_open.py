"""Sent at 11:00 AM NST when market opens."""
import os
import urllib3
import requests
from datetime import datetime
import pytz
from nepse_scraper import NepseScraper

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NST = pytz.timezone("Asia/Kathmandu")
DIV = "─────────────────"


def send_telegram(msg: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )


def main():
    scraper = NepseScraper(verify_ssl=False)
    now = datetime.now(NST)
    date_str = now.strftime("%a, %d %b %Y")

    # NEPSE index
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

    # Sector overview
    try:
        sectors = scraper.call_endpoint("sectorwise_summary_api")
        top_sector = max(sectors, key=lambda s: float(s.get("percentageChange") or 0))
        bot_sector = min(sectors, key=lambda s: float(s.get("percentageChange") or 0))
        tp = float(top_sector.get("percentageChange") or 0)
        bp = float(bot_sector.get("percentageChange") or 0)
        sector_lines = [
            f"  Best   {top_sector.get('sectorName','?'):<20} +{tp:.2f}%",
            f"  Worst  {bot_sector.get('sectorName','?'):<20} {bp:.2f}%",
        ]
    except Exception:
        sector_lines = []

    lines = [
        f"🔔 <b>Market Open — {date_str}</b>",
        DIV,
        f"NEPSE Index  {index_line}",
        f"Market Hours  11:00 AM – 3:00 PM NST",
    ]
    if sector_lines:
        lines += ["", "📂 <b>Sector Outlook</b>"] + sector_lines

    lines += [
        "",
        "<i>Updates every 30 min. Use /check for live data anytime.</i>",
    ]

    send_telegram("\n".join(lines))
    print("Market open summary sent.")


if __name__ == "__main__":
    main()

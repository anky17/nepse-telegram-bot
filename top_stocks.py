"""Top gainers/losers/turnover or sector-wise performance report."""
import os
import urllib3
import requests
from nepse_scraper import NepseScraper

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MODE = os.environ.get("MODE", "top")  # "top" or "sector"
DIV = "─────────────────"


def send_telegram(msg: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )


def fval(d: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


def top_stocks_report(scraper: NepseScraper) -> str:
    categories = [
        ("top_gainer",   "Top Gainers",  "🟢"),
        ("top_loser",    "Top Losers",   "🔴"),
        ("top_turnover", "Top Turnover", "💰"),
    ]
    lines = ["📊 <b>NEPSE Top Stocks</b>", DIV]
    for category, header, icon in categories:
        try:
            items = scraper.get_top_stocks(category=category)
            lines.append(f"\n{icon} <b>{header}</b>")
            for i, s in enumerate(items[:5], 1):
                sym = s.get("symbol") or "?"
                ltp = fval(s, "ltp", "lastTradedPrice", "closePrice")
                pct = fval(s, "percentageChange", "perChange")
                sign = "+" if pct >= 0 else ""
                lines.append(f"  {i}. <b>{sym:<10}</b> {ltp:>8.1f}   <code>{sign}{pct:.2f}%</code>")
        except Exception as e:
            print(f"[WARN] {category}: {e}")
            lines.append(f"\n{icon} <b>{header}</b>\n  ⚠️ Unavailable")
    return "\n".join(lines)


def sector_report(scraper: NepseScraper) -> str:
    try:
        sectors = scraper.call_endpoint("sectorwise_summary_api")
        sectors_sorted = sorted(
            sectors,
            key=lambda s: float(s.get("percentageChange") or 0),
            reverse=True,
        )
        lines = ["📂 <b>Sector Performance</b>", DIV]
        for s in sectors_sorted:
            name = s.get("sectorName", "?")
            pct = float(s.get("percentageChange") or 0)
            sign = "+" if pct >= 0 else ""
            icon = "🟢" if pct >= 0 else "🔴"
            lines.append(f"  {icon} {name:<28} <code>{sign}{pct:.2f}%</code>")
        return "\n".join(lines)
    except Exception as e:
        print(f"[WARN] sector_report: {e}")
        return f"📂 <b>Sector Performance</b>\n{DIV}\n⚠️ Unavailable"


def main():
    scraper = NepseScraper(verify_ssl=False)
    if MODE == "sector":
        send_telegram(sector_report(scraper))
    else:
        send_telegram(top_stocks_report(scraper))
    print(f"{MODE} report sent.")


if __name__ == "__main__":
    main()

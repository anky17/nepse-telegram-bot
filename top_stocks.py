"""Top gainers/losers/turnover or sector-wise performance."""
import os
import sys
from nepse.common import get_scraper, DIV, fval
from nepse.telegram import send

MODE = os.environ.get("MODE", "top")  # "top" or "sector"


def top_stocks_report(scraper) -> str:
    lines = ["📊 <b>NEPSE Top Stocks</b>", DIV]

    for category, header, icon in [("top_gainer", "Top Gainers", "🟢"), ("top_loser", "Top Losers", "🔴")]:
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

    try:
        items = scraper.get_top_stocks(category="top_turnover")
        lines.append("\n💰 <b>Top Turnover</b>")
        for i, s in enumerate(items[:5], 1):
            sym = s.get("symbol") or "?"
            ltp = fval(s, "closingPrice", "ltp", "lastTradedPrice")
            turnover = fval(s, "turnover")
            t_str = f"Rs {turnover/1_000_000:.1f}M" if turnover >= 1_000_000 else f"Rs {turnover:,.0f}"
            lines.append(f"  {i}. <b>{sym:<10}</b> {ltp:>8.1f}   <code>{t_str}</code>")
    except Exception as e:
        print(f"[WARN] top_turnover: {e}")
        lines.append("\n💰 <b>Top Turnover</b>\n  ⚠️ Unavailable")

    return "\n".join(lines)


def sector_report(scraper) -> str:
    try:
        sectors = scraper.call_endpoint("sectorwise_summary_api")
        sectors_sorted = sorted(sectors, key=lambda s: float(s.get("percentageChange") or 0), reverse=True)
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
    scraper = get_scraper()
    if MODE == "sector":
        send(sector_report(scraper))
    else:
        send(top_stocks_report(scraper))
    print(f"{MODE} report sent.")


if __name__ == "__main__":
    main()

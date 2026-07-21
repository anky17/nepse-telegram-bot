"""End-of-day market recap from ShareSansar static API."""
from nepse.sharesansar import get_recap
from nepse.common import DIV
from nepse.telegram import send


def build_recap_message(data: dict) -> str:
    date = data.get("date", "")
    headline = data.get("headline", "")
    advances = int(data.get("advances", 0))
    declines = int(data.get("declines", 0))
    unchanged = int(data.get("unchanged", 0))
    total = int(data.get("symbols", advances + declines + unchanged))
    turnover = float(data.get("totalTurnover", 0))
    transactions = int(data.get("totalTransactions", 0))
    most_active = data.get("mostActive") or {}

    bull_ratio = advances / (advances + declines) if (advances + declines) else 0
    if bull_ratio >= 0.65:
        mood, mood_icon = "Broadly Bullish", "🟢"
    elif bull_ratio >= 0.45:
        mood, mood_icon = "Mixed", "🟡"
    else:
        mood, mood_icon = "Broadly Bearish", "🔴"

    lines = [
        f"📈 <b>NEPSE End-of-Day Recap — {date}</b>",
        DIV,
    ]
    if headline:
        lines += [f"<i>{headline}</i>", ""]

    lines += [
        f"{mood_icon} <b>Market Breadth</b>",
        f"   🟢 {advances} up · 🔴 {declines} down · ⬜ {unchanged} flat  ({total} scrips)",
        f"   Mood: <b>{mood}</b>  ({bull_ratio * 100:.0f}% bulls)",
        "",
        "💼 <b>Market Activity</b>",
        f"   Turnover     Rs {turnover / 1_000_000:.2f}M",
        f"   Transactions {transactions:,}",
    ]

    if most_active.get("symbol"):
        ma_sym = most_active["symbol"]
        ma_to = float(most_active.get("turnover", 0))
        lines.append(f"   Most Active  <b>{ma_sym}</b>  Rs {ma_to / 1_000_000:.1f}M")

    for section_key, header, icon, sign_prefix in [
        ("topGainers", "Top Gainers", "🟢", "+"),
        ("topLosers", "Top Losers", "🔴", ""),
    ]:
        items = data.get(section_key, [])
        if not items:
            continue
        lines += ["", f"{icon} <b>{header}</b>", DIV]
        for i, s in enumerate(items[:5], 1):
            sym = s.get("symbol", "?")
            ltp = float(s.get("ltp", 0))
            pct = float(s.get("diffPct", 0))
            lines.append(f"  {i}.  <b>{sym:<10}</b> {ltp:>8.1f}   <code>{sign_prefix}{pct:.2f}%</code>")

    lines.append("\n<i>Source: ShareSansar · sharesansar.com</i>")
    return "\n".join(lines)


def main():
    data = get_recap()
    send(build_recap_message(data))
    print("EOD recap sent.")


if __name__ == "__main__":
    main()

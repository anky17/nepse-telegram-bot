"""Daily market insights — streaks, day-of-week patterns, year-ago comparison."""
from collections import defaultdict
from datetime import datetime, timedelta
from nepse.sharesansar import get_history
from nepse.common import DIV, NST
from nepse.telegram import send

POPULAR_STOCKS = ["NABIL", "NTC", "NLIC", "NICA", "GBIME", "SCB", "ADBL", "MEGA", "SANIMA"]
PROXY_STOCK = "NABIL"  # used for market-wide pattern analysis


# ── History parsing ───────────────────────────────────────────────

def parse_history(data: dict) -> tuple[list[str], list[float], list[float]]:
    """Return (dates, dp_values, ltp_values) oldest-first."""
    cols = data.get("cols", [])
    rows = data.get("rows", [])
    d_idx   = next((i for i, c in enumerate(cols) if c == "d"),   None)
    dp_idx  = next((i for i, c in enumerate(cols) if c == "dp"),  None)
    ltp_idx = next((i for i, c in enumerate(cols) if c == "ltp"), None)

    dates, dp_vals, ltp_vals = [], [], []
    for row in rows:
        try:
            if d_idx is not None:
                dates.append(str(row[d_idx]))
            if dp_idx is not None:
                dp_vals.append(float(row[dp_idx]))
            if ltp_idx is not None:
                ltp_vals.append(float(str(row[ltp_idx]).replace(",", "")))
        except (IndexError, TypeError, ValueError):
            pass
    return dates, dp_vals, ltp_vals


# ── Insight builders ──────────────────────────────────────────────

def compute_streak(dp_vals: list[float]) -> tuple[int, bool, float]:
    """Return (length, is_green, cumulative_pct)."""
    if not dp_vals:
        return 0, True, 0.0
    is_green = dp_vals[-1] > 0
    count, total = 0, 0.0
    for v in reversed(dp_vals):
        if v == 0:
            break
        if (v > 0) == is_green:
            count += 1
            total += v
        else:
            break
    return count, is_green, total


def build_streak_section() -> str:
    active = []
    for sym in POPULAR_STOCKS:
        try:
            _, dp_vals, _ = parse_history(get_history(sym))
            length, is_green, total = compute_streak(dp_vals)
            if length >= 3:
                active.append((sym, length, is_green, total))
        except Exception:
            continue

    if not active:
        return ""

    active.sort(key=lambda x: x[1], reverse=True)
    lines = ["\n🔥 <b>Active Streaks</b>  (3+ consecutive days)", DIV]
    for sym, length, is_green, total in active[:6]:
        icon = "🟢" if is_green else "🔴"
        word = "wins" if is_green else "losses"
        sign = "+" if total >= 0 else ""
        lines.append(
            f"  {icon} <b>{sym:<10}</b>  {length} day {word}  "
            f"<code>({sign}{total:.1f}% cumulative)</code>"
        )
    return "\n".join(lines)


def build_dow_section(dates: list[str], dp_vals: list[float]) -> str:
    """Which day of the week is historically most/least bullish."""
    if len(dates) < 50:
        return ""

    wins: dict[int, int] = defaultdict(int)
    total: dict[int, int] = defaultdict(int)
    DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for d, dp in zip(dates, dp_vals):
        try:
            day = datetime.strptime(d, "%Y-%m-%d").weekday()
            total[day] += 1
            if dp > 0:
                wins[day] += 1
        except ValueError:
            continue

    if not total:
        return ""

    rates = {d: wins[d] / total[d] * 100 for d in total}
    best  = max(rates, key=rates.get)
    worst = min(rates, key=rates.get)
    n = sum(total.values())

    lines = [
        f"\n📅 <b>Day-of-Week Patterns</b>  <i>(from {n} sessions)</i>",
        DIV,
        f"  🏆 Strongest : <b>{DOW[best]}</b>   stocks advance {rates[best]:.0f}% of the time",
        f"  ⚠️  Weakest   : <b>{DOW[worst]}</b>   stocks advance {rates[worst]:.0f}% of the time",
    ]

    today_day = datetime.now(NST).weekday()
    today_rate = rates.get(today_day)
    if today_rate is not None:
        mood = "historically strong 💪" if today_rate >= 60 else ("historically weak 😬" if today_rate <= 40 else "mixed historically 🤷")
        lines.append(f"  Today is <b>{DOW[today_day]}</b> — {mood} ({today_rate:.0f}% bull rate)")

    return "\n".join(lines)


def build_year_ago_section(dates: list[str], ltp_vals: list[float]) -> str:
    """NABIL price vs one year ago as a market-mood proxy."""
    if len(dates) < 50 or len(ltp_vals) < 50:
        return ""

    today = datetime.now(NST).date()
    target_str = (today - timedelta(days=365)).strftime("%Y-%m-%d")

    best_idx = None
    for i, d in enumerate(dates):
        if d <= target_str:
            best_idx = i

    if best_idx is None or best_idx >= len(ltp_vals):
        return ""

    price_then = ltp_vals[best_idx]
    price_now  = ltp_vals[-1]
    if price_then == 0:
        return ""

    pct = (price_now - price_then) / price_then * 100
    sign = "+" if pct >= 0 else ""
    icon = "🟢" if pct >= 0 else "🔴"
    mood = (
        "the market has been on a strong bull run 🚀" if pct >= 20 else
        "steady gains over the year 📈" if pct >= 5 else
        "relatively flat over 12 months ➡️" if pct >= -5 else
        "a tough year for investors 📉"
    )

    return (
        f"\n📆 <b>One Year Ago Today</b>  <i>(NABIL as proxy)</i>\n{DIV}\n"
        f"  {dates[best_idx]}: Rs {price_then:,.1f}  →  Today: Rs {price_now:,.1f}\n"
        f"  {icon} <b>{sign}{pct:.1f}%</b> over 12 months — {mood}"
    )


# ── Main ──────────────────────────────────────────────────────────

def main():
    now = datetime.now(NST)
    date_str = now.strftime("%a, %d %b %Y")

    lines = [f"🧠 <b>NEPSE Market Insights — {date_str}</b>", DIV]

    streak_text = build_streak_section()
    if streak_text:
        lines.append(streak_text)

    try:
        dates, dp_vals, ltp_vals = parse_history(get_history(PROXY_STOCK))
        # Alternate between the two fact types by day of week
        if now.weekday() % 2 == 0:
            section = build_dow_section(dates, dp_vals)
        else:
            section = build_year_ago_section(dates, ltp_vals)
        if section:
            lines.append(section)
    except Exception as e:
        print(f"[WARN] Proxy history error: {e}")

    lines.append("\n<i>Data: ShareSansar archive · 400+ trading sessions</i>")

    if len(lines) > 2:
        send("\n".join(lines))
        print("Insights sent.")
    else:
        print("No insights to send today.")


if __name__ == "__main__":
    main()

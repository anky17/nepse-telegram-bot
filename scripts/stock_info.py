"""Individual stock info with indicators and sentiment analysis."""
import sys
from datetime import date, datetime, timedelta
from nepse_scraper import NepseScraper
from nepse.common import get_scraper, DIV
from nepse.sharesansar import get_history_closes_volumes as ss_history, get_history_dated_closes
from nepse.telegram import send

args = [a.upper() for a in sys.argv[1:] if a != "--print"]


# ── Indicator math ────────────────────────────────────────────────

def compute_rsi(closes: list, period: int = 14) -> float | None:
    """Wilder's smoothed RSI — uses EMA after the seed average, not a simple average."""
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    # Seed: simple average of the first `period` moves
    avg_gain = sum(max(c, 0) for c in changes[:period]) / period
    avg_loss = sum(abs(min(c, 0)) for c in changes[:period]) / period
    # Wilder's EMA smoothing for every subsequent bar
    for c in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(c, 0)) / period
        avg_loss = (avg_loss * (period - 1) + abs(min(c, 0))) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def compute_sma(closes: list, period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def compute_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict | None:
    """Classic floor-trader pivot points from the prior session's H/L/C."""
    if not (prev_high and prev_low and prev_close):
        return None
    pp = (prev_high + prev_low + prev_close) / 3
    rng = prev_high - prev_low
    return {
        "pp": pp,
        "r1": 2 * pp - prev_low, "r2": pp + rng, "r3": prev_high + 2 * (pp - prev_low),
        "s1": 2 * pp - prev_high, "s2": pp - rng, "s3": prev_low - 2 * (prev_high - pp),
    }


def _ema_series(values: list, period: int) -> list:
    """EMA aligned 1:1 with `values`; None entries until enough data to seed."""
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    out = [None] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def compute_macd(closes: list) -> dict | None:
    """MACD(12,26,9) — line, signal, histogram, and a bullish/bearish cross flag."""
    if len(closes) < 26 + 9:
        return None
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_line = [a - b if a is not None and b is not None else None for a, b in zip(ema12, ema26)]
    valid = [v for v in macd_line if v is not None]
    if len(valid) < 9:
        return None
    signal_line = _ema_series(valid, 9)
    macd_val, signal_val = valid[-1], signal_line[-1]
    prev_macd, prev_signal = valid[-2], signal_line[-2]
    bull_cross = prev_macd <= prev_signal and macd_val > signal_val
    bear_cross = prev_macd >= prev_signal and macd_val < signal_val
    return {
        "macd": macd_val, "signal": signal_val, "hist": macd_val - signal_val,
        "bull_cross": bull_cross, "bear_cross": bear_cross,
    }


def compute_1y_return(dated_closes: list[tuple[str, float]], ltp: float) -> float | None:
    """% change from the close nearest 365 days ago to today's LTP."""
    if not dated_closes or not ltp:
        return None
    target = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    candidates = [c for d, c in dated_closes if d <= target]
    if not candidates:
        return None
    year_ago = candidates[-1]
    return (ltp - year_ago) / year_ago * 100 if year_ago else None


# ── Sentiment engine ──────────────────────────────────────────────

def build_sentiment(ltp, sma20, sma50, rsi, vol_ratio, day_pct, w52_pct, sma200=None):
    score = 0
    signals = []

    if sma20 is not None:
        if ltp > sma20:
            score += 1
            signals.append(("🟢", f"Price is above its 20-day average (Rs {sma20:,.0f}) — short-term uptrend"))
        else:
            score -= 1
            signals.append(("🔴", f"Price is below its 20-day average (Rs {sma20:,.0f}) — short-term downtrend"))

    if sma50 is not None:
        if ltp > sma50:
            score += 1
            signals.append(("🟢", f"Price is above its 50-day average (Rs {sma50:,.0f}) — medium-term uptrend"))
        else:
            score -= 1
            signals.append(("🔴", f"Price is below its 50-day average (Rs {sma50:,.0f}) — medium-term downtrend"))

    if sma200 is not None:
        if ltp > sma200:
            score += 1
            signals.append(("🟢", f"Price is above its 200-day average (Rs {sma200:,.0f}) — long-term uptrend"))
        else:
            score -= 1
            signals.append(("🔴", f"Price is below its 200-day average (Rs {sma200:,.0f}) — long-term downtrend"))
        if sma50 is not None and sma200 is not None:
            if sma50 > sma200:
                score += 1
                signals.append(("🟢", "50-day above 200-day — GOLDEN CROSS (major long-term bullish signal)"))
            else:
                score -= 1
                signals.append(("🔴", "50-day below 200-day — DEATH CROSS (major long-term bearish signal)"))

    if rsi is not None:
        if rsi >= 70:
            score -= 1
            signals.append(("🟡", f"RSI {rsi:.0f}/100 — Overbought. The stock has risen fast and may slow down or dip soon"))
        elif rsi <= 30:
            score += 1
            signals.append(("🟡", f"RSI {rsi:.0f}/100 — Oversold. The stock has fallen a lot and may bounce back"))
        elif rsi >= 55:
            score += 1
            signals.append(("🟢", f"RSI {rsi:.0f}/100 — Strong momentum. Buyers are in control"))
        elif rsi <= 45:
            score -= 1
            signals.append(("🔴", f"RSI {rsi:.0f}/100 — Weak momentum. Sellers are in control"))
        else:
            signals.append(("🟡", f"RSI {rsi:.0f}/100 — Neutral zone, no clear direction"))

    if vol_ratio is not None:
        if vol_ratio >= 2.0:
            if day_pct >= 0:
                score += 1
                signals.append(("🟢", f"Volume is {vol_ratio:.1f}x above normal — very strong buying interest today"))
            else:
                score -= 1
                signals.append(("🔴", f"Volume is {vol_ratio:.1f}x above normal — heavy selling pressure today"))
        elif vol_ratio >= 1.3:
            if day_pct >= 0:
                signals.append(("🟢", f"Volume is {vol_ratio:.1f}x above normal — above-average buying"))
            else:
                signals.append(("🔴", f"Volume is {vol_ratio:.1f}x above normal — above-average selling"))
        elif vol_ratio < 0.5:
            signals.append(("🟡", f"Volume is low ({vol_ratio:.1f}x normal) — not many people trading today"))

    if w52_pct is not None:
        if w52_pct >= 80:
            signals.append(("🟡", f"Near 52-week HIGH (top {100-w52_pct:.0f}% of yearly range) — strong run, be careful of pullback"))
        elif w52_pct <= 20:
            signals.append(("🟡", f"Near 52-week LOW (bottom {w52_pct:.0f}% of yearly range) — could be a buying opportunity or value trap"))

    if score >= 2:
        verdict = "✅ Watch"
        plain = "More signals positive than negative. Worth monitoring closely."
    elif score >= 0:
        verdict = "⚠️ Caution"
        plain = "Mixed signals — proceed carefully and wait for confirmation."
    else:
        verdict = "❌ Avoid"
        plain = "More signals negative than positive. High risk right now."

    return verdict, plain, signals, score


# ── Main report ───────────────────────────────────────────────────

def fetch_stock(scraper: NepseScraper, symbol: str) -> str:
    try:
        data = scraper.get_ticker_info(symbol)
    except Exception as e:
        return f"⚠️ <b>{symbol}</b>: could not fetch — {e}"

    t = data.get("securityDailyTradeDto") or {}
    sec = data.get("security") or {}
    company = sec.get("companyId") or {}
    sector = company.get("sectorMaster") or {}
    group = sec.get("shareGroupId") or {}

    ltp = float(t.get("lastTradedPrice") or t.get("closePrice") or 0)
    prev = float(t.get("previousClose") or 0)
    change = ltp - prev
    day_pct = (change / prev * 100) if prev else 0
    open_ = float(t.get("openPrice") or 0)
    high = float(t.get("highPrice") or 0)
    low = float(t.get("lowPrice") or 0)
    volume = int(t.get("totalTradeQuantity") or 0)
    trades = int(t.get("totalTrades") or 0)
    w52h = float(t.get("fiftyTwoWeekHigh") or 0)
    w52l = float(t.get("fiftyTwoWeekLow") or 0)
    mktcap = float(data.get("marketCapitalization") or 0)
    networth = float(sec.get("networthBasePrice") or 0)
    biz_date = t.get("businessDate", "")

    icon = "🟢" if change >= 0 else "🔴"
    sign = "+" if change >= 0 else ""

    w52_pct = None
    if w52h > w52l:
        w52_pct = (ltp - w52l) / (w52h - w52l) * 100

    # Fetch volumes from the scraper (90-day window is enough for a 20-day avg).
    # Fetch full price history from ShareSansar for all SMA/RSI calculations —
    # the scraper's 90-day window is too short for reliable SMA50 on busy calendars.
    volumes: list[float] = []
    pivots = None
    try:
        end = date.today()
        start = end - timedelta(days=90)
        hist = scraper.get_ticker_price_history(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        rows = sorted(hist.get("content", []), key=lambda r: r.get("businessDate", ""))
        volumes = [float(r["totalTradedQuantity"]) for r in rows if r.get("totalTradedQuantity")]
        # Most recent completed session (excludes today, which hasn't settled into
        # this history endpoint yet) — its H/L/C seeds today's pivot levels.
        prior_rows = [r for r in rows if r.get("businessDate") != biz_date]
        if prior_rows:
            prior = prior_rows[-1]
            pivots = compute_pivots(
                float(prior.get("highPrice") or 0),
                float(prior.get("lowPrice") or 0),
                float(prior.get("closePrice") or 0),
            )
    except Exception:
        pass

    ss_closes: list[float] = []
    try:
        ss_closes, _ = ss_history(symbol)
    except Exception:
        pass

    price_data = ss_closes  # full history for all price-based indicators
    sma20 = compute_sma(price_data, 20)
    sma50 = compute_sma(price_data, 50)
    sma200 = compute_sma(price_data, 200) if len(price_data) >= 200 else None
    rsi = compute_rsi(price_data, 14) if len(price_data) > 14 else None
    macd = compute_macd(price_data)
    avg_vol = (sum(volumes[-20:]) / len(volumes[-20:])) if len(volumes) >= 5 else None
    vol_ratio = (volume / avg_vol) if avg_vol and avg_vol > 0 else None

    dated_closes: list[tuple[str, float]] = []
    try:
        dated_closes = get_history_dated_closes(symbol)
    except Exception:
        pass
    ret_1y = compute_1y_return(dated_closes, ltp)

    verdict, plain, signals, score = build_sentiment(
        ltp, sma20, sma50, rsi, vol_ratio, day_pct, w52_pct, sma200=sma200
    )

    # Quick-kill: Group Z/D = compliance risk, override verdict regardless of score
    group_name = group.get("name", "")
    if group_name in ("Z", "D"):
        verdict = "❌ Avoid"
        plain = f"Group {group_name} — compliance risk. Trading may be restricted or suspended."

    lines = [
        f"📋 <b>{symbol}</b>  —  {company.get('companyName', sec.get('securityName', ''))}",
        f"<i>{sector.get('sectorDescription', '')}  |  Group {group_name}  |  {biz_date}</i>",
        DIV,
    ]
    if group_name in ("Z", "D"):
        lines += [
            f"⛔ <b>Group {group_name} — Compliance Risk</b>",
            "   Trading may be restricted or suspended. Exercise extreme caution.",
            "",
        ]
    lines += [
        f"{icon}  <b>Price: Rs {ltp:,.2f}</b>   {sign}{change:.2f} ({sign}{day_pct:.2f}% today)",
        f"   Open {open_:,.2f}   High {high:,.2f}   Low {low:,.2f}",
        f"   Yesterday closed at Rs {prev:,.2f}",
        "",
        "📊 <b>Today's Activity</b>",
        f"   {volume:,} shares traded across {trades:,} transactions",
    ]

    if vol_ratio is not None:
        vol_label = "above" if vol_ratio >= 1 else "below"
        lines.append(f"   Volume is <b>{vol_ratio:.1f}x {vol_label} normal</b>")

    lines += [
        "",
        "📅 <b>52-Week Range</b>  (past year high & low)",
        f"   Lowest: Rs {w52l:,.2f}   →   Highest: Rs {w52h:,.2f}",
    ]
    if w52_pct is not None:
        bar_filled = int(w52_pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines.append(f"   [{bar}] {w52_pct:.0f}% of yearly range")
        if w52_pct >= 75:
            lines.append("   <i>Near yearly high — strong run</i>")
        elif w52_pct <= 25:
            lines.append("   <i>Near yearly low — possible opportunity or continued fall</i>")
    if ret_1y is not None:
        ret_sign = "+" if ret_1y >= 0 else ""
        ret_icon = "🟢" if ret_1y >= 0 else "🔴"
        lines.append(f"   {ret_icon} 1-year return: <b>{ret_sign}{ret_1y:.1f}%</b>")

    if mktcap:
        pb = ltp / networth if networth else 0
        lines += [
            "",
            "🏢 <b>Company Size</b>",
            f"   Total market value: Rs {mktcap / 1_000_000:.1f}M",
        ]
        if networth:
            lines.append(f"   Book value per share: Rs {networth:.2f}   |   P/B ratio: {pb:.2f}x")
            if pb > 3:
                lines.append("   <i>P/B above 3 — market pays a premium for this stock</i>")
            elif pb < 1:
                lines.append("   <i>P/B below 1 — trading below book value (may be undervalued)</i>")

    lines += ["", "🧭 <b>Technical Indicators</b>  <i>(what the charts say)</i>", DIV]
    if sma20 is not None:
        rel = "ABOVE ▲" if ltp > sma20 else "BELOW ▼"
        lines.append(f"   20-day avg price: Rs {sma20:,.0f}  →  Currently <b>{rel}</b>")
    if sma50 is not None:
        rel = "ABOVE ▲" if ltp > sma50 else "BELOW ▼"
        lines.append(f"   50-day avg price: Rs {sma50:,.0f}  →  Currently <b>{rel}</b>")
    if sma200 is not None:
        rel = "ABOVE ▲" if ltp > sma200 else "BELOW ▼"
        lines.append(f"   200-day avg price: Rs {sma200:,.0f}  →  Currently <b>{rel}</b>")
    if sma20 is not None and sma50 is not None:
        if sma20 > sma50:
            lines.append("   <i>Short-term: 20-day above 50-day — near-term bullish</i>")
        else:
            lines.append("   <i>Short-term: 20-day below 50-day — near-term bearish</i>")
    if sma50 is not None and sma200 is not None:
        if sma50 > sma200:
            lines.append("   <i>Long-term: 50-day above 200-day — ✅ GOLDEN CROSS (major bull signal)</i>")
        else:
            lines.append("   <i>Long-term: 50-day below 200-day — ⚠️ DEATH CROSS (major bear signal)</i>")
    if rsi is not None:
        if rsi >= 70:
            rsi_label = "Overbought — may pull back"
        elif rsi <= 30:
            rsi_label = "Oversold — may bounce"
        elif rsi >= 55:
            rsi_label = "Bullish momentum"
        elif rsi <= 45:
            rsi_label = "Weak momentum"
        else:
            rsi_label = "Neutral"
        lines.append(f"   RSI (14-day): <b>{rsi:.0f}/100</b>  →  {rsi_label}")
        lines.append("   <i>RSI: 0-30 = very cheap/oversold, 30-70 = normal, 70-100 = expensive/overbought</i>")
    if macd is not None:
        if macd["bull_cross"]:
            macd_label = "Just crossed BULLISH 🟢 — momentum turning up"
        elif macd["bear_cross"]:
            macd_label = "Just crossed BEARISH 🔴 — momentum turning down"
        elif macd["macd"] > macd["signal"]:
            macd_label = "Bullish — trend line above signal line"
        else:
            macd_label = "Bearish — trend line below signal line"
        lines.append(f"   MACD: <b>{macd['macd']:.2f}</b> vs signal {macd['signal']:.2f}  →  {macd_label}")

    if pivots is not None:
        lines += [
            "",
            "🎯 <b>Pivot Points</b>  <i>(today's support/resistance from yesterday's range)</i>",
            f"   Resistance:  R3 {pivots['r3']:,.1f}   R2 {pivots['r2']:,.1f}   R1 {pivots['r1']:,.1f}",
            f"   Pivot:       {pivots['pp']:,.1f}",
            f"   Support:     S1 {pivots['s1']:,.1f}   S2 {pivots['s2']:,.1f}   S3 {pivots['s3']:,.1f}",
        ]

    # Build a 2-line key-reasons summary from the strongest aligned signals
    aligned = [txt for ic, txt in signals if (ic == "🟢") == (score >= 0)][:2]
    reasons = " · ".join(t.split(" — ")[1] if " — " in t else t for t in aligned) or plain

    lines += [
        "",
        f"🎯 <b>Verdict: {verdict}</b>",
        DIV,
        f"   {reasons}",
        "",
        "   <b>Signal breakdown:</b>",
    ]
    for sig_icon, sig_text in signals:
        lines.append(f"   {sig_icon} {sig_text}")

    return "\n".join(lines)


def main():
    symbols = args
    if not symbols:
        print("Usage: python stock_info.py NABIL [NICA ...] [--print]")
        sys.exit(1)

    scraper = get_scraper()
    for symbol in symbols:
        send(fetch_stock(scraper, symbol))


if __name__ == "__main__":
    main()

"""Individual stock info with indicators and sentiment analysis."""
import sys
from datetime import date, timedelta
from nepse_scraper import NepseScraper
from nepse.common import get_scraper, DIV
from nepse.telegram import send

args = [a.upper() for a in sys.argv[1:] if a != "--print"]


# ── Indicator math ────────────────────────────────────────────────

def compute_rsi(closes: list, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes[-period:]]
    losses = [abs(min(c, 0)) for c in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def compute_sma(closes: list, period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


# ── Sentiment engine ──────────────────────────────────────────────

def build_sentiment(ltp, sma20, sma50, rsi, vol_ratio, day_pct, w52_pct):
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

    if score >= 3:
        verdict, icon = "STRONGLY BULLISH 🚀", "🟢"
        plain = "Most signals point UP. This stock looks strong right now."
    elif score >= 1:
        verdict, icon = "BULLISH 📈", "🟢"
        plain = "More signals point UP than DOWN. Leaning positive."
    elif score == 0:
        verdict, icon = "NEUTRAL ➡️", "🟡"
        plain = "Mixed signals — no clear direction. Watch and wait."
    elif score >= -2:
        verdict, icon = "BEARISH 📉", "🔴"
        plain = "More signals point DOWN than UP. Caution advised."
    else:
        verdict, icon = "STRONGLY BEARISH ⚠️", "🔴"
        plain = "Most signals point DOWN. This stock looks weak right now."

    return verdict, icon, plain, signals, score


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

    closes, volumes = [], []
    try:
        end = date.today()
        start = end - timedelta(days=90)
        hist = scraper.get_ticker_price_history(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        rows = sorted(hist.get("content", []), key=lambda r: r.get("businessDate", ""))
        closes = [float(r["closePrice"]) for r in rows if r.get("closePrice")]
        volumes = [float(r["totalTradedQuantity"]) for r in rows if r.get("totalTradedQuantity")]
    except Exception:
        pass

    sma20 = compute_sma(closes, 20)
    sma50 = compute_sma(closes, 50)
    rsi = compute_rsi(closes, 14)
    avg_vol = (sum(volumes[-20:]) / len(volumes[-20:])) if len(volumes) >= 5 else None
    vol_ratio = (volume / avg_vol) if avg_vol and avg_vol > 0 else None

    verdict, sent_icon, plain, signals, score = build_sentiment(
        ltp, sma20, sma50, rsi, vol_ratio, day_pct, w52_pct
    )

    lines = [
        f"📋 <b>{symbol}</b>  —  {company.get('companyName', sec.get('securityName', ''))}",
        f"<i>{sector.get('sectorDescription', '')}  |  Group {group.get('name', '')}  |  {biz_date}</i>",
        DIV,
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
    if sma20:
        rel = "ABOVE ▲" if ltp > sma20 else "BELOW ▼"
        lines.append(f"   20-day avg price: Rs {sma20:,.0f}  →  Currently <b>{rel}</b>")
    if sma50:
        rel = "ABOVE ▲" if ltp > sma50 else "BELOW ▼"
        lines.append(f"   50-day avg price: Rs {sma50:,.0f}  →  Currently <b>{rel}</b>")
    if sma20 and sma50:
        if sma20 > sma50:
            lines.append("   <i>20-day crossed above 50-day — bullish golden signal</i>")
        else:
            lines.append("   <i>20-day below 50-day — bearish death cross signal</i>")
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

    lines += [
        "",
        f"🎯 <b>Overall Sentiment: {verdict}</b>",
        DIV,
        f"   {plain}",
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

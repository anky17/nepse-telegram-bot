import os
import sys
import urllib3
import requests
from datetime import datetime
import pytz
from nepse_scraper import NepseScraper
from kv_utils import kv_get_json, kv_put_json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NST = pytz.timezone("Asia/Kathmandu")
DIV = "─────────────────"


# ── KV loaders ────────────────────────────────────────────────────

def load_watch_stocks() -> list[str]:
    raw = kv_get_json("watch_stocks", default=None)
    if raw is None:
        # watch_stocks is stored as plain string, not JSON
        from kv_utils import kv_get
        text = kv_get("watch_stocks", "")
        return [s.strip().upper() for s in text.split(",") if s.strip()]
    if isinstance(raw, list):
        return [s.upper() for s in raw]
    return [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]

def load_alerts() -> dict:
    return kv_get_json("alerts", {})

def load_portfolio() -> dict:
    return kv_get_json("portfolio", {})


# ── Telegram ──────────────────────────────────────────────────────

def send_telegram(message: str):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


# ── Field helpers ─────────────────────────────────────────────────

def fval(d: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default

def ival(d: dict, *keys, default=0) -> int:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return int(float(v))
            except (ValueError, TypeError):
                pass
    return default

def tag(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{'▲' if value >= 0 else '▼'} {sign}{value:.2f}"


# ── Alert & circuit breaker checks ───────────────────────────────

def check_alerts_and_circuits(today_prices: list[dict]) -> None:
    """Fire price alerts and circuit breaker notifications immediately."""
    price_map = {s.get("symbol"): s for s in today_prices if s.get("symbol")}

    # ── Price alerts ──────────────────────────────────────────────
    alerts = load_alerts()
    triggered = []

    for symbol, cfg in alerts.items():
        stock = price_map.get(symbol)
        if not stock:
            continue
        ltp = fval(stock, "lastUpdatedPrice", "ltp", "closePrice")
        target = float(cfg.get("price", 0))
        direction = cfg.get("direction", "above")

        hit = (direction == "above" and ltp >= target) or (direction == "below" and ltp <= target)
        if hit:
            arrow = "▲" if direction == "above" else "▼"
            send_telegram(
                f"🔔 <b>Price Alert — {symbol}</b>\n"
                f"{DIV}\n"
                f"Target {arrow} Rs {target:,.2f} reached!\n"
                f"Current LTP: <b>Rs {ltp:,.2f}</b>"
            )
            triggered.append(symbol)

    if triggered:
        remaining = {k: v for k, v in alerts.items() if k not in triggered}
        kv_put_json("alerts", remaining)
        print(f"Alerts fired and removed: {triggered}")

    # ── Circuit breakers ──────────────────────────────────────────
    alerted_today = kv_get_json("alerted_circuits", {})
    new_circuits = []

    for stock in today_prices:
        sym = stock.get("symbol")
        if not sym or sym in alerted_today:
            continue
        pct = fval(stock, "percentageChange", "perChange")
        ltp = fval(stock, "lastUpdatedPrice", "ltp", "closePrice")

        if abs(pct) >= 9.5:
            direction = "UPPER CIRCUIT" if pct > 0 else "LOWER CIRCUIT"
            icon = "🚨" if pct > 0 else "💥"
            sign = "+" if pct > 0 else ""
            send_telegram(
                f"{icon} <b>{direction} — {sym}</b>\n"
                f"{DIV}\n"
                f"LTP  : Rs {ltp:,.2f}\n"
                f"Change: <b>{sign}{pct:.2f}%</b>"
            )
            alerted_today[sym] = True
            new_circuits.append(sym)

    if new_circuits:
        kv_put_json("alerted_circuits", alerted_today)
        print(f"Circuit breakers fired: {new_circuits}")


# ── Volume spikes ─────────────────────────────────────────────────

def build_volume_spikes_section(scraper: NepseScraper, today_prices: list[dict]) -> str:
    try:
        avg_data = scraper.get_trading_average(n_days=30)
        # Build map: symbol → average volume
        avg_map: dict[str, float] = {}
        for item in avg_data:
            sym = item.get("symbol") or item.get("stockSymbol")
            avg_vol = fval(item, "averageDailyVolume", "averageVolume", "avgVolume", "tradedShares")
            if sym and avg_vol > 0:
                avg_map[sym] = avg_vol

        spikes = []
        for stock in today_prices:
            sym = stock.get("symbol")
            today_vol = fval(stock, "totalTradedQuantity", "tradedShares", "volume")
            avg_vol = avg_map.get(sym, 0)
            if avg_vol > 0 and today_vol >= avg_vol * 2.0:
                spikes.append((sym, today_vol, avg_vol, today_vol / avg_vol))

        if not spikes:
            return ""

        spikes.sort(key=lambda x: x[3], reverse=True)
        lines = [f"\n⚡ <b>Volume Spikes</b> (≥2× 30-day avg)", DIV]
        for sym, vol, avg, ratio in spikes[:5]:
            lines.append(f"  <b>{sym:<10}</b> {int(vol):>9,}   <code>{ratio:.1f}x</code> avg")
        return "\n".join(lines)
    except Exception as e:
        print(f"[WARN] Volume spikes error: {e}")
        return ""


# ── Portfolio P&L ─────────────────────────────────────────────────

def build_portfolio_section(today_prices: list[dict]) -> str:
    portfolio = load_portfolio()
    if not portfolio:
        return ""

    price_map = {s.get("symbol"): s for s in today_prices if s.get("symbol")}
    lines = [f"\n💰 <b>Your Portfolio</b>", DIV]

    total_current = 0.0
    total_invested = 0.0

    for symbol, cfg in portfolio.items():
        qty = int(cfg.get("qty", 0))
        buy_price = float(cfg.get("buy", 0))
        stock = price_map.get(symbol)
        if not stock:
            lines.append(f"  ⚠️ {symbol}: price unavailable")
            continue

        ltp = fval(stock, "lastUpdatedPrice", "ltp", "closePrice")
        current_val = qty * ltp
        invested_val = qty * buy_price
        pl = current_val - invested_val
        pl_pct = ((ltp - buy_price) / buy_price * 100) if buy_price else 0

        total_current += current_val
        total_invested += invested_val

        icon = "🟢" if pl >= 0 else "🔴"
        sign = "+" if pl >= 0 else ""
        lines.append(
            f"  {icon} <b>{symbol}</b>  {qty} shares @ {ltp:.1f}\n"
            f"       P&L: <code>{sign}Rs {pl:,.0f}  ({sign}{pl_pct:.2f}%)</code>"
        )

    total_pl = total_current - total_invested
    total_pl_pct = ((total_current - total_invested) / total_invested * 100) if total_invested else 0
    sign = "+" if total_pl >= 0 else ""
    icon = "🟢" if total_pl >= 0 else "🔴"
    lines += [
        DIV,
        f"  {icon} <b>Total</b>  Invested Rs {total_invested:,.0f}",
        f"         Value    Rs {total_current:,.0f}",
        f"         P&L      <code>{sign}Rs {total_pl:,.0f}  ({sign}{total_pl_pct:.2f}%)</code>",
    ]
    return "\n".join(lines)


# ── Standard market sections ──────────────────────────────────────

def build_index_section(scraper: NepseScraper) -> str:
    try:
        indices = scraper.get_nepse_index()
        main = next((i for i in indices if i.get("id") == 58), None)
        if not main:
            return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ No data"
        current = fval(main, "currentValue")
        change = fval(main, "change")
        pct = fval(main, "perChange")
        high = fval(main, "high")
        low = fval(main, "low")
        icon = "🟢" if change >= 0 else "🔴"
        return "\n".join([
            "📊 <b>NEPSE Index</b>", DIV,
            f"{icon}  <b>{current:,.2f}</b>   {tag(change)} pts  ({tag(pct)}%)",
            f"  H: {high:,.2f}   L: {low:,.2f}",
        ])
    except Exception as e:
        print(f"[WARN] Index error: {e}")
        return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ Unavailable"


def build_summary_section(scraper: NepseScraper) -> str:
    try:
        rows = scraper.call_endpoint("market_summary_api")
        lookup = {r["detail"]: r["value"] for r in rows if "detail" in r}
        turnover = float(lookup.get("Total Turnover Rs:", 0))
        shares = int(lookup.get("Total Traded Shares", 0))
        txns = int(lookup.get("Total Transactions", 0))
        scrips = int(lookup.get("Total Scrips Traded", 0))
        return "\n".join([
            "\n💼 <b>Market Summary</b>", DIV,
            f"  Turnover      Rs {turnover / 1_000_000:.2f}M",
            f"  Shares Traded {shares:,}",
            f"  Transactions  {txns:,}",
            f"  Scrips Traded {scrips:,}",
        ])
    except Exception as e:
        print(f"[WARN] Summary error: {e}")
        return ""


def build_gainers_losers_section(scraper: NepseScraper) -> str:
    lines = []

    def section(category: str, header: str, icon: str):
        try:
            items = scraper.get_top_stocks(category=category)
            rows = [f"\n{icon} <b>{header}</b>", DIV]
            for i, s in enumerate(items[:5], 1):
                sym = s.get("symbol") or "?"
                ltp = fval(s, "ltp", "lastTradedPrice")
                pct = fval(s, "percentageChange", "perChange")
                sign = "+" if pct >= 0 else ""
                rows.append(f"  {i}.  <b>{sym:<10}</b> {ltp:>8.1f}   <code>{sign}{pct:.2f}%</code>")
            return rows
        except Exception as e:
            print(f"[WARN] {category} error: {e}")
            return [f"\n{icon} <b>{header}</b>", DIV, "⚠️ Unavailable"]

    lines += section("top_gainer", "Top Gainers", "🟢")
    lines += section("top_loser", "Top Losers", "🔴")
    return "\n".join(lines)


def build_watchlist_section(scraper: NepseScraper, watch_stocks: list[str]) -> str:
    if not watch_stocks:
        return ""
    lines = ["\n👁 <b>Your Watchlist</b>", DIV]
    for symbol in watch_stocks:
        try:
            info = scraper.get_ticker_info(symbol)
            sec = info.get("security", info) if isinstance(info, dict) else {}
            ltp = fval(info, "lastTradedPrice", "ltp") or fval(sec, "lastTradedPrice", "ltp")
            pct = fval(info, "percentageChange", "perChange") or fval(sec, "percentageChange", "perChange")
            change = fval(info, "change", "pointChange") or fval(sec, "change", "pointChange")
            vol = ival(info, "tradedShares", "totalTradedShares") or ival(sec, "tradedShares")
            icon = "🟢" if change >= 0 else "🔴"
            sign = "+" if change >= 0 else ""
            lines.append(f"  {icon} <b>{symbol:<10}</b> {ltp:>8.2f}   <code>{sign}{pct:.2f}%</code>   Vol {vol:,}")
        except Exception as e:
            print(f"[WARN] Watchlist {symbol} error: {e}")
            lines.append(f"  ⚠️ {symbol}: unavailable")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────

def main():
    now_nst = datetime.now(NST)
    time_str = now_nst.strftime("%a, %d %b %Y  %I:%M %p NST")
    scraper = NepseScraper(verify_ssl=False)

    if not scraper.is_market_open():
        print("Market is closed. Skipping.")
        sys.exit(0)

    print(f"[{time_str}] Fetching NEPSE data...")

    watch_stocks = load_watch_stocks()

    # Fetch today's full price list once — shared across all checks
    today_prices: list[dict] = []
    try:
        today_prices = scraper.get_today_price() or []
    except Exception as e:
        print(f"[WARN] get_today_price failed: {e}")

    # Fire alerts + circuit breakers immediately (separate messages)
    if today_prices:
        check_alerts_and_circuits(today_prices)

    # Build main update message
    sections = [
        "📈 <b>NEPSE Market Update</b>",
        f"<i>{time_str}</i>",
        build_index_section(scraper),
        build_summary_section(scraper),
        build_gainers_losers_section(scraper),
        build_volume_spikes_section(scraper, today_prices),
        build_watchlist_section(scraper, watch_stocks),
        build_portfolio_section(today_prices),
        "\n<i>Next update in 30 min</i>",
    ]

    send_telegram("\n".join(s for s in sections if s))
    print("Update sent.")


if __name__ == "__main__":
    main()

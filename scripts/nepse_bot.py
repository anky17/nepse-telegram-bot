import os
import sys
from datetime import datetime
from nepse_scraper import NepseScraper
from nepse.common import get_scraper, DIV, NST, fval, ival, tag, compute_sector_avg
from nepse.telegram import send
from nepse.kv import get as kv_get, get_json as kv_get_json, put_json as kv_put_json, put_index_snapshot


# ── KV loaders ────────────────────────────────────────────────────

def load_watch_stocks() -> list[str]:
    raw = kv_get_json("watch_stocks", default=None)
    if raw is None:
        text = kv_get("watch_stocks", "")
        return [s.strip().upper() for s in text.split(",") if s.strip()]
    if isinstance(raw, list):
        return [s.upper() for s in raw]
    return [s.strip().upper() for s in os.environ.get("WATCH_STOCKS", "").split(",") if s.strip()]

def load_alerts() -> dict:
    return kv_get_json("alerts", {})

def load_portfolio() -> dict:
    return kv_get_json("portfolio", {})


# ── Alert & circuit breaker checks ───────────────────────────────

def check_alerts_and_circuits(today_prices: list[dict], now_nst) -> None:
    price_map = {s.get("symbol"): s for s in today_prices if s.get("symbol")}

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
            send(
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

    alerted_today = kv_get_json("alerted_circuits", {})
    new_circuits = []
    new_hits = []

    for stock in today_prices:
        sym = stock.get("symbol")
        if not sym or sym in alerted_today:
            continue
        pct = fval(stock, "percentageChange", "perChange")
        ltp = fval(stock, "lastUpdatedPrice", "ltp", "closePrice")

        if abs(pct) >= 9.5:
            hit_top = pct > 0
            direction = "UPPER CIRCUIT" if hit_top else "LOWER CIRCUIT"
            icon = "🚨" if hit_top else "💥"
            sign = "+" if hit_top else ""
            explainer = (
                "Hit the daily limit for how much it can rise — buyers are lining up, no sellers left today."
                if hit_top else
                "Hit the daily limit for how much it can fall — sellers are rushing out, few buyers left today."
            )
            send(
                f"{icon} <b>{direction} — {sym}</b>\n"
                f"{DIV}\n"
                f"LTP  : Rs {ltp:,.2f}\n"
                f"Change: <b>{sign}{pct:.2f}%</b>\n"
                f"<i>{explainer}</i>"
            )
            alerted_today[sym] = True
            new_circuits.append(sym)
            new_hits.append({
                "symbol": sym,
                "direction": "up" if hit_top else "down",
                "pct": pct,
                "ltp": ltp,
                "time": now_nst.strftime("%I:%M %p"),
            })

    if new_circuits:
        kv_put_json("alerted_circuits", alerted_today)
        print(f"Circuit breakers fired: {new_circuits}")

    if new_hits:
        today_str = now_nst.strftime("%Y-%m-%d")
        state = kv_get_json("site_circuits", {})
        if state.get("date") != today_str:
            state = {"date": today_str, "items": []}
        state["items"] = state.get("items", []) + new_hits
        kv_put_json("site_circuits", state)


# ── Volume spikes ─────────────────────────────────────────────────

def build_volume_spikes_section(scraper: NepseScraper, today_prices: list[dict]) -> str:
    try:
        avg_data = scraper.get_trading_average(n_days=30)
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

def build_index_section(main_idx: dict | None, today_prices: list[dict]) -> str:
    if not main_idx:
        return f"📊 <b>NEPSE Index</b>\n{DIV}\n⚠️ No data"
    current = fval(main_idx, "currentValue")
    change = fval(main_idx, "change")
    pct = fval(main_idx, "perChange")
    high = fval(main_idx, "high")
    low = fval(main_idx, "low")
    icon = "🟢" if change >= 0 else "🔴"

    gainers = sum(1 for s in today_prices if fval(s, "percentageChange", "perChange") > 0)
    losers  = sum(1 for s in today_prices if fval(s, "percentageChange", "perChange") < 0)
    total   = len(today_prices)
    if total > 0 and gainers + losers > 0:
        bull_ratio = gainers / (gainers + losers)
        if bull_ratio >= 0.65:
            sentiment, sent_icon = "Broadly Bullish", "🟢"
        elif bull_ratio >= 0.45:
            sentiment, sent_icon = "Mixed", "🟡"
        else:
            sentiment, sent_icon = "Broadly Bearish", "🔴"
        breadth_line = f"  {sent_icon} Market Mood: <b>{sentiment}</b>   {gainers} up · {losers} down · {total - gainers - losers} flat"
    else:
        breadth_line = ""

    lines = [
        "📊 <b>NEPSE Index</b>", DIV,
        f"{icon}  <b>{current:,.2f}</b>   {tag(change)} pts  ({tag(pct)}%)",
        f"  H: {high:,.2f}   L: {low:,.2f}",
    ]
    if breadth_line:
        lines.append(breadth_line)
    return "\n".join(lines)


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
            # Debentures/bonds have digits in their symbols — filter to equities only
            equities = [s for s in items if (s.get("symbol") or "").isalpha()]
            rows = [f"\n{icon} <b>{header}</b>", DIV]
            for i, s in enumerate(equities[:5], 1):
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


# ── Live board snapshot for the website ────────────────────────────
# get_today_price() already has everything the site's board table needs
# (OHLC, volume, turnover, 52-week range) — mirror ShareSansar's latest.json
# row shape exactly so site/app.js can use either source interchangeably.

def build_site_board(today_prices: list[dict]) -> list[dict]:
    rows = []
    for s in today_prices:
        symbol = s.get("symbol")
        if not symbol:
            continue
        prev_close = fval(s, "previousDayClosePrice")
        ltp = fval(s, "lastUpdatedPrice") or fval(s, "closePrice") or prev_close
        diff_pct = ((ltp - prev_close) / prev_close * 100) if prev_close else 0.0
        rows.append({
            "symbol": symbol,
            "open": fval(s, "openPrice"),
            "high": fval(s, "highPrice"),
            "low": fval(s, "lowPrice"),
            "close": fval(s, "closePrice") or ltp,
            "ltp": ltp,
            "vwap": fval(s, "averageTradedPrice"),
            "vol": ival(s, "totalTradedQuantity"),
            "prevClose": prev_close,
            "turnover": fval(s, "totalTradedValue"),
            "trans": ival(s, "totalTrades"),
            "diffPct": diff_pct,
            "high52": fval(s, "fiftyTwoWeekHigh"),
            "low52": fval(s, "fiftyTwoWeekLow"),
        })
    return rows


# ── Main ──────────────────────────────────────────────────────────

def main():
    now_nst = datetime.now(NST)
    time_str = now_nst.strftime("%a, %d %b %Y  %I:%M %p NST")
    scraper = get_scraper()

    if not scraper.is_market_open():
        print("Market is closed. Skipping.")
        sys.exit(0)

    print(f"[{time_str}] Fetching NEPSE data...")

    watch_stocks = load_watch_stocks()

    today_prices: list[dict] = []
    try:
        today_prices = scraper.get_today_price() or []
    except Exception as e:
        print(f"[WARN] get_today_price failed: {e}")

    if today_prices:
        check_alerts_and_circuits(today_prices, now_nst)

    try:
        indices = scraper.get_nepse_index()
        main_idx = next((i for i in indices if i.get("id") == 58), None)
    except Exception as e:
        print(f"[WARN] Index fetch error: {e}")
        main_idx = None

    if main_idx:
        put_index_snapshot(
            current=fval(main_idx, "currentValue"),
            change=fval(main_idx, "change"),
            pct=fval(main_idx, "perChange"),
            high=fval(main_idx, "high"),
            low=fval(main_idx, "low"),
            date_str=now_nst.strftime("%Y-%m-%d"),
            updated_at=now_nst.isoformat(),
        )

    if today_prices:
        try:
            securities = scraper.get_all_securities()
            sector_avg = compute_sector_avg(securities, today_prices)
            if sector_avg:
                kv_put_json("site_sectors", {
                    "date": now_nst.strftime("%Y-%m-%d"),
                    "updatedAt": now_nst.isoformat(),
                    "sectors": [
                        {"name": k, "pct": v}
                        for k, v in sorted(sector_avg.items(), key=lambda kv: -kv[1])
                    ],
                })
        except Exception as e:
            print(f"[WARN] Sector KV write error: {e}")

        try:
            kv_put_json("site_board", {
                "date": now_nst.strftime("%Y-%m-%d"),
                "updatedAt": now_nst.isoformat(),
                "count": len(today_prices),
                "rows": build_site_board(today_prices),
            })
        except Exception as e:
            print(f"[WARN] Board KV write error: {e}")

    try:
        live = scraper.get_live_indices()
        if live:
            kv_put_json("site_index_intraday", {
                "date": now_nst.strftime("%Y-%m-%d"),
                "points": live,
            })
    except Exception as e:
        print(f"[WARN] Intraday KV write error: {e}")

    sections = [
        "📈 <b>NEPSE Market Update</b>",
        f"<i>{time_str}</i>",
        build_index_section(main_idx, today_prices),
        build_summary_section(scraper),
        build_gainers_losers_section(scraper),
        build_volume_spikes_section(scraper, today_prices),
        build_watchlist_section(scraper, watch_stocks),
        build_portfolio_section(today_prices),
        "\n<i>Next update in 30 min</i>",
    ]

    send("\n".join(s for s in sections if s))
    print("Update sent.")


if __name__ == "__main__":
    main()

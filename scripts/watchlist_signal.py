"""NEPSE ML Buy Signal Scanner.

Fetches history for all active NEPSE equities, engineers technical features
(RSI, MACD, Bollinger Bands, SMA crossovers, volume, momentum), trains a
Random Forest on 10-day forward return labels, and sends top-ranked picks.
"""
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import RobustScaler
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator

from nepse.common import DIV, NST, get_scraper
from nepse.sharesansar import get_history_closes_volumes
from nepse.telegram import send

warnings.filterwarnings("ignore")

MIN_HISTORY = 100    # minimum trading sessions per stock
FORWARD_DAYS = 10   # label: did price rise in next N sessions?
WORKERS = 20        # parallel fetch threads
TOP_N = 8           # stocks shown in message

FEATURE_COLS = [
    "rsi_14", "rsi_6",
    "macd_hist_norm", "macd_cross",
    "bb_pct", "bb_width",
    "price_vs_sma20", "price_vs_sma50",
    "sma20_vs_sma50", "sma50_vs_sma200",
    "vol_ratio",
    "mom_5d", "mom_10d", "mom_20d",
    "obv_mom",
]


# ── Feature Engineering ───────────────────────────────────────────────────────

def build_features(sym: str, closes: list, volumes: list) -> pd.DataFrame | None:
    if len(closes) < MIN_HISTORY:
        return None

    c = pd.Series(closes, dtype=float)
    v = pd.Series(volumes if len(volumes) == len(closes) else [0.0] * len(closes), dtype=float)
    df = pd.DataFrame({"close": c, "volume": v})

    # RSI
    df["rsi_14"] = RSIIndicator(c, window=14).rsi()
    df["rsi_6"] = RSIIndicator(c, window=6).rsi()

    # MACD
    _macd = MACD(c)
    macd_line = _macd.macd()
    macd_sig = _macd.macd_signal()
    df["macd_hist_norm"] = _macd.macd_diff() / df["close"]   # normalized by price
    df["macd_cross"] = (macd_line > macd_sig).astype(float)

    # Bollinger Bands
    bb = BollingerBands(c, window=20, window_dev=2)
    df["bb_pct"] = bb.bollinger_pband()    # 0 = at lower band, 1 = at upper band
    df["bb_width"] = bb.bollinger_wband()  # band width as % of middle

    # SMA relationships
    sma20 = SMAIndicator(c, 20).sma_indicator()
    sma50 = SMAIndicator(c, 50).sma_indicator()
    sma200 = SMAIndicator(c, 200).sma_indicator()
    df["price_vs_sma20"] = (c / sma20 - 1).fillna(0)
    df["price_vs_sma50"] = (c / sma50 - 1).fillna(0)
    df["sma20_vs_sma50"] = (sma20 / sma50 - 1).fillna(0)
    df["sma50_vs_sma200"] = (sma50 / sma200 - 1).fillna(0)  # 0 if SMA200 unavailable

    # Volume ratio
    vol_avg = v.rolling(20).mean().replace(0, np.nan)
    df["vol_ratio"] = (v / vol_avg).fillna(1.0).clip(0, 10)

    # Price momentum
    df["mom_5d"] = c.pct_change(5)
    df["mom_10d"] = c.pct_change(10)
    df["mom_20d"] = c.pct_change(20)

    # OBV momentum (skip if no volume data)
    if v.sum() > 0:
        obv = OnBalanceVolumeIndicator(c, v).on_balance_volume()
        df["obv_mom"] = obv.pct_change(5).fillna(0).clip(-5, 5)
    else:
        df["obv_mom"] = 0.0

    # Forward return label
    df["fwd_ret"] = c.shift(-FORWARD_DAYS) / c - 1
    df["label"] = (df["fwd_ret"] > 0).astype(int)

    df["symbol"] = sym
    df["current_price"] = c
    return df


# ── Data Pipeline ─────────────────────────────────────────────────────────────

def fetch_all(symbols: list) -> dict:
    """Parallel-fetch EOD history for all symbols."""
    result = {}

    def _get(sym):
        return sym, get_history_closes_volumes(sym)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_get, s): s for s in symbols}
        for fut in as_completed(futures):
            try:
                sym, (closes, vols) = fut.result()
                if len(closes) >= MIN_HISTORY:
                    result[sym] = (closes, vols)
            except Exception:
                pass
    return result


def build_dataset(stock_data: dict) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Build training DataFrame and per-stock latest-feature rows."""
    train_parts = []
    latest: dict[str, pd.Series] = {}

    for sym, (closes, vols) in stock_data.items():
        df = build_features(sym, closes, vols)
        if df is None:
            continue

        # Latest row = prediction point (no future label available yet)
        last_row = df.iloc[-1]
        latest[sym] = last_row

        # Training rows: all except last FORWARD_DAYS (no valid label)
        train = df.iloc[:-FORWARD_DAYS].dropna(subset=FEATURE_COLS + ["label"])
        if len(train) >= 20:
            train_parts.append(train[FEATURE_COLS + ["label"]])

    full_train = pd.concat(train_parts, ignore_index=True) if train_parts else pd.DataFrame()
    return full_train, latest


# ── ML Model ──────────────────────────────────────────────────────────────────

def train_and_score(train_df: pd.DataFrame, latest: dict) -> list:
    """Train RF, score all stocks, return list sorted by ML probability."""
    if train_df.empty or not latest:
        return []

    # Clean: replace inf, fill NaN with column median
    X_raw = train_df[FEATURE_COLS].replace([np.inf, -np.inf], np.nan)
    col_medians = X_raw.median()
    X_raw = X_raw.fillna(col_medians)
    y = train_df["label"].values

    scaler = RobustScaler()
    X = scaler.fit_transform(X_raw)

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=6,
        min_samples_leaf=30,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X, y)

    results = []
    for sym, row in latest.items():
        feat = row[FEATURE_COLS].replace([np.inf, -np.inf], np.nan)
        feat = feat.fillna(col_medians)
        try:
            prob = clf.predict_proba(scaler.transform(feat.values.reshape(1, -1)))[0][1]
        except Exception:
            continue
        price = float(row["current_price"])
        signals = _signals(row)
        results.append((sym, prob, price, signals))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _signals(row: pd.Series) -> list[str]:
    """Plain-English reasons why this stock looks like a buy opportunity."""
    out = []
    rsi = row.get("rsi_14", 50)
    if rsi < 30:
        out.append("Price looks undervalued — historically a good entry point")
    elif rsi < 40:
        out.append("Price has dipped — potential buying opportunity")
    elif rsi < 50:
        out.append("Selling pressure is easing, buyers stepping in")

    if row.get("macd_cross", 0) == 1:
        if (row.get("macd_hist_norm", 0) or 0) > 0:
            out.append("Trend is turning upward with strong momentum")
        else:
            out.append("Short-term trend is turning positive")

    if (row.get("sma50_vs_sma200", 0) or 0) > 0:
        out.append("Long-term trend is bullish — big investors are buying")

    bb = row.get("bb_pct", 0.5)
    if (bb or 0.5) < 0.1:
        out.append("Price near strong support — often bounces back from here")
    elif (bb or 0.5) < 0.25:
        out.append("Price approaching a support level")

    vol = row.get("vol_ratio", 1.0)
    if (vol or 1.0) >= 2.0:
        out.append(f"Unusually high trading activity — {vol:.1f}× normal volume")
    elif (vol or 1.0) >= 1.5:
        out.append(f"Higher-than-usual buying interest — {vol:.1f}× normal volume")

    mom5 = row.get("mom_5d", 0) or 0
    if 0 < mom5 < 0.08:
        out.append(f"Price has been rising steadily this past week (+{mom5 * 100:.1f}%)")

    return out or ["Several market indicators are aligning positively"]


# ── Message Builder ───────────────────────────────────────────────────────────

def _bar(prob: float) -> str:
    # Normalize bar to 50–65% range so it reflects relative strength visually
    filled = max(0, min(10, round((prob - 0.50) / 0.15 * 10)))
    return "█" * filled + "░" * (10 - filled)


def _strength_label(prob: float) -> str:
    if prob >= 0.56:
        return "Strong 🔥"
    if prob >= 0.53:
        return "Good 📈"
    return "Moderate"


def build_message(results: list, n_stocks: int, sym_names: dict) -> str:
    now = datetime.now(NST)
    date_str = now.strftime("%a, %d %b %Y")

    top = results[:TOP_N]
    strong = [(s, p, pr, sg) for s, p, pr, sg in top if p >= 0.56]
    watch  = [(s, p, pr, sg) for s, p, pr, sg in top if p < 0.56]

    lines = [
        f"🎯 <b>NEPSE Buy Picks — {date_str}</b>",
        DIV,
        f"<i>Scanned all {n_stocks} listed stocks. Here are today's best opportunities.</i>",
    ]

    detail_list = strong if strong else top[:3]
    watch_list  = watch  if strong else top[3:8]

    if detail_list:
        lines.append(f"\n🔥 <b>{'Strong Picks' if strong else 'Best Picks Today'}</b>")
        for i, (sym, prob, price, sigs) in enumerate(detail_list[:4], 1):
            name = sym_names.get(sym, "")
            header = f"<b>{sym}</b>" + (f" — {name}" if name else "")
            lines += [
                f"\n{i}. {header}",
                f"   💰 Price: <b>Rs {price:,.1f}</b>",
                f"   📊 Buy Signal: <code>[{_bar(prob)}]</code> {_strength_label(prob)}",
                "   <b>Why this stock?</b>",
            ]
            for s in sigs[:3]:
                lines.append(f"   • {s}")

    if watch_list:
        lines.append(f"\n\n👀 <b>Also Worth Watching</b>")
        lines.append(DIV)
        for sym, prob, price, sigs in watch_list[:5]:
            reason = sigs[0] if sigs else "Positive market setup"
            lines.append(f"  • <b>{sym}</b>  Rs {price:,.1f}  — {reason}")

    lines += [
        "",
        DIV,
        "💡 <i>Higher signal = more market indicators agree it's a good time to buy.</i>",
        "⚠️ <i>Not financial advice. Always do your own research before investing.</i>",
    ]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching active NEPSE equities...")
    scraper = get_scraper()
    securities = scraper.get_all_securities()
    active = [
        s for s in securities
        if s.get("status") == "A" and s.get("instrumentType") == "Equity" and s.get("symbol")
    ]
    symbols = [s["symbol"] for s in active]
    sym_names = {s["symbol"]: s.get("companyName", "") for s in active}
    print(f"  {len(symbols)} active equities")

    print(f"Fetching price histories ({WORKERS} parallel workers)...")
    stock_data = fetch_all(symbols)
    print(f"  Got data for {len(stock_data)} stocks")

    print("Building feature dataset...")
    train_df, latest = build_dataset(stock_data)
    print(f"  Training rows: {len(train_df):,}  |  Stocks to score: {len(latest)}")

    print("Training Random Forest and scoring all stocks...")
    results = train_and_score(train_df, latest)

    if not results:
        print("No results — exiting.")
        return

    print(f"\nTop 10 by buy signal:")
    for sym, prob, price, _ in results[:10]:
        print(f"  {sym:<12}  {prob * 100:.1f}%  Rs {price:.1f}")

    msg = build_message(results, len(stock_data), sym_names)
    send(msg)
    print("\nWatchlist signal sent.")



if __name__ == "__main__":
    main()

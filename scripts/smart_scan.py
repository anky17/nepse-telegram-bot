"""NEPSE Pre-Market Anomaly Scanner.

Inspired by:
  - Surpriver (tradytics/surpriver): IsolationForest on recent volume+price features
    to detect unusual patterns that precede large stock moves.
  - StockSight (shirosaidev/stocksight): layering news/sentiment context
    on top of price signals.
  - omnianalyst.com/blog/ml-stock-prediction + MDPI AI 5(3):76:
    volatility regime ratio, mean-reversion z-score, trend quality features.

How it works:
  For each active NEPSE equity, we compute a feature vector over the last 14
  trading days — daily log returns, volume return ratios, and their interaction
  (an EOM proxy since we only have close+volume from ShareSansar).
  IsolationForest scores each stock; lower (more negative) score = more anomalous.
  Stocks with anomalous patterns have historically shown larger absolute moves
  in the days following the anomaly.

Schedule: runs at 10:30 AM NST, 30 minutes before market opens.
"""
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from ta.momentum import RSIIndicator

from nepse.common import DIV, NST, get_scraper
from nepse.sharesansar import get_history_closes_volumes
from nepse.telegram import send

warnings.filterwarnings("ignore")

HISTORY_BARS = 14    # bars in the anomaly feature window (surpriver default)
MIN_HISTORY = 35     # minimum EOD sessions needed to compute valid features
MIN_AVG_VOLUME = 500 # skip extremely illiquid stocks
WORKERS = 20
TOP_N = 8


# ─── Feature helpers ──────────────────────────────────────────────────────────

def _slope(arr: np.ndarray) -> float:
    """Least-squares slope without scipy dependency."""
    n = len(arr)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    mx, my = x.mean(), arr.mean()
    denom = ((x - mx) ** 2).sum()
    return float(((x - mx) * (arr - my)).sum() / denom) if denom > 0 else 0.0


def _build_feature_vector(closes: list, volumes: list) -> np.ndarray | None:
    """
    Build a 1-D feature vector for IsolationForest (one row per stock).

    Features (surpriver-inspired):
      - Last HISTORY_BARS daily log returns          (14 values)
      - Last HISTORY_BARS volume return ratios       (14 values)
      - Last HISTORY_BARS price×volume interaction   (14 values, EOM proxy)
      - Slope of log returns                         (1)
      - Slope of volume returns                      (1)
      - Volume spike vs 5-day avg                    (1)
      - Volume spike vs 20-day avg                   (1)
      - 3-day momentum                               (1)
      - 5-day momentum                               (1)
    Total: 47 features
    """
    c = np.array(closes, dtype=float)
    v = np.array(volumes, dtype=float)

    if len(c) < MIN_HISTORY:
        return None
    if np.mean(v[-21:]) < MIN_AVG_VOLUME:
        return None

    N = HISTORY_BARS

    # Daily log returns (len = len(c)-1)
    log_ret_all = np.diff(np.log(np.maximum(c, 1e-9)))
    log_ret_w = log_ret_all[-N:] if len(log_ret_all) >= N else np.zeros(N)
    log_ret_w = np.nan_to_num(log_ret_w).clip(-0.5, 0.5)

    # Volume return ratios vol[t]/vol[t-1]
    v_safe = np.where(v > 0, v, np.nan)
    vol_ret_all = v_safe[1:] / v_safe[:-1]
    vol_ret_w = vol_ret_all[-N:] if len(vol_ret_all) >= N else np.ones(N)
    vol_ret_w = np.nan_to_num(vol_ret_w, nan=1.0).clip(0.05, 20.0)

    # Pad to exactly N if needed
    if len(log_ret_w) < N:
        log_ret_w = np.concatenate([np.zeros(N - len(log_ret_w)), log_ret_w])
    if len(vol_ret_w) < N:
        vol_ret_w = np.concatenate([np.ones(N - len(vol_ret_w)), vol_ret_w])

    # EOM proxy: price_change_direction × volume_surge (no H/L available)
    eom_proxy = log_ret_w * vol_ret_w

    # Slope features
    slope_ret = _slope(log_ret_w)
    slope_vol = _slope(vol_ret_w)

    # Volume spike ratios
    avg5 = float(np.nanmean(v[-6:-1])) if len(v) >= 6 else float(np.nanmean(v))
    avg20 = float(np.nanmean(v[-21:-1])) if len(v) >= 21 else float(np.nanmean(v))
    spike_5d = float(v[-1] / avg5) if avg5 > 0 else 1.0
    spike_20d = float(v[-1] / avg20) if avg20 > 0 else 1.0

    # Price momentum
    mom_3d = float(c[-1] / c[-4] - 1) if len(c) >= 4 else 0.0
    mom_5d = float(c[-1] / c[-6] - 1) if len(c) >= 6 else 0.0

    # ── Regime / z-score / trend-quality (omnianalyst + MDPI AI 5(3):76) ──
    # Volatility regime: recent 5-bar vol vs. full window vol
    vol_5 = float(np.std(log_ret_w[-5:])) if len(log_ret_w) >= 5 else 0.0
    vol_w = float(np.std(log_ret_w)) if len(log_ret_w) > 1 else 1e-9
    vol_regime = np.clip(vol_5 / vol_w if vol_w > 0 else 1.0, 0.1, 5.0)

    # Mean-reversion z-score: how many std devs is price from its 20-day mean
    if len(c) >= 20:
        roll_mean = float(np.mean(c[-20:]))
        roll_std = float(np.std(c[-20:])) or 1e-9
        price_zscore = np.clip((c[-1] - roll_mean) / roll_std, -4, 4)
    else:
        price_zscore = 0.0

    # Trend quality: 10-day slope normalized by volatility (clean vs. choppy trend)
    trend_quality = 0.0
    if len(c) >= 10:
        pct_10 = float(c[-1] / c[-11] - 1) if len(c) >= 11 else 0.0
        trend_quality = np.clip(pct_10 / vol_w if vol_w > 0 else 0.0, -10, 10)

    feat = np.concatenate([
        log_ret_w,
        vol_ret_w,
        eom_proxy,
        [slope_ret, slope_vol, spike_5d, spike_20d, mom_3d, mom_5d,
         vol_regime, price_zscore, trend_quality],
    ])

    if np.any(np.isnan(feat)) or np.any(np.isinf(feat)):
        return None
    return feat.astype(np.float32)


# ─── Display stats (separate from feature vector) ─────────────────────────────

def _compute_stats(closes: list, volumes: list) -> dict:
    c = np.array(closes, dtype=float)
    v = np.array(volumes, dtype=float)

    ltp = float(c[-1])
    prev = float(c[-2]) if len(c) >= 2 else ltp
    pct_chg = (ltp - prev) / prev * 100 if prev else 0.0

    avg5 = float(np.nanmean(v[-6:-1])) if len(v) >= 6 else float(np.nanmean(v))
    avg20 = float(np.nanmean(v[-21:-1])) if len(v) >= 21 else float(np.nanmean(v))
    latest_vol = float(v[-1])
    spike_5d = latest_vol / avg5 if avg5 > 0 else 1.0
    spike_20d = latest_vol / avg20 if avg20 > 0 else 1.0

    rsi14 = 50.0
    if len(c) >= 15:
        try:
            rsi14 = float(RSIIndicator(pd.Series(c), window=14).rsi().iloc[-1])
        except Exception:
            pass

    mom_5d = float(c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0.0
    mom_10d = float(c[-1] / c[-11] - 1) * 100 if len(c) >= 11 else 0.0

    # 10-bar historical volatility (annualised to % per day)
    volatility = 0.0
    if len(c) >= 10:
        ret10 = np.diff(np.log(np.maximum(c[-10:], 1e-9)))
        volatility = float(np.std(ret10) * 100)

    return {
        "ltp": ltp,
        "pct_chg": pct_chg,
        "spike_5d": spike_5d,
        "spike_20d": spike_20d,
        "latest_vol": latest_vol,
        "avg5_vol": avg5,
        "rsi14": rsi14,
        "mom_5d": mom_5d,
        "mom_10d": mom_10d,
        "volatility": volatility,
    }


def _explain(stats: dict) -> list[str]:
    """Plain-English reasons why this stock is flagged."""
    reasons = []
    spike = stats["spike_5d"]
    rsi = stats["rsi14"]
    mom5 = stats["mom_5d"]
    vol = stats["volatility"]

    if spike >= 3.0:
        reasons.append(f"Volume surge {spike:.1f}× normal — institutional activity likely")
    elif spike >= 2.0:
        reasons.append(f"Volume {spike:.1f}× above 5-day avg — unusual interest")
    elif spike >= 1.4:
        reasons.append(f"Above-average volume — {spike:.1f}× normal")

    if rsi < 30:
        reasons.append("RSI deeply oversold — reversal often follows sharp drops")
    elif rsi < 40:
        reasons.append("RSI oversold — buyers may step in soon")
    elif rsi > 72:
        reasons.append("RSI overbought — breakout momentum in play")
    elif rsi > 62:
        reasons.append("RSI rising — upward momentum building")

    if mom5 > 8:
        reasons.append(f"5-day rally +{mom5:.1f}% — strong short-term trend")
    elif mom5 > 3:
        reasons.append(f"Steady 5-day rise +{mom5:.1f}%")
    elif mom5 < -8:
        reasons.append(f"5-day drop {mom5:.1f}% — potential reversal or continued sell-off")
    elif mom5 < -3:
        reasons.append(f"Down {abs(mom5):.1f}% in 5 days — watch for bounce")

    if vol > 3.0:
        reasons.append(f"High short-term volatility ({vol:.1f}%/day) — big move expected")

    return reasons if reasons else ["Unusual price-volume pattern detected by anomaly model"]


# ─── Message builder ──────────────────────────────────────────────────────────

def _build_message(results: list, total_scanned: int, sym_names: dict) -> str:
    now = datetime.now(NST)
    date_str = now.strftime("%a, %d %b %Y")

    lines = [
        f"🔭 <b>Pre-Market Anomaly Scan — {date_str}</b>",
        DIV,
        f"<i>Scanned {total_scanned} equities · IsolationForest on 14-day price+volume patterns</i>",
        "<i>Stocks with anomalous patterns often see bigger moves in the session ahead.</i>",
        "",
    ]

    for i, (sym, score, stats) in enumerate(results[:TOP_N], 1):
        name = sym_names.get(sym, "")
        ltp = stats["ltp"]
        pct = stats["pct_chg"]
        spike = stats["spike_5d"]
        rsi = stats["rsi14"]
        sign = "+" if pct >= 0 else ""
        arrow = "▲" if pct >= 0 else "▼"
        reasons = _explain(stats)

        header = f"<b>{i}. {sym}</b>"
        if name:
            header += f" · {name}"

        lines += [
            header,
            f"   💰 LTP: <b>Rs {ltp:,.1f}</b>  {arrow} <code>{sign}{pct:.2f}%</code>",
            f"   📦 Vol: {spike:.1f}× normal  · RSI: {rsi:.0f}  · Anomaly score: <code>{score:.3f}</code>",
        ]
        for r in reasons[:2]:
            lines.append(f"   • {r}")
        lines.append("")

    lines += [
        DIV,
        "📌 <b>How to read this scan:</b>",
        "  Lower anomaly score = more unusual pattern = higher potential for a big move today.",
        "  Volume spike + RSI extreme = strongest setup. Use /stock SYM for full details.",
        "",
        "⚠️ <i>Not financial advice. Always do your own research before investing.</i>",
    ]

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Fetching active NEPSE equities...")
    scraper = get_scraper()
    securities = scraper.get_all_securities()
    active = [
        s for s in securities
        if s.get("status") == "A"
        and s.get("instrumentType") == "Equity"
        and s.get("symbol")
    ]
    symbols = [s["symbol"] for s in active]
    sym_names = {s["symbol"]: s.get("companyName", "") for s in active}
    print(f"  {len(symbols)} active equities")

    print(f"Fetching EOD history ({WORKERS} parallel workers)...")
    stock_data: dict[str, tuple[list, list]] = {}

    def _fetch(sym):
        return sym, get_history_closes_volumes(sym)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_fetch, s): s for s in symbols}
        for fut in as_completed(futs):
            try:
                sym, (closes, vols) = fut.result()
                if len(closes) >= MIN_HISTORY and vols:
                    stock_data[sym] = (closes, vols)
            except Exception:
                pass

    print(f"  Got data for {len(stock_data)} stocks")

    print("Building anomaly feature matrix...")
    sym_list, feat_list = [], []
    for sym, (closes, vols) in stock_data.items():
        fv = _build_feature_vector(closes, vols)
        if fv is not None:
            sym_list.append(sym)
            feat_list.append(fv)

    if len(feat_list) < 10:
        print("Not enough valid stocks for anomaly detection. Aborting.")
        return

    X = np.array(feat_list, dtype=np.float32)
    X = StandardScaler().fit_transform(X)

    print(f"Running IsolationForest on {len(sym_list)} stocks...")
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=42, n_jobs=-1)
    iso.fit(X)
    scores = iso.score_samples(X)  # lower (more negative) = more anomalous

    ranked = sorted(zip(sym_list, scores), key=lambda x: x[1])

    results = []
    for sym, score in ranked[: TOP_N * 3]:
        closes, vols = stock_data[sym]
        stats = _compute_stats(closes, vols)
        results.append((sym, float(score), stats))

    msg = _build_message(results, len(sym_list), sym_names)
    send(msg)

    print(f"\nTop anomalous stocks:")
    for sym, score, stats in results[:10]:
        print(f"  {sym:<12} score={score:.3f}  vol_spike={stats['spike_5d']:.1f}x  RSI={stats['rsi14']:.0f}")
    print("Pre-market anomaly scan sent.")


if __name__ == "__main__":
    main()

"""ShareSansar static JSON API — free, no-auth, no rate-limits."""
import requests

_BASE = "https://omitnomis.github.io/ShareSansarScraper/api"
_TIMEOUT = 10


def get_history(symbol: str) -> dict:
    """Return raw {cols, rows, symbol} — callers extract what they need."""
    r = requests.get(f"{_BASE}/history/{symbol.upper()}.json", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_recap() -> dict:
    r = requests.get(f"{_BASE}/recap/latest.json", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_history_closes_volumes(symbol: str) -> tuple[list[float], list[float]]:
    """Return (closes, volumes) oldest-first from full ShareSansar history."""
    r = requests.get(f"{_BASE}/history/{symbol.upper()}.json", timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    cols = data.get("cols", [])
    rows = data.get("rows", [])

    price_idx = next((i for i, c in enumerate(cols) if c == "ltp"), None)
    if price_idx is None:
        price_idx = next((i for i, c in enumerate(cols) if c == "c"), None)
    vol_idx = next((i for i, c in enumerate(cols) if c == "vol"), None)

    if price_idx is None or not rows:
        return [], []

    closes, volumes = [], []
    for row in rows:
        try:
            closes.append(float(str(row[price_idx]).replace(",", "")))
        except (IndexError, TypeError, ValueError):
            pass
        if vol_idx is not None:
            try:
                volumes.append(float(str(row[vol_idx]).replace(",", "")))
            except (IndexError, TypeError, ValueError):
                pass

    return closes, volumes

import pytz
import urllib3
from nepse_scraper import NepseScraper

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NST = pytz.timezone("Asia/Kathmandu")
DIV = "─────────────────"


def get_scraper() -> NepseScraper:
    return NepseScraper(verify_ssl=False)


def fval(d: dict, *keys, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


def ival(d: dict, *keys, default: int = 0) -> int:
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


def compute_sector_avg(securities: list[dict], prices: list[dict]) -> dict[str, float]:
    """Turnover-weighted average % change per sector, from securities + today's prices."""
    sym_sector = {s["symbol"]: s.get("sectorName", "Others") for s in securities}
    bucket: dict[str, list[tuple[float, float]]] = {}
    for p in prices:
        sym = p.get("symbol") or ""
        prev_p = p.get("previousDayClosePrice") or 0
        ltp = p.get("lastUpdatedPrice") or 0
        to = p.get("totalTradedValue") or 0
        if not prev_p or not ltp:
            continue
        pct_p = (ltp - prev_p) / prev_p * 100
        sector = sym_sector.get(sym, "Others")
        bucket.setdefault(sector, []).append((pct_p, to))

    sector_avg = {}
    for sector, items in bucket.items():
        total_to = sum(t for _, t in items)
        sector_avg[sector] = (
            sum(p * t for p, t in items) / total_to if total_to else
            sum(p for p, _ in items) / len(items)
        )
    return sector_avg

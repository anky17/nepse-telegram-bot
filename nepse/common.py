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

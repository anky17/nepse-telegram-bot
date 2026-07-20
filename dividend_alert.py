"""Daily dividend alert — checks all 365 companies for new dividend announcements."""
import os
import sys
import csv
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from kv_utils import kv_get_json, kv_put_json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PRINT_MODE = "--print" in sys.argv
TELEGRAM_TOKEN = "" if PRINT_MODE else os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = "" if PRINT_MODE else os.environ["TELEGRAM_CHAT_ID"]
DIV = "─────────────────"
BASE_URL = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data"


def send_telegram(msg: str):
    if PRINT_MODE:
        print(msg)
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )


def fetch_latest_dividend(symbol: str) -> tuple[str, dict | None]:
    """Returns (symbol, latest_row) or (symbol, None) on failure."""
    try:
        url = f"{BASE_URL}/company-wise/{symbol}/dividend.csv"
        r = requests.get(url, timeout=10)
        if not r.ok:
            return symbol, None
        lines = r.text.strip().splitlines()
        if len(lines) < 2:
            return symbol, None
        reader = csv.DictReader(lines)
        rows = list(reader)
        if not rows:
            return symbol, None
        return symbol, rows[0]  # most recent is first
    except Exception:
        return symbol, None


def main():
    # Load all company symbols
    try:
        r = requests.get(f"{BASE_URL}/company_list.json", timeout=10)
        symbols: list[str] = r.json()
    except Exception as e:
        print(f"[ERROR] Could not fetch company list: {e}")
        return

    print(f"Checking dividends for {len(symbols)} companies...")

    # In print mode skip KV so all dividends appear as "new" for testing
    seen: dict = {} if PRINT_MODE else kv_get_json("dividend_seen", {})
    new_dividends = []

    # Fetch all in parallel
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_latest_dividend, sym): sym for sym in symbols}
        for future in as_completed(futures):
            symbol, row = future.result()
            if not row:
                continue
            fiscal_year = row.get("fiscal_year", "").strip()
            if not fiscal_year:
                continue
            if seen.get(symbol) != fiscal_year:
                new_dividends.append((symbol, row))
                seen[symbol] = fiscal_year

    if not new_dividends:
        print("No new dividends found.")
        return

    if not PRINT_MODE:
        kv_put_json("dividend_seen", seen)

    # Sort alphabetically
    new_dividends.sort(key=lambda x: x[0])

    lines = [f"💰 <b>Dividend Announcement ({len(new_dividends)} companies)</b>", DIV]
    for symbol, row in new_dividends:
        fy = row.get("fiscal_year", "?")
        bonus = row.get("bonus_share", "").strip()
        cash = row.get("cash_dividend", "").strip()
        total = row.get("total_dividend", "").strip()
        closure = row.get("book_closure_date", "").strip()

        parts = []
        if bonus and float(bonus or 0) > 0:
            parts.append(f"Bonus {bonus}%")
        if cash and float(cash or 0) > 0:
            parts.append(f"Cash {cash}%")

        detail = "  +  ".join(parts) if parts else f"Total {total}%"
        lines.append(f"  <b>{symbol}</b>  {fy}  —  {detail}")
        if closure:
            lines.append(f"    Book closure: {closure}")

    lines.append("")
    lines.append("<i>Source: NEPSE via Nepse-All-Scraper</i>")
    send_telegram("\n".join(lines))
    print(f"Sent dividend alert for {len(new_dividends)} companies.")


if __name__ == "__main__":
    main()

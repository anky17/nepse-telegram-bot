"""Daily dividend alert — checks all 365 companies for new announcements."""
import csv
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from nepse.common import DIV
from nepse.telegram import send, PRINT_MODE
from nepse.kv import get_json, put_json

BASE_URL = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data"


def fetch_latest_dividend(symbol: str) -> tuple[str, dict | None]:
    try:
        r = requests.get(f"{BASE_URL}/company-wise/{symbol}/dividend.csv", timeout=10)
        if not r.ok:
            return symbol, None
        lines = r.text.strip().splitlines()
        if len(lines) < 2:
            return symbol, None
        rows = list(csv.DictReader(lines))
        return symbol, rows[0] if rows else None
    except Exception:
        return symbol, None


def main():
    try:
        r = requests.get(f"{BASE_URL}/company_list.json", timeout=10)
        symbols: list[str] = r.json()
    except Exception as e:
        print(f"[ERROR] Could not fetch company list: {e}")
        return

    print(f"Checking dividends for {len(symbols)} companies...")

    seen: dict = {} if PRINT_MODE else get_json("dividend_seen", {})
    new_dividends = []
    all_rows = []  # every company's latest dividend record, regardless of "new" status

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_latest_dividend, sym): sym for sym in symbols}
        for future in as_completed(futures):
            symbol, row = future.result()
            if not row:
                continue
            fiscal_year = row.get("fiscal_year", "").strip()
            if not fiscal_year:
                continue
            all_rows.append((symbol, row))
            if seen.get(symbol) != fiscal_year:
                new_dividends.append((symbol, row))
                seen[symbol] = fiscal_year

    if all_rows and not PRINT_MODE:
        # "[Closed]" marks a book closure that has already passed — a "proposed"
        # dividend on the site should only show ones still pending.
        upcoming = [
            (symbol, row) for symbol, row in all_rows
            if "[Closed]" not in (row.get("book_closure_date", "") or "")
        ]

        def closure_key(item):
            return item[1].get("book_closure_date", "") or ""
        site_items = []
        for symbol, row in sorted(upcoming, key=closure_key)[:40]:
            site_items.append({
                "symbol": symbol,
                "bonus": row.get("bonus_share", "").strip(),
                "cash": row.get("cash_dividend", "").strip(),
                "total": row.get("total_dividend", "").strip(),
                "closure": row.get("book_closure_date", "").strip(),
                "fiscalYear": row.get("fiscal_year", "").strip(),
            })
        put_json("site_dividends", {"items": site_items})

    if not new_dividends:
        print("No new dividends found.")
        return

    if not PRINT_MODE:
        put_json("dividend_seen", seen)

    new_dividends.sort(key=lambda x: x[0])

    lines = [f"💰 <b>Dividend Announcement ({len(new_dividends)} companies)</b>", DIV]
    for symbol, row in new_dividends:
        fy = row.get("fiscal_year", "?")
        bonus = row.get("bonus_share", "").strip()
        cash = row.get("cash_dividend", "").strip()
        total = row.get("total_dividend", "").strip()
        closure = row.get("book_closure_date", "").strip()
        parts = []
        if bonus and float((bonus or "0").replace(",", "")) > 0:
            parts.append(f"Bonus {bonus}%")
        if cash and float((cash or "0").replace(",", "")) > 0:
            parts.append(f"Cash {cash}%")
        detail = "  +  ".join(parts) if parts else f"Total {total}%"
        lines.append(f"  <b>{symbol}</b>  {fy}  —  {detail}")
        if closure:
            lines.append(f"    Book closure: {closure}")

    lines.append("\n<i>Source: NEPSE via Nepse-All-Scraper</i>")
    send("\n".join(lines))
    print(f"Sent dividend alert for {len(new_dividends)} companies.")


if __name__ == "__main__":
    main()

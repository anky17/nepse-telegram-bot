import os
import sys
import urllib3
import requests
from nepse_scraper import NepseScraper
from datetime import datetime
import pytz

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NST = pytz.timezone("Asia/Kathmandu")
PRINT_MODE = "--print" in sys.argv
args = [a for a in sys.argv[1:] if a != "--print"]

TELEGRAM_TOKEN = "" if PRINT_MODE else os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = "" if PRINT_MODE else os.environ["TELEGRAM_CHAT_ID"]

DIV = "─────────────────"


def send_telegram(message: str):
    if PRINT_MODE:
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


def fetch_stock(scraper: NepseScraper, symbol: str) -> str:
    try:
        data = scraper.get_ticker_info(symbol)
    except Exception as e:
        return f"⚠️ <b>{symbol}</b>: could not fetch data — {e}"

    t = data.get("securityDailyTradeDto") or {}
    sec = data.get("security") or {}
    company = sec.get("companyId") or {}
    sector = company.get("sectorMaster") or {}
    group = sec.get("shareGroupId") or {}

    ltp = float(t.get("lastTradedPrice") or t.get("closePrice") or 0)
    prev = float(t.get("previousClose") or 0)
    change = ltp - prev
    pct = (change / prev * 100) if prev else 0
    open_ = float(t.get("openPrice") or 0)
    high = float(t.get("highPrice") or 0)
    low = float(t.get("lowPrice") or 0)
    volume = int(t.get("totalTradeQuantity") or 0)
    trades = int(t.get("totalTrades") or 0)
    w52h = float(t.get("fiftyTwoWeekHigh") or 0)
    w52l = float(t.get("fiftyTwoWeekLow") or 0)
    mktcap = float(data.get("marketCapitalization") or 0)
    listed = float(data.get("stockListedShares") or 0)
    pub_pct = float(data.get("publicPercentage") or 0)
    pro_pct = float(data.get("promoterPercentage") or 0)
    face = float(sec.get("faceValue") or 100)
    networth = float(sec.get("networthBasePrice") or 0)

    icon = "🟢" if change >= 0 else "🔴"
    sign = "+" if change >= 0 else ""
    biz_date = t.get("businessDate", "")

    lines = [
        f"📋 <b>{symbol}</b>  —  {company.get('companyName', sec.get('securityName', ''))}",
        f"<i>{sector.get('sectorDescription', '')}  |  Group {group.get('name', '')}  |  {biz_date}</i>",
        DIV,
        f"{icon}  <b>LTP  Rs {ltp:,.2f}</b>   {sign}{change:.2f} ({sign}{pct:.2f}%)",
        f"   Open  {open_:,.2f}   High  {high:,.2f}   Low  {low:,.2f}",
        f"   Prev Close  {prev:,.2f}",
        "",
        f"📊 <b>Today</b>",
        f"   Volume   {volume:,} shares",
        f"   Trades   {trades:,}",
        "",
        f"📅 <b>52-Week</b>",
        f"   High  {w52h:,.2f}   Low  {w52l:,.2f}",
    ]

    if mktcap:
        lines += [
            "",
            f"🏢 <b>Fundamentals</b>",
            f"   Market Cap   Rs {mktcap / 1_000_000:.1f}M",
            f"   Listed Shares  {listed:,.0f}",
            f"   Face Value   Rs {face:.0f}",
        ]
        if networth:
            pb = ltp / networth if networth else 0
            lines.append(f"   Networth/Share  Rs {networth:.2f}   P/B {pb:.2f}x")
        lines.append(f"   Public {pub_pct:.0f}%   Promoter {pro_pct:.0f}%")

    return "\n".join(lines)


def main():
    symbols = [s.strip().upper() for s in args if s.strip()]
    if not symbols:
        print("Usage: python stock_info.py NABIL [NICA ...] [--print]")
        sys.exit(1)

    scraper = NepseScraper(verify_ssl=False)
    for symbol in symbols:
        send_telegram(fetch_stock(scraper, symbol))


if __name__ == "__main__":
    main()

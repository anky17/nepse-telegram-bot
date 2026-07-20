"""Daily IPO/FPO/rights notice alert from NEPSE."""
import os
import urllib3
import requests
from nepse_scraper import NepseScraper
from kv_utils import kv_get_json, kv_put_json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DIV = "─────────────────"

IPO_KEYWORDS = ["ipo", "fpo", "right share", "rights share", "rights issue",
                "debenture", "mutual fund", "new listing", "public issue"]


def send_telegram(msg: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )


def main():
    scraper = NepseScraper(verify_ssl=False)

    try:
        notices = scraper.call_endpoint("notice_api")
    except Exception as e:
        print(f"[WARN] Failed to fetch notices: {e}")
        return

    if not isinstance(notices, list):
        print(f"[WARN] Unexpected notices format: {type(notices)}")
        return

    seen: dict = kv_get_json("seen_notices", {})
    new_notices = []

    for notice in notices:
        notice_id = str(notice.get("id") or notice.get("noticeId") or "")
        title = (notice.get("title") or notice.get("subject") or notice.get("description") or "").lower()

        if not notice_id or notice_id in seen:
            continue

        if any(kw in title for kw in IPO_KEYWORDS):
            new_notices.append(notice)
            seen[notice_id] = True

    if not new_notices:
        print("No new IPO/rights notices.")
        return

    kv_put_json("seen_notices", seen)

    lines = [f"📢 <b>New IPO / Rights Notice</b> ({len(new_notices)})", DIV]
    for notice in new_notices:
        title = notice.get("title") or notice.get("subject") or "Untitled"
        lines.append(f"  • {title}")

    lines += ["", "Check <b>Mero Share</b> or <b>NEPSE website</b> for full details."]
    send_telegram("\n".join(lines))
    print(f"Sent {len(new_notices)} IPO/rights notices.")


if __name__ == "__main__":
    main()

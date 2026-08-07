"""NEPSE announcements and notices alert."""
import requests
from datetime import datetime
from nepse.common import get_scraper, DIV, NST
from nepse.telegram import send
from nepse.kv import get_json as kv_get_json, put_json as kv_put_json

SCRAPER_BASE = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data"

IMPORTANT_KEYWORDS = [
    "ipo", "fpo", "right share", "rights share", "rights issue",
    "debenture", "mutual fund", "new listing", "public issue",
    "merger", "acquisition", "dividend", "bonus share",
    "book closure", "agm", "egm", "suspension", "delisting",
]


def fetch_scraper_notices() -> list[dict]:
    """Fetch pre-aggregated notices from Nepse-All-Scraper."""
    try:
        r = requests.get(f"{SCRAPER_BASE}/notices.json", timeout=10)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else data.get("notices", [])
    except Exception as e:
        print(f"[WARN] Scraper notices fetch failed: {e}")
    return []


def fetch_nepse_notices(scraper) -> list[dict]:
    try:
        notices = scraper.call_endpoint("notice_api")
        return notices if isinstance(notices, list) else []
    except Exception as e:
        print(f"[WARN] NEPSE notice_api failed: {e}")
    return []


def is_important(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in IMPORTANT_KEYWORDS)


def extract_id(notice: dict) -> str:
    return str(notice.get("id") or notice.get("noticeId") or notice.get("notice_id") or "")


def extract_title(notice: dict) -> str:
    # "noticeHeading" is what NEPSE's own notice_api actually uses; the others
    # cover Nepse-All-Scraper's differently-shaped pre-aggregated notices.
    return (
        notice.get("title") or notice.get("subject") or notice.get("notice_title") or
        notice.get("noticeHeading") or notice.get("description") or notice.get("noticeBody") or ""
    ).strip()


def main():
    scraper = get_scraper()
    seen: dict = kv_get_json("seen_notices_v2", {})
    new_notices = []

    # Try Nepse-All-Scraper first (more reliable), fall back to NEPSE API
    notices = fetch_scraper_notices() or fetch_nepse_notices(scraper)

    if not notices:
        print("No notices available from any source.")
        return

    site_items = []
    for notice in notices:
        title = extract_title(notice)
        notice_id = extract_id(notice)
        if not title:
            continue
        site_items.append({"id": notice_id, "title": title, "important": is_important(title)})
    site_items.sort(key=lambda n: not n["important"])
    kv_put_json("site_notices", {
        "date": datetime.now(NST).strftime("%Y-%m-%d"),
        "items": site_items[:15],
    })

    for notice in notices:
        notice_id = extract_id(notice)
        title = extract_title(notice)

        if not notice_id or not title:
            continue
        if notice_id in seen:
            continue

        new_notices.append({"id": notice_id, "title": title, "important": is_important(title)})
        seen[notice_id] = True

    if not new_notices:
        print("No new notices.")
        return

    kv_put_json("seen_notices_v2", seen)

    important = [n for n in new_notices if n["important"]]
    regular = [n for n in new_notices if not n["important"]]

    date_str = datetime.now(NST).strftime("%d %b %Y")
    lines = [f"📢 <b>NEPSE Notices — {date_str}</b>", DIV]

    if important:
        lines.append(f"\n🔔 <b>Important ({len(important)})</b>")
        for n in important[:10]:
            lines.append(f"  • {n['title']}")

    if regular:
        lines.append(f"\n📋 <b>Other Notices ({len(regular)})</b>")
        for n in regular[:5]:
            lines.append(f"  • {n['title']}")
        if len(regular) > 5:
            lines.append(f"  <i>...and {len(regular) - 5} more</i>")

    lines.append("\n<i>Source: NEPSE  ·  Check nepse.com.np for full details</i>")
    send("\n".join(lines))
    print(f"Sent notices: {len(important)} important, {len(regular)} regular.")


if __name__ == "__main__":
    main()

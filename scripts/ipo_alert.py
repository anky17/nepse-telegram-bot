"""Daily IPO/FPO/rights notice alert from NEPSE."""
from nepse.common import get_scraper, DIV
from nepse.telegram import send
from nepse.kv import get_json as kv_get_json, put_json as kv_put_json

IPO_KEYWORDS = ["ipo", "fpo", "right share", "rights share", "rights issue",
                "debenture", "mutual fund", "new listing", "public issue"]


def main():
    scraper = get_scraper()

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
        raw_title = (
            notice.get("title") or notice.get("subject") or
            notice.get("noticeHeading") or notice.get("description") or ""
        )
        title = raw_title.lower()

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
        title = notice.get("title") or notice.get("subject") or notice.get("noticeHeading") or "Untitled"
        lines.append(f"  • {title}")

    lines += ["", "Check <b>Mero Share</b> or <b>NEPSE website</b> for full details."]
    send("\n".join(lines))
    print(f"Sent {len(new_notices)} IPO/rights notices.")


if __name__ == "__main__":
    main()

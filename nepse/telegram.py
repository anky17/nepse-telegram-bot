import os
import sys
import requests

PRINT_MODE = "--print" in sys.argv
_TOKEN = "" if PRINT_MODE else os.environ.get("TELEGRAM_TOKEN", "")
_CHAT_ID = "" if PRINT_MODE else os.environ.get("TELEGRAM_CHAT_ID", "")


def send(msg: str) -> None:
    if PRINT_MODE:
        print(msg)
        print()
        return
    r = requests.post(
        f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
        json={"chat_id": _CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")

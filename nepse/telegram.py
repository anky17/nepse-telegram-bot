import os
import sys
import requests

PRINT_MODE = "--print" in sys.argv
_TOKEN = "" if PRINT_MODE else os.environ.get("TELEGRAM_TOKEN", "")
_CHAT_ID = "" if PRINT_MODE else os.environ.get("TELEGRAM_CHAT_ID", "")

# Telegram's hard limit is 4096 chars; leave margin for safety.
_MAX_LEN = 3900


def _chunk(msg: str) -> list[str]:
    """Split on line boundaries so HTML tags (each self-contained per line) never get cut mid-tag."""
    if len(msg) <= _MAX_LEN:
        return [msg]
    chunks = []
    current = ""
    for line in msg.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _MAX_LEN and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _send_one(msg: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
        json={"chat_id": _CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )
    if not r.ok:
        print(f"[ERROR] Telegram send failed: {r.text}")


def send(msg: str) -> None:
    if PRINT_MODE:
        print(msg)
        print()
        return
    for chunk in _chunk(msg):
        _send_one(chunk)

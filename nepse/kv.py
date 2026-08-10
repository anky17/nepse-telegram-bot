import os
import json
import requests

_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "")
_TOKEN = os.environ.get("CF_API_TOKEN", "")
_NS = os.environ.get("CF_KV_NAMESPACE_ID", "")
_BASE = f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT}/storage/kv/namespaces/{_NS}"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def get(key: str, default=None):
    if not _ACCOUNT:
        return default
    try:
        r = requests.get(f"{_BASE}/values/{key}", headers=_HEADERS, timeout=10)
        if r.ok and r.text:
            return r.text
    except Exception as e:
        print(f"[WARN] KV get {key}: {e}")
    return default


def get_json(key: str, default=None):
    raw = get(key)
    if raw is None:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except Exception:
        return default if default is not None else {}


def put(key: str, value: str):
    if not _ACCOUNT:
        return
    try:
        r = requests.put(f"{_BASE}/values/{key}", headers=_HEADERS, data=value, timeout=20)
        if not r.ok:
            print(f"[WARN] KV put {key}: HTTP {r.status_code}: {r.text[:300]}")
    except Exception as e:
        print(f"[WARN] KV put {key}: {e}")


def put_json(key: str, value):
    put(key, json.dumps(value))


def put_index_snapshot(current, change, pct, high, low, date_str, updated_at):
    put_json("site_index", {
        "current": current,
        "change": change,
        "pct": pct,
        "high": high,
        "low": low,
        "date": date_str,
        "updatedAt": updated_at,
    })


def delete(key: str):
    if not _ACCOUNT:
        return
    try:
        requests.delete(f"{_BASE}/values/{key}", headers=_HEADERS, timeout=10)
    except Exception as e:
        print(f"[WARN] KV delete {key}: {e}")

"""Shared Cloudflare KV read/write utilities."""
import os
import json
import requests

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_KV_NAMESPACE_ID = os.environ.get("CF_KV_NAMESPACE_ID", "")

_BASE = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}"
)
_HEADERS = {"Authorization": f"Bearer {CF_API_TOKEN}"}


def kv_get(key: str, default=None):
    if not CF_ACCOUNT_ID:
        return default
    try:
        r = requests.get(f"{_BASE}/values/{key}", headers=_HEADERS, timeout=10)
        if r.ok and r.text:
            return r.text
    except Exception as e:
        print(f"[WARN] KV get {key}: {e}")
    return default


def kv_get_json(key: str, default=None):
    raw = kv_get(key)
    if raw is None:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except Exception:
        return default if default is not None else {}


def kv_put(key: str, value: str):
    if not CF_ACCOUNT_ID:
        return
    try:
        requests.put(f"{_BASE}/values/{key}", headers=_HEADERS, data=value, timeout=10)
    except Exception as e:
        print(f"[WARN] KV put {key}: {e}")


def kv_put_json(key: str, value):
    kv_put(key, json.dumps(value))

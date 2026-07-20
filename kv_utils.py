"""Backward-compat shim — use nepse.kv directly in new code."""
from nepse.kv import get as kv_get, get_json as kv_get_json, put as kv_put, put_json as kv_put_json

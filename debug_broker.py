"""Try floorsheet with POST payload and different param styles."""
from nepse_scraper import NepseScraper
import json, urllib3
urllib3.disable_warnings()

scraper = NepseScraper(verify_ssl=False)
scraper._get_security_map()
nabil_id = scraper._security_map.get("NABIL")
print(f"NABIL ID: {nabil_id}\n")

# Try 1: POST with security ID in body
print("--- Try POST with id in payload ---")
try:
    scraper.register_endpoint("fs_post", "/api/nots/nepse-data/floorsheet", method="POST")
    r = scraper.call_endpoint("fs_post", payload={"id": nabil_id, "size": 10, "page": 0, "sort": "contractId,desc"})
    print(json.dumps(r, indent=2)[:1500])
except Exception as e:
    print(f"Error: {e}")

# Try 2: GET with securityId as query param (different key names)
print("\n--- Try GET with various param keys ---")
for params in [
    {"securityId": nabil_id, "size": 10, "page": 0},
    {"id": nabil_id, "size": 10, "page": 0},
    {"stockId": nabil_id, "size": 10, "page": 0},
    {"size": 500, "page": 0},
]:
    try:
        scraper.register_endpoint("fs_get", "/api/nots/nepse-data/floorsheet", method="GET")
        r = scraper.call_endpoint("fs_get", params=params)
        if r and r != []:
            print(f"✅ params={params}")
            print(json.dumps(r, indent=2)[:1500])
            break
        else:
            print(f"Empty: params={params}")
    except Exception as e:
        print(f"Error params={params}: {e}")

# Try 3: check what today_price looks like for NABIL specifically
print("\n--- Today price for NABIL ---")
try:
    prices = scraper.get_today_price()
    nabil = next((p for p in prices if p.get("symbol") == "NABIL"), None)
    print(json.dumps(nabil, indent=2))
except Exception as e:
    print(f"Error: {e}")

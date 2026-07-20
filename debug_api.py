"""Round 2: probe NEPSE auth token + alternative sources."""
import urllib.request, urllib.parse, json, ssl, http.cookiejar

ctx = ssl.create_default_context()

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

def post(url, data=b"", headers={}):
    req = urllib.request.Request(url, data=data, headers={**BROWSER_HEADERS, **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()
    except Exception as e:
        return None, {}, str(e)

def get(url, headers={}):
    req = urllib.request.Request(url, headers={**BROWSER_HEADERS, **headers})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()
    except Exception as e:
        return None, {}, str(e)

def show(label, status, headers, body):
    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"  Status: {status}")
    if "Set-Cookie" in headers:
        print(f"  Cookie: {headers['Set-Cookie'][:80]}")
    print(body[:800])

# ── 1. NEPSE: POST to auth to get token ──────────────────────────
print("\n>>> 1. NEPSE auth via POST (empty body)")
s, h, b = post("https://nepalstock.com.np/api/authenticate",
                headers={"Referer": "https://nepalstock.com.np/", "Content-Type": "application/json"})
show("POST /api/authenticate", s, h, b)

print("\n>>> 2. NEPSE homepage (look for token in JS)")
s, h, b = get("https://nepalstock.com.np/", {"Accept": "text/html"})
# Search for token patterns in the HTML
import re
tokens = re.findall(r'token["\s:=]+(["\w\-\.]{20,})', b[:5000], re.IGNORECASE)
print(f"  Status: {s}  |  Token patterns found: {tokens[:3]}")

# ── 3. nepsealpha.com ─────────────────────────────────────────────
print("\n>>> 3. nepsealpha.com")
for path in ["/api/market-index", "/api/live", "/trading/live"]:
    s, h, b = get(f"https://nepsealpha.com{path}")
    print(f"\n  GET {path}  →  {s}")
    print(f"  {b[:300]}")

# ── 4. nepalipaisa.com ───────────────────────────────────────────
print("\n>>> 4. nepalipaisa.com")
for url in [
    "https://www.nepalipaisa.com/api/GetStockDetails/?StockSymbol=NABIL",
    "https://www.nepalipaisa.com/Modules/MarketUpdate/MarketSummary.aspx",
]:
    s, h, b = get(url)
    print(f"\n  GET {url.split('/')[-1][:40]}  →  {s}")
    print(f"  {b[:300]}")

# ── 5. nepalstock.com (old site) ─────────────────────────────────
print("\n>>> 5. nepalstock.com (old API)")
for path in ["/api/nots/nepse-data/index", "/api/nots/market-open"]:
    s, h, b = get(f"https://www.nepalstock.com{path}")
    print(f"\n  GET {path}  →  {s}")
    print(f"  {b[:300]}")

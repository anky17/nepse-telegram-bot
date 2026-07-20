"""Print raw field names from nepse-scraper responses."""
from nepse_scraper import NepseScraper
import json

scraper = NepseScraper(verify_ssl=False)

print("\n=== NEPSE INDEX (all entries) ===")
indices = scraper.get_nepse_index()
for idx in indices:
    print(json.dumps(idx, indent=2))
    print("---")

print("\n=== MARKET SUMMARY ===")
summary = scraper.call_endpoint("market_summary_api")
print(json.dumps(summary, indent=2))

print("\n=== TOP GAINER (first item fields) ===")
gainers = scraper.get_top_stocks(category="top_gainer")
if gainers:
    print(json.dumps(gainers[0], indent=2))

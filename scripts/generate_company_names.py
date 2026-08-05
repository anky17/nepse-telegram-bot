"""Generate site/companies.json — a static symbol -> full company name mapping.

Run manually whenever new companies list on NEPSE:
    python scripts/generate_company_names.py
"""
import json
import os

from nepse_scraper import NepseScraper

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "site", "companies.json")


def main():
    scraper = NepseScraper()
    securities = scraper.get_securities_list()
    mapping = {
        s["securitySymbol"]: s["securityName"].strip()
        for s in securities
        if s.get("securitySymbol") and s.get("securityName")
    }
    with open(OUT_PATH, "w") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)
    print(f"Wrote {len(mapping)} symbol -> company name entries to {OUT_PATH}")


if __name__ == "__main__":
    main()

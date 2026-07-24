"""Latest NEPSE market news scraped from ShareSansar."""
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from nepse.common import DIV, NST
from nepse.telegram import send
from nepse.kv import get_json as kv_get_json, put_json as kv_put_json

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")

# Title must contain at least one of these to be included
_SHARE_KEYWORDS = {
    "share", "shares", "stock", "stocks", "equity",
    "nepse", "market", "index",
    "trade", "trading", "turnover", "transaction", "floorsheet",
    "ipo", "fpo", "right share", "right issue", "bonus share", "bonus",
    "dividend", "debenture", "mutual fund", "fund",
    "broker", "depository", "cds",
    "agm", "egm", "annual general", "extraordinary general",
    "sebon", "securities board",
    "bull", "bear", "rally", "circuit breaker",
    "listed", "listing", "delisted", "delisting",
    "sector", "bank", "insurance", "hydropower", "finance company",
    "merger", "acquisition", "promoter",
}


def _is_share_news(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _SHARE_KEYWORDS)

NEWS_URL = "https://www.sharesansar.com/category/latest"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NepseBotScraper/1.0)"}


def _title_from_slug(slug: str) -> str:
    """Derive a readable title from a newsdetail URL slug as fallback."""
    # slug format: some-title-words-YYYY-MM-DD
    parts = slug.split("-")
    # strip trailing date (YYYY-MM-DD = 3 numeric parts)
    while len(parts) > 1 and parts[-1].isdigit() and len(parts[-1]) in (2, 4):
        parts.pop()
    return " ".join(parts).title()


def fetch_articles() -> list[dict]:
    r = requests.get(NEWS_URL, timeout=15, headers=_HEADERS)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    seen_slugs: set[str] = set()
    articles = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "/newsdetail/" not in href:
            continue
        slug = href.rstrip("/").split("/")[-1]
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Prefer a heading element nested inside the card link
        heading = a.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heading:
            title = heading.get_text(separator=" ", strip=True)
        else:
            title = a.get_text(separator=" ", strip=True)

        # Fall back to slug-derived title for image-only cards
        if len(title) < 30:
            title = _title_from_slug(slug)
        if len(title) < 20:
            continue

        # Only keep articles with a date in the slug (filters out evergreen sidebar links)
        date_match = _DATE_RE.search(slug)
        if not date_match:
            continue
        article_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
        cutoff = datetime.now(NST).date() - timedelta(days=30)
        if article_date < cutoff:
            continue

        if not _is_share_news(title):
            continue

        url = href if href.startswith("http") else f"https://www.sharesansar.com{href}"
        articles.append({"slug": slug, "title": title, "url": url, "date": str(article_date)})

    return articles


def main():
    seen: dict = kv_get_json("seen_news_sharesansar", {})
    articles = fetch_articles()
    new_articles = [a for a in articles if a["slug"] not in seen]

    if not new_articles:
        print("No new articles.")
        return

    for a in new_articles:
        seen[a["slug"]] = True
    kv_put_json("seen_news_sharesansar", seen)

    date_str = datetime.now(NST).strftime("%d %b %Y  %I:%M %p NST")
    lines = [f"📰 <b>NEPSE Market News — {date_str}</b>", DIV]

    for i, a in enumerate(new_articles[:8], 1):
        lines.append(f"\n{i}. <b>{a['title']}</b>")
        lines.append(f"   <a href=\"{a['url']}\">Read →</a>")

    lines.append(f"\n<i>Source: ShareSansar · sharesansar.com/category/latest</i>")
    send("\n".join(lines))
    print(f"Sent {min(len(new_articles), 8)} new articles.")


if __name__ == "__main__":
    main()

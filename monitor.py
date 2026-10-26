
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://p-bandai.com"
SHOPS_URL = f"{BASE_URL}/us/shops"
HOME_URL = f"{BASE_URL}/us/?lang=en"
STATE_FILE = Path("seen_products.json")

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

# Keywords used to identify trading-card products across TCG brands.
TCG_TERMS = [
    "card game", "trading card", "tcg",
    "one piece card", "digimon", "dragon ball super",
    "gundam card", "union arena", "battle spirits",
    "pokemon card", "pokemon tcg", "card pack",
    "card set", "card collection", "card sleeves",
    "booster pack", "starter deck", "deck build box",
    "premium card", "limited card pack",
]

# Only accept product-detail URLs, not category/search links.
PRODUCT_PATHS = ("/us/item/", "/us/hotdeals/")


def get_page(url):
    response = requests.get(
        url, headers=HEADERS, timeout=25
    )
    response.raise_for_status()
    return response.text


def find_card_shop_url():
    """Discover the official Bandai Card Shop link."""
    soup = BeautifulSoup(get_page(SHOPS_URL), "html.parser")

    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.stripped_strings).lower()
        if "bandai card shop" in text:
            return urljoin(BASE_URL, anchor["href"])

    # Safe fallback: the official shop directory.
    # The monitor will also inspect product links on the homepage.
    print("Card Shop link not found; using homepage fallback.")
    return HOME_URL


def is_tcg_product(title, url):
    text = f"{title} {url}".lower()
    return any(term in text for term in TCG_TERMS)


def get_products(page_url):
    """Extract matching product-detail links from a page."""
    soup = BeautifulSoup(get_page(page_url), "html.parser")
    products = {}

    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        parsed = urlparse(href)

        if parsed.netloc not in ("p-bandai.com", "www.p-bandai.com"):
            continue

        if not any(path in parsed.path for path in PRODUCT_PATHS):
            continue

        title = " ".join(anchor.stripped_strings)
        title = re.sub(r"\s+", " ", title).strip()

        # Some product cards put the title in a nested image.
        if not title:
            image = anchor.find("img", alt=True)
            if image:
                title = image["alt"].strip()

        if not title or len(title) < 4:
            continue

        if not is_tcg_product(title, href):
            continue

        # Ignore tracking/query parameters for stable product IDs.
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        products[clean_url] = {
            "title": title[:250],
            "url": clean_url,
        }

    return products


def send_alert(product):
    payload = {
        "username": "P-Bandai TCG Alerts",
        "embeds": [{
            "title": product["title"][:256],
            "url": product["url"],
            "description": "🆕 New TCG product listing detected on Premium Bandai USA.",
            "color": 16753920,
            "footer": {"text": "Premium Bandai USA • New listing"},
        }],
        "allowed_mentions": {"parse": []},
    }

    response = requests.post(
        WEBHOOK_URL, json=payload, timeout=20
    )
    response.raise_for_status()


def main():
    if not WEBHOOK_URL:
        sys.exit("Missing DISCORD_WEBHOOK_URL secret.")

    # Discover the official card shop and inspect both it and
    # the homepage, since some new items may be surfaced there.
    shop_url = find_card_shop_url()
    pages = list(dict.fromkeys([shop_url, HOME_URL]))
    products = {}

    for page_url in pages:
        try:
            found = get_products(page_url)
            products.update(found)
            print(f"Found {len(found)} matching products on {page_url}")
        except Exception as error:
            print(f"Could not scan {page_url}: {error}")

    if not products:
        sys.exit(
            "No matching products found. The page may be dynamic, "
            "blocked, or its URL structure may have changed. "
            "Check the workflow logs before relying on alerts."
        )

    if STATE_FILE.exists():
        seen = set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        first_run = False
    else:
        seen = set()
        first_run = True

    current_urls = set(products)
    new_urls = current_urls - seen

    if first_run:
        # Establish a baseline without flooding Discord with old items.
        print(f"First run: saving baseline of {len(current_urls)} products.")
    else:
        for url in sorted(new_urls):
            try:
                send_alert(products[url])
                print(f"Alert sent: {products[url]['title']}")
            except Exception as error:
                # Do not mark a product seen if the alert failed.
                print(f"Failed to alert for {url}: {error}")
                sys.exit(1)

    # Keep previous IDs as well as current IDs so an item disappearing
    # and later returning won't trigger a duplicate "new listing" alert.
    seen.update(current_urls)
    STATE_FILE.write_text(
        json.dumps(sorted(seen), indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

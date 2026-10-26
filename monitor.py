
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://p-bandai.com"
LISTING_URLS = [
    "https://p-bandai.com/us/shops",
    "https://p-bandai.com/us/?lang=en",
]
STATE_FILE = Path("seen_products.json")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TCGAvailabilityMonitor/1.0)"
}
PRODUCT_PATH = re.compile(r"^/us/(?:item|hotdeals)/", re.I)

LOTTERY_TERMS = [
    "chance to buy",
    "lottery",
    "drawing entry",
    "enter for a chance",
]
UNAVAILABLE_TERMS = [
    "sold out",
    "out of stock",
    "currently unavailable",
    "temporarily unavailable",
]
AVAILABLE_BUTTON_TERMS = [
    "add to cart",
    "add to bag",
    "buy now",
    "pre-order",
    "place order",
    "add to basket",
]


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            # Support the older version that stored URLs only.
            return {url: {"status": "unknown"} for url in data}
    except (json.JSONDecodeError, OSError):
        print("Could not read saved product history.")

    return {}


def get_soup(url):
    response = requests.get(
        url, headers=HEADERS, timeout=25
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def discover_products():
    products = {}

    for listing_url in LISTING_URLS:
        try:
            soup = get_soup(listing_url)

            for link in soup.find_all("a", href=True):
                href = link["href"].strip()
                url = urljoin(BASE_URL, href).split("#")[0]

                if not PRODUCT_PATH.match(
                    __import__("urllib.parse", fromlist=["urlparse"])
                    .urlparse(url).path
                ):
                    continue

                title = link.get_text(" ", strip=True)
                if url not in products:
                    products[url] = title or url

        except requests.RequestException as error:
            print(f"Could not scan {listing_url}: {error}")

    return products


def check_availability(url):
    try:
        soup = get_soup(url)
    except requests.RequestException as error:
        print(f"Could not check product {url}: {error}")
        return "unknown", url

    title_tag = soup.find("h1")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""

    page_text = soup.get_text(" ", strip=True).lower()

    # Do not mistake a lottery entry page for a purchasable product.
    if any(term in page_text for term in LOTTERY_TERMS):
        return "lottery", title or url

    if any(term in page_text for term in UNAVAILABLE_TERMS):
        return "unavailable", title or url

    # Check actual buttons and form controls, not just product descriptions.
    controls = []
    for element in soup.find_all(
        ["button", "input", "a"], limit=1000
    ):
        label = " ".join([
            element.get_text(" ", strip=True),
            element.get("value", ""),
            element.get("aria-label", ""),
        ]).lower()
        controls.append(label)

    for label in controls:
        if any(term in label for term in AVAILABLE_BUTTON_TERMS):
            return "available", title or url

    return "unknown", title or url


def send_alert(title, url):
    if not WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL secret is missing.")
        return False

    payload = {
        "username": "P-Bandai TCG Alerts",
        "content": "@everyone 🆕 **TCG PRODUCT NOW AVAILABLE!**",
        "embeds": [{
            "title": title[:256],
            "url": url,
            "description": (
                "A Premium Bandai USA product appears to be "
                "available to order. Check out immediately."
            ),
            "color": 16753920,
            "footer": {"text": "Premium Bandai USA"},
        }],
        "allowed_mentions": {"parse": ["everyone"]},
    }

    try:
        response = requests.post(
            WEBHOOK_URL, json=payload, timeout=20
        )
        response.raise_for_status()
        print(f"Discord alert sent: {title}")
        return True
    except requests.RequestException as error:
        print(f"Discord alert failed: {error}")
        return False


def main():
    previous = load_state()
    current = discover_products()

    if not current:
        print(
            "No product links found. The site may load products "
            "with JavaScript; keeping the previous history."
        )
        return

    print(f"Found {len(current)} product links.")

    updated = dict(previous)
    alerts_sent = 0

    for url, listing_title in current.items():
        status, page_title = check_availability(url)
        old_entry = previous.get(url, {})
        old_status = (
            old_entry.get("status", "unknown")
            if isinstance(old_entry, dict)
            else "unknown"
        )

        title = page_title if page_title != url else listing_title

        print(f"{status.upper()}: {title} ({url})")

        if status == "unknown":
            # Don't overwrite a known status when the page is unclear.
            if url not in updated:
                updated[url] = {"status": "unknown", "title": title}
            continue

        if status == "available" and (
    old_status in ("unavailable", "lottery") or url not in previous
):
            if send_alert(title, url):
                alerts_sent += 1

        updated[url] = {"status": status, "title": title}

    STATE_FILE.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Finished. Alerts sent: {alerts_sent}")


if __name__ == "__main__":
    main()

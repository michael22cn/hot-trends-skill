import requests
from bs4 import BeautifulSoup
from typing import Dict, List
from urllib.parse import urljoin


def get_techmeme_trending(limit: int = 20) -> List[Dict]:
    url = "https://www.techmeme.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    items = []
    seen = set()
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        selectors = [
            "a.ourh",
            "strong a[href]",
        ]
        for selector in selectors:
            for anchor in soup.select(selector):
                title = " ".join(anchor.get_text(" ", strip=True).split())
                href = (anchor.get("href") or "").strip()
                if not title or len(title) < 8 or title in seen:
                    continue
                seen.add(title)
                items.append(
                    {
                        "title": title,
                        "channel": "front_page",
                        "source": "techmeme.com",
                        "url": urljoin(url, href),
                        "platform": "techmeme",
                    }
                )
                if len(items) >= limit:
                    return items
    except Exception as e:
        print(f"Techmeme error: {e}")

    return items

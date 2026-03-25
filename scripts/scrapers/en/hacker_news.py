import requests
from typing import Dict, List


def get_hacker_news_trending(limit: int = 30) -> List[Dict]:
    url = "https://hn.algolia.com/api/v1/search"
    items = []

    try:
        resp = requests.get(
            url,
            params={"tags": "front_page", "hitsPerPage": min(limit, 50)},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for hit in data.get("hits", [])[:limit]:
            title = (hit.get("title") or hit.get("story_title") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "points": hit.get("points", 0),
                    "author": hit.get("author", ""),
                    "comments": hit.get("num_comments", 0),
                    "channel": "front_page",
                    "url": hit.get("url") or hit.get("story_url") or "",
                    "platform": "hacker_news",
                }
            )
    except Exception as e:
        print(f"Hacker News error: {e}")

    return items

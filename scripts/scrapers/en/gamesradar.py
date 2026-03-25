import feedparser
from typing import Dict, List


def get_gamesradar_trending(limit: int = 20) -> List[Dict]:
    rss_url = "https://www.gamesradar.com/feeds/tag/games/"
    feed = feedparser.parse(rss_url)

    items = []
    for entry in feed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "title": title,
                "channel": "games_news_feed",
                "source": "gamesradar.com/feeds/tag/games",
                "published": entry.get("published", ""),
                "url": entry.get("link", ""),
                "platform": "gamesradar",
            }
        )

    return items

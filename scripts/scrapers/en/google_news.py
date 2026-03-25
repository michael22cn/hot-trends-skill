import feedparser
from typing import List, Dict

def get_google_news(topic: str = "h", limit: int = 20) -> List[Dict]:
    rss_url = f"https://news.google.com/rss?topic={topic}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(rss_url)
    
    news = []
    for entry in feed.entries[:limit]:
        source = entry.get("source", {})
        if isinstance(source, dict):
            source_name = source.get("title", "Unknown")
        else:
            source_name = str(source) if source else "Unknown"
        
        news.append({
            "title": entry.title,
            "source": source_name,
            "channel": f"topic:{topic}",
            "published": entry.get("published", ""),
            "url": entry.link,
            "platform": "google_news"
        })
    
    return news

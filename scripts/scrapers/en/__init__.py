from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from .github import get_github_trending
from .google_news import get_google_news
from .hacker_news import get_hacker_news_trending
from .reddit import get_reddit_trending
from .youtube import get_youtube_trending


SCRAPERS = {
    "reddit": (get_reddit_trending, 50),
    "youtube": (get_youtube_trending, 50),
    "hacker_news": (get_hacker_news_trending, 50),
    "google_news": (get_google_news, 50),
    "github": (get_github_trending, 50),
}


def _run_scraper(name: str, fn, limit: int) -> tuple[str, List[Dict]]:
    try:
        return name, fn(limit=limit)
    except Exception as e:
        print(f"    ✗ {name}: {e}")
        return name, []


def get_all_en_trending() -> List[Dict]:
    all_items = []
    with ThreadPoolExecutor(max_workers=len(SCRAPERS)) as pool:
        futures = {
            pool.submit(_run_scraper, name, fn, limit): name
            for name, (fn, limit) in SCRAPERS.items()
        }
        for future in as_completed(futures):
            name, data = future.result()
            print(f"    ✓ {name}: {len(data)} 条")
            all_items.extend(data)

    return all_items

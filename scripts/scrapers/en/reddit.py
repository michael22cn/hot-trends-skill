import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

REDDIT_FEEDS = [
    ("r/technology", "https://www.reddit.com/r/technology/hot.json"),
    ("r/programming", "https://www.reddit.com/r/programming/hot.json"),
    ("r/artificial", "https://www.reddit.com/r/artificial/hot.json"),
]


def _fetch_reddit_feed(channel: str, url: str, limit: int) -> List[Dict]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    items = []
    resp = requests.get(url, headers=headers, params={'limit': limit}, timeout=10)
    data = resp.json()

    posts = data.get('data', {}).get('children', [])
    for post in posts:
        post_data = post.get('data', {})
        title = post_data.get('title', '')
        subreddit = post_data.get('subreddit', '')
        score = post_data.get('score', 0)
        permalink = post_data.get('permalink', '')

        if title:
            items.append({
                "title": title,
                "subreddit": subreddit,
                "channel": channel,
                "source": f"reddit.com/{channel}",
                "score": score,
                "url": f"https://reddit.com{permalink}",
                "platform": "reddit"
            })

    return items


def get_reddit_trending(limit: int = 20) -> List[Dict]:
    items = []
    per_feed_limit = max(5, min(20, limit // max(1, len(REDDIT_FEEDS)) or 5))

    with ThreadPoolExecutor(max_workers=len(REDDIT_FEEDS)) as pool:
        futures = {
            pool.submit(_fetch_reddit_feed, channel, url, per_feed_limit): channel
            for channel, url in REDDIT_FEEDS
        }
        for future in as_completed(futures):
            channel = futures[future]
            try:
                items.extend(future.result())
            except Exception as e:
                print(f"Reddit error ({channel}): {e}")

    return items

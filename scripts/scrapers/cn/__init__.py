from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from .bilibili import get_bilibili_trending
from .douyin import get_douyin_trending
from .toutiao import get_toutiao_trending
from .weibo import get_weibo_trending


SCRAPERS = {
    "weibo": (get_weibo_trending, 50),
    "toutiao": (get_toutiao_trending, 50),
    "douyin": (get_douyin_trending, 50),
    "bilibili": (get_bilibili_trending, 50),
}


def _run_scraper(name: str, fn, limit: int) -> tuple[str, List[Dict]]:
    try:
        return name, fn(limit=limit)
    except Exception as e:
        print(f"    ✗ {name}: {e}")
        return name, []


def get_all_cn_trending() -> List[Dict]:
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

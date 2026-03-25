from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
from typing import List, Dict


YOUTUBE_SOURCE_SPECS = [
    {
        "channel": "gaming_trending_us",
        "source": "youtube.com/gaming/trending",
        "url": "https://www.youtube.com/gaming/trending?gl=US&hl=en",
    },
    {
        "channel": "technology_news_us",
        "source": "youtube.com/feed/news_destination/technology",
        "url": "https://www.youtube.com/feed/news_destination/technology?gl=US&hl=en",
    },
    {
        "channel": "focus_news_us",
        "source": "youtube.com/channel/UCYfdidRxbB8Qhf0Nx7ioOYw",
        "url": "https://www.youtube.com/channel/UCYfdidRxbB8Qhf0Nx7ioOYw?gl=US&hl=en",
    },
]


def _extract_youtube_items(page, source_channel: str, source_name: str, source_url: str, limit: int) -> List[Dict]:
    videos = []
    seen = set()
    selectors = [
        "ytd-rich-item-renderer",
        "ytd-video-renderer",
        "ytd-grid-video-renderer",
    ]

    for selector in selectors:
        for item in page.query_selector_all(selector):
            title_elem = item.query_selector("#video-title")
            channel_elem = item.query_selector("#channel-name a, ytd-channel-name a")
            meta_elem = item.query_selector("#metadata-line")

            title = " ".join((title_elem.inner_text() if title_elem else "").split()).strip()
            channel_name = " ".join((channel_elem.inner_text() if channel_elem else "").split()).strip() or "Unknown"
            meta = " ".join((meta_elem.inner_text() if meta_elem else "").split()).strip()
            href = (title_elem.get_attribute("href") if title_elem else "") or ""

            if not title or not href:
                continue
            if title in seen:
                continue
            seen.add(title)
            videos.append(
                {
                    "title": title,
                    "channel": source_channel,
                    "source": source_name,
                    "video_channel": channel_name,
                    "meta": meta,
                    "url": href if href.startswith("http") else f"https://youtube.com{href}",
                    "platform": "youtube",
                }
            )
            if len(videos) >= limit:
                return videos

    return videos


def _fetch_youtube_source(spec: Dict[str, str], limit: int) -> List[Dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()

        try:
            page.goto(spec["url"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            return _extract_youtube_items(page, spec["channel"], spec["source"], spec["url"], limit)
        except Exception as e:
            print(f"YouTube error ({spec['channel']}): {e}")
        finally:
            browser.close()

    return []


def get_youtube_trending(region: str = "US", limit: int = 20) -> List[Dict]:
    videos = []
    per_source_limit = max(5, min(20, limit // max(1, len(YOUTUBE_SOURCE_SPECS)) or 5))

    with ThreadPoolExecutor(max_workers=len(YOUTUBE_SOURCE_SPECS)) as pool:
        futures = {
            pool.submit(_fetch_youtube_source, spec, per_source_limit): spec["channel"]
            for spec in YOUTUBE_SOURCE_SPECS
        }
        for future in as_completed(futures):
            source_channel = futures[future]
            try:
                videos.extend(future.result())
            except Exception as e:
                print(f"YouTube error ({source_channel}): {e}")

    return videos

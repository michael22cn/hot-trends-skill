import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
from typing import List, Dict
from urllib.parse import urljoin


def _get_weibo_main_hot(limit: int) -> List[Dict]:
    url = "https://weibo.com/ajax/side/hotSearch"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Cookie': 'SUB=;',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://weibo.com/'
    }
    
    items = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('ok') == 1:
            realtime = data.get('data', {}).get('realtime', [])
            for item in realtime[:limit]:
                word = item.get('word', '')
                label = item.get('label_name', '')
                num = item.get('num', 0)
                
                title = word
                if label:
                    title = f"{word} [{label}]"
                
                items.append({
                    "title": title,
                    "hot": str(num),
                    "channel": "main_hot_search",
                    "source": "weibo.com/ajax/side/hotSearch",
                    "platform": "weibo"
                })
    except Exception as e:
        print(f"Weibo main hot error: {e}")

    return items


def _get_weibo_social_hot(limit: int) -> List[Dict]:
    url = "https://weibo.com/hot/social"
    items = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            selectors = [
                'a[href*="/search?"]',
                'a[href*="/weibo?q="]',
                'a[href*="containerid=231522"]',
            ]
            for selector in selectors:
                for anchor in page.query_selector_all(selector):
                    text = " ".join(anchor.inner_text().split()).strip()
                    href = (anchor.get_attribute("href") or "").strip()
                    if not text or len(text) < 2 or len(text) > 48:
                        continue
                    if text in {"热搜", "社会", "刷新", "更多", "返回", "查看详情"}:
                        continue
                    if text in seen:
                        continue
                    seen.add(text)
                    items.append(
                        {
                            "title": text,
                            "channel": "social_hot",
                            "source": "weibo.com/hot/social",
                            "url": urljoin("https://weibo.com", href) if href else url,
                            "platform": "weibo",
                        }
                    )
                    if len(items) >= limit:
                        return items
        except Exception as e:
            print(f"Weibo social hot error: {e}")
        finally:
            browser.close()

    return items


def get_weibo_trending(limit: int = 20) -> List[Dict]:
    items = []
    tasks = [
        ("main_hot_search", _get_weibo_main_hot, limit),
        ("social_hot", _get_weibo_social_hot, limit),
    ]

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {
            pool.submit(fn, task_limit): channel
            for channel, fn, task_limit in tasks
        }
        for future in as_completed(futures):
            channel = futures[future]
            try:
                items.extend(future.result())
            except Exception as e:
                print(f"Weibo {channel} error: {e}")

    return items

from playwright.sync_api import sync_playwright
from typing import List, Dict

def get_bilibili_trending(limit: int = 20) -> List[Dict]:
    url = "https://www.bilibili.com/v/popular/rank/all"
    
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            
            rank_items = page.query_selector_all(".rank-item")[:limit]
            for item in rank_items:
                title_elem = item.query_selector(".title")
                if title_elem:
                    title = title_elem.inner_text().strip()
                    if title:
                        items.append({
                            "title": title,
                            "channel": "all_rank",
                            "platform": "bilibili"
                        })
        except Exception as e:
            print(f"Bilibili error: {e}")
        finally:
            browser.close()
    
    return items

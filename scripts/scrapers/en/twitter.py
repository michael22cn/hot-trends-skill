import json
import os
from playwright.sync_api import sync_playwright
from typing import List, Dict

DEFAULT_TWITTER_COOKIES: List[Dict] = []


def _load_twitter_cookies() -> List[Dict]:
    raw = os.getenv("HOT_TRENDS_TWITTER_COOKIES", "")
    if not raw:
        return DEFAULT_TWITTER_COOKIES

    try:
        cookies = json.loads(raw)
        if isinstance(cookies, list):
            return cookies
    except json.JSONDecodeError:
        pass

    return DEFAULT_TWITTER_COOKIES

def get_twitter_trending(limit: int = 50) -> List[Dict]:
    items = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        cookies = _load_twitter_cookies()
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        
        try:
            page.goto('https://x.com/explore/tabs/trending', timeout=15000)
            page.wait_for_timeout(2000)
            
            trends = []
            trend_blocks = page.query_selector_all('[data-testid="trend"]')
            
            for block in trend_blocks:
                spans = block.query_selector_all('span')
                for span in spans:
                    text = span.inner_text().strip()
                    if text and len(text) > 2 and not text.isdigit():
                        if 'Promoted' not in text and '趋势' not in text:
                            trends.append(text)
                            break
            
            for trend in trends[:limit]:
                items.append({
                    'name': trend,
                    'channel': 'explore_trending',
                    'source': 'x.com',
                    'platform': 'twitter'
                })
        except Exception as e:
            print(f"Twitter error: {e}")
        finally:
            browser.close()
    
    return items

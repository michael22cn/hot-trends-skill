import requests
from typing import List, Dict

def get_douyin_trending(limit: int = 20) -> List[Dict]:
    url = "https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.douyin.com/',
    }
    
    items = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        word_list = data.get('data', {}).get('word_list', [])
        for item in word_list[:limit]:
            word = item.get('word', '')
            hot_value = item.get('hot_value', 0)
            
            if word:
                items.append({
                    "title": word,
                    "hot": str(hot_value),
                    "channel": "hot_search",
                    "platform": "douyin"
                })
    except Exception as e:
        print(f"Douyin error: {e}")
    
    return items

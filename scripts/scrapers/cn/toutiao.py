import requests
from typing import List, Dict

def get_toutiao_trending(limit: int = 20) -> List[Dict]:
    url = "https://www.toutiao.com/api/pc/feed/?max_behot_time=0&category=__all__"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.toutiao.com/'
    }
    
    items = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('message') == 'success':
            feed_list = data.get('data', [])
            for item in feed_list[:limit]:
                title = item.get('title', '')
                if title:
                    items.append({
                        "title": title,
                        "channel": "__all__",
                        "platform": "toutiao"
                    })
    except Exception as e:
        print(f"Toutiao error: {e}")
    
    return items

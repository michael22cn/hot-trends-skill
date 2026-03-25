import requests
from datetime import date, timedelta
from typing import List, Dict

def get_github_trending(limit: int = 50) -> List[Dict]:
    url = "https://api.github.com/search/repositories"
    
    items = []
    try:
        since = (date.today() - timedelta(days=7)).isoformat()
        params = {
            'q': f'created:>{since}',
            'sort': 'stars',
            'order': 'desc',
            'per_page': min(limit, 100)
        }
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        
        repos = data.get('items', [])
        for repo in repos[:limit]:
            items.append({
                'name': repo.get('full_name', ''),
                'description': repo.get('description', ''),
                'stars': repo.get('stargazers_count', 0),
                'language': repo.get('language', ''),
                'channel': 'weekly_new_repos',
                'source': 'api.github.com/search/repositories',
                'url': repo.get('html_url', ''),
                'platform': 'github'
            })
    except Exception as e:
        print(f"GitHub error: {e}")
    
    return items

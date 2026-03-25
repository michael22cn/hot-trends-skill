import requests
from bs4 import BeautifulSoup
from typing import Dict, List


def get_yahoo_finance_trending(limit: int = 20) -> List[Dict]:
    url = "https://finance.yahoo.com/trending-tickers/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    items = []
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        rows = soup.select("table tbody tr")
        for row in rows[:limit]:
            cells = row.find_all("td")
            link = row.select_one('a[href*="/quote/"]')
            if not cells or not link:
                continue

            symbol = " ".join(cells[0].get_text(" ", strip=True).split()) if len(cells) > 0 else ""
            name = " ".join(cells[1].get_text(" ", strip=True).split()) if len(cells) > 1 else ""
            price = " ".join(cells[2].get_text(" ", strip=True).split()) if len(cells) > 2 else ""
            change = " ".join(cells[3].get_text(" ", strip=True).split()) if len(cells) > 3 else ""
            change_pct = " ".join(cells[4].get_text(" ", strip=True).split()) if len(cells) > 4 else ""
            volume = " ".join(cells[5].get_text(" ", strip=True).split()) if len(cells) > 5 else ""

            if not symbol:
                continue

            items.append(
                {
                    "title": f"{symbol} {name}".strip(),
                    "symbol": symbol,
                    "channel": "trending_tickers",
                    "source": "finance.yahoo.com/trending-tickers",
                    "price": price,
                    "change": change,
                    "change_percent": change_pct,
                    "volume": volume,
                    "url": f"https://finance.yahoo.com{link.get('href', '')}",
                    "platform": "yahoo_finance",
                }
            )
    except Exception as e:
        print(f"Yahoo Finance error: {e}")

    return items

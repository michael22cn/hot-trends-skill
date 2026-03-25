import os
import tempfile
from typing import Dict, List

from playwright.sync_api import BrowserContext, Page, TimeoutError, sync_playwright


PERPLEXITY_DISCOVER_URL = "https://www.perplexity.ai/discover/yo"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _extract_discover_cards(page: Page, limit: int) -> List[Dict]:
    return page.evaluate(
        """({ limit }) => {
            const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\\s+/g, " ").trim();
            const absUrl = (href) => {
              if (!href) return "";
              try { return new URL(href, "https://www.perplexity.ai").toString(); } catch { return ""; }
            };

            const candidates = [];
            const seen = new Set();
            const blocks = Array.from(document.querySelectorAll("main article, main section, main div"));

            for (const block of blocks) {
              const anchor = block.querySelector('a[href*="/discover/"], a[href*="/page/"], a[href^="/search"], a[href^="/p/"]');
              const heading = block.querySelector("h1, h2, h3, h4, strong");
              const title = textOf(heading) || textOf(anchor);
              const href = absUrl(anchor?.getAttribute("href") || "");
              const summaryNode = Array.from(block.querySelectorAll("p, span, div"))
                .map((el) => textOf(el))
                .find((text) => text && text !== title && text.length > 20);
              const summary = (summaryNode || "").slice(0, 280);

              if (!title || title.length < 8 || title.length > 180) continue;
              if (["Discover", "Home", "Library", "Spaces", "Sign In", "Get Pro"].includes(title)) continue;
              if (seen.has(title)) continue;

              seen.add(title);
              candidates.push({ title, url: href, summary });
              if (candidates.length >= limit) break;
            }

            return candidates;
        }""",
        {"limit": limit},
    )


def _build_context(playwright, user_data_dir: str) -> BrowserContext:
    headless = _env_flag("HOT_TRENDS_PERPLEXITY_HEADLESS", False)
    slow_mo = 150 if not headless else 0

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=headless,
        slow_mo=slow_mo,
        user_agent=DEFAULT_USER_AGENT,
        viewport={"width": 1440, "height": 1080},
        locale="en-US",
        timezone_id="America/Los_Angeles",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    )
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
        """
    )
    return context


def _wait_for_discover_ready(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)

    ready_selectors = [
        'main a[href*="/discover/"]',
        'main a[href*="/page/"]',
        'main a[href^="/search"]',
        "main article",
        "main h1, main h2, main h3",
    ]

    last_error = None
    for selector in ready_selectors:
        try:
            page.locator(selector).first.wait_for(timeout=8000)
            return
        except TimeoutError as exc:
            last_error = exc

    if last_error:
        raise last_error


def _progressive_extract(page: Page, limit: int) -> List[Dict]:
    collected: Dict[str, Dict] = {}
    stagnant_rounds = 0

    for round_index in range(10):
        before_count = len(collected)
        for entry in _extract_discover_cards(page, limit):
            title = entry.get("title", "").strip()
            if not title:
                continue
            collected.setdefault(title, entry)

        if len(collected) >= limit:
            break

        if len(collected) == before_count:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0

        if stagnant_rounds >= 3:
            break

        scroll_distance = 1200 if round_index < 4 else 1800
        page.mouse.wheel(0, scroll_distance)
        page.wait_for_timeout(2200)

    return list(collected.values())[:limit]


def get_perplexity_discover(limit: int = 20) -> List[Dict]:
    items = []

    with sync_playwright() as p:
        with tempfile.TemporaryDirectory(prefix="hot-trends-perplexity-") as user_data_dir:
            context = _build_context(p, user_data_dir)
            page = context.pages[0] if context.pages else context.new_page()

            try:
                attempts = 3
                extracted: List[Dict] = []

                for attempt in range(1, attempts + 1):
                    page.goto(PERPLEXITY_DISCOVER_URL, wait_until="domcontentloaded", timeout=30000)
                    _wait_for_discover_ready(page)
                    extracted = _progressive_extract(page, limit)
                    if extracted:
                        if len(extracted) >= min(limit, 10):
                            break
                    if attempt < attempts:
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3500)

                for entry in extracted:
                    items.append(
                        {
                            "title": entry.get("title", ""),
                            "summary": entry.get("summary", ""),
                            "channel": "discover",
                            "source": "perplexity.ai/discover",
                            "url": entry.get("url", "") or PERPLEXITY_DISCOVER_URL,
                            "platform": "perplexity_discover",
                        }
                    )
            except Exception as e:
                print(f"Perplexity Discover error: {e}")
            finally:
                context.close()

    return items

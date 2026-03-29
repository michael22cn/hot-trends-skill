import json
import os
from typing import Dict, List

from playwright.sync_api import BrowserContext, Page, TimeoutError, sync_playwright


# Same URL as the browser tool: /discover (headlines tab)
PERPLEXITY_DISCOVER_URL = "https://www.perplexity.ai/discover"
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


def _dismiss_login_modal(page: Page) -> None:
    """Close login / sign-up modal if it appears, same as the browser tool does."""
    modal_close_selectors = [
        "button[aria-label*='close' i]",
        "button[data-testid='close-btn']",
        "[role='dialog'] button",
        "[data-testid='modal'] button",
        "button:has-text('Continue with Google')",
        "button:has-text('Continue with Apple')",
        "button:has-text('Sign In')",
        "button:has-text('Sign up')",
        "button:has-text('登录')",
        "button:has-text('注册')",
    ]
    for selector in modal_close_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click(timeout=2000)
                page.wait_for_timeout(800)
                return
        except TimeoutError:
            continue


# ----------------------------------------------------------------------
# JavaScript extraction logic — built as a json.dumps string so all
# backslashes are preserved correctly when passed to page.evaluate().
# ----------------------------------------------------------------------
_EXTRACT_JS = r"""
function(limit) {
    var textOf = function(el) {
        return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    };
    var absUrl = function(href) {
        if (!href) return '';
        try { return new URL(href, 'https://www.perplexity.ai').toString(); } catch(e) { return ''; }
    };

    var candidates = [];
    var seen = {};
    var links = Array.from(document.querySelectorAll('main a[href*="/discover/"]'));

    for (var i = 0; i < links.length; i++) {
        var anchor = links[i];
        var raw = textOf(anchor);
        // innerText is "Title\nmetadata1\nmetadata2\n..." — split and take first line as title
        var lines = raw.split('\n');
        var title = lines[0].trim();
        var href = absUrl(anchor.getAttribute('href') || '');

        if (!title || title.length < 6 || title.length > 200) continue;
        var skipTitles = ['Discover','Home','Library','Spaces','Sign In','Get Pro','搜索','历史','发现','主题'];
        if (skipTitles.indexOf(title) !== -1) continue;
        if (seen[title]) continue;
        seen[title] = true;

        // Second line is typically the source count or timestamp — use it as summary
        var secondLine = (lines.length > 1) ? lines[1].trim() : '';
        var summary = secondLine;

        candidates.push({ title: title, url: href, summary: summary });
        if (candidates.length >= limit) break;
    }

    // Fallback: scan article / section blocks
    if (candidates.length < 5) {
        var blocks = Array.from(document.querySelectorAll('main article, main section'));
        for (var j = 0; j < blocks.length; j++) {
            var block = blocks[j];
            var a = block.querySelector('a[href*="/discover/"]');
            if (!a) continue;
            var t = textOf(a) || textOf(block.querySelector('h1,h2,h3,h4'));
            var h = absUrl(a.getAttribute('href') || '');
            if (!t || t.length < 6 || seen[t]) continue;
            seen[t] = true;
            candidates.push({ title: t, url: h, summary: '' });
            if (candidates.length >= limit) break;
        }
    }

    return candidates;
}
"""


def _extract_discover_cards(page: Page, limit: int) -> List[Dict]:
    """Extract news cards from the discover page — same logic as browser tool snapshot."""
    return page.evaluate(json.loads(json.dumps(_EXTRACT_JS)), {"limit": limit})


def _wait_for_discover_ready(page: Page) -> None:
    """Wait for the discover page to be fully rendered."""
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except TimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)

    indicators = [
        'main a[href*="/discover/"]',
        "main article",
        "main [role='tablist']",
    ]
    for sel in indicators:
        try:
            page.locator(sel).first.wait_for(timeout=5000, state="attached")
            return
        except TimeoutError:
            continue


def _progressive_extract(page: Page, limit: int) -> List[Dict]:
    """Scroll and extract progressively, same technique as the browser tool."""
    collected: Dict[str, Dict] = {}
    stagnant = 0

    for _round in range(10):
        before = len(collected)
        _dismiss_login_modal(page)

        for entry in _extract_discover_cards(page, limit):
            title = entry.get("title", "").strip()
            if not title:
                continue
            collected.setdefault(title, entry)

        if len(collected) >= limit:
            break
        if len(collected) == before:
            stagnant += 1
        else:
            stagnant = 0
        if stagnant >= 3:
            break

        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1800)

    return list(collected.values())[:limit]


def get_perplexity_discover(limit: int = 20) -> List[Dict]:
    """Fetch Perplexity Discover headlines using a visible browser (same as browser tool).

    Set HOT_TRENDS_PERPLEXITY_HEADLESS=1 to switch back to headless mode.
    """
    items: List[Dict] = []

    with sync_playwright() as p:
        # Always open visible browser — matches how the browser tool works
        headless = _env_flag("HOT_TRENDS_PERPLEXITY_HEADLESS", False)  # browser tool = visible

        # Always visible browser (same as browser tool) unless explicitly opt into headless
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="",
            headless=headless,
            slow_mo=50 if not headless else 0,
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1440, "height": 1080},
            locale="en-US",
            timezone_id="America/Los_Angeles",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            page.goto(PERPLEXITY_DISCOVER_URL, wait_until="load", timeout=30000)
            _wait_for_discover_ready(page)
            _dismiss_login_modal(page)
            page.wait_for_timeout(1000)

            extracted: List[Dict] = []
            for attempt in range(1, 4):
                _dismiss_login_modal(page)
                extracted = _progressive_extract(page, limit)
                if len(extracted) >= min(limit, 8):
                    break
                if attempt < 3:
                    page.reload(wait_until="load", timeout=30000)
                    page.wait_for_timeout(3000)
                    _dismiss_login_modal(page)

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
            ctx.close()

    return items

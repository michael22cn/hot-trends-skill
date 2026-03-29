#!/usr/bin/env python3
"""最小用例：测试无头浏览器能否获取 Perplexity/Discover 新闻数据"""

import sys
import os
import tempfile
import time

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

PERPLEXITY_DISCOVER_URL = "https://www.perplexity.ai/discover/yo"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def extract_cards(page, limit=20):
    """提取发现页面的卡片数据"""
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


def wait_for_ready(page):
    """等待页面加载完成"""
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)

    ready_selectors = [
        'main a[href*="/discover/"]',
        'main a[href*="/page/"]',
        'main a[href^="/search"]',
        "main article",
        "main h1, main h2, main h3",
    ]

    for selector in ready_selectors:
        try:
            page.locator(selector).first.wait_for(timeout=8000)
            print(f"  ✅ 页面就绪（选择器: {selector}）")
            return True
        except PlaywrightTimeout:
            continue

    print("  ⚠️ 未找到预期的选择器")
    return False


def test_perplexity_headless():
    """测试无头浏览器模式"""
    print("\n" + "="*60)
    print("🧪 最小用例测试：无头浏览器获取 Perplexity/Discover")
    print("="*60)

    results = []

    with sync_playwright() as p:
        with tempfile.TemporaryDirectory(prefix="test-perplexity-") as user_data_dir:
            print(f"\n📍 测试地址: {PERPLEXITY_DISCOVER_URL}")
            print(f"🖥️  模式: HEADLESS (无头)")

            # 启动无头浏览器
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=True,  # 关键：无头模式
                slow_mo=0,
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1440, "height": 1080},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )

            # 注入脚本隐藏自动化特征
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
                """
            )

            page = context.pages[0] if context.pages else context.new_page()

            try:
                # 访问页面
                print(f"\n🌐 正在访问页面...")
                start_time = time.time()

                try:
                    page.goto(PERPLEXITY_DISCOVER_URL, wait_until="domcontentloaded", timeout=45000)
                    elapsed = time.time() - start_time
                    print(f"  ✅ 页面加载成功 ({elapsed:.1f}秒)")
                except PlaywrightTimeout as e:
                    elapsed = time.time() - start_time
                    print(f"  ❌ 页面加载超时 ({elapsed:.1f}秒): {e}")
                    return False

                # 等待就绪
                print(f"\n⏳ 等待页面就绪...")
                if not wait_for_ready(page):
                    # 截图调试
                    page.screenshot(path="/tmp/perplexity-debug.png")
                    print(f"  📸 已保存截图到 /tmp/perplexity-debug.png")

                    # 打印页面标题
                    title = page.title()
                    print(f"  📄 页面标题: {title}")

                    # 打印 body 文本片段
                    body_text = page.evaluate("() => document.body?.innerText?.slice(0, 500)")
                    print(f"  📝 Body 文本预览:\n{body_text}")
                    return False

                # 滚动提取数据
                print(f"\n📥 开始提取数据...")
                collected = {}
                stagnant_rounds = 0

                for round_index in range(5):
                    before_count = len(collected)

                    cards = extract_cards(page, limit=20)
                    for card in cards:
                        title = card.get("title", "").strip()
                        if title:
                            collected.setdefault(title, card)

                    print(f"  轮次 {round_index + 1}: 获取 {len(cards)} 条, 累计 {len(collected)} 条")

                    if len(collected) >= 20:
                        break

                    if len(collected) == before_count:
                        stagnant_rounds += 1
                    else:
                        stagnant_rounds = 0

                    if stagnant_rounds >= 2:
                        print(f"  ⚠️ 数据停止增长，停止滚动")
                        break

                    # 滚动
                    scroll_distance = 1200
                    page.mouse.wheel(0, scroll_distance)
                    page.wait_for_timeout(2000)

                results = list(collected.values())[:20]

                print(f"\n" + "="*60)
                print(f"📊 提取结果: {len(results)} 条")
                print("="*60)

                if results:
                    for i, item in enumerate(results[:5], 1):
                        print(f"\n{i}. {item['title'][:60]}...")
                        print(f"   URL: {item['url'][:80] if item['url'] else '(无)'}")
                        if item.get('summary'):
                            print(f"   摘要: {item['summary'][:80]}...")
                    if len(results) > 5:
                        print(f"\n... 还有 {len(results) - 5} 条")
                else:
                    print("\n❌ 未能提取到任何数据")

                return len(results) > 0

            except Exception as e:
                print(f"\n💥 发生错误: {e}")
                import traceback
                traceback.print_exc()
                return False

            finally:
                context.close()

    return len(results) > 0


if __name__ == "__main__":
    success = test_perplexity_headless()
    print("\n" + "="*60)
    if success:
        print("✅ 测试通过：无头浏览器可以获取 Perplexity/Discover 数据")
    else:
        print("❌ 测试失败")
    print("="*60)
    sys.exit(0 if success else 1)

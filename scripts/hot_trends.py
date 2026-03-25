#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.scrapers.cn.bilibili import get_bilibili_trending
from scripts.scrapers.cn.douyin import get_douyin_trending
from scripts.scrapers.cn.toutiao import get_toutiao_trending
from scripts.scrapers.cn.weibo import get_weibo_trending
from scripts.scrapers.en.github import get_github_trending
from scripts.scrapers.en.gamesradar import get_gamesradar_trending
from scripts.scrapers.en.google_news import get_google_news
from scripts.scrapers.en.hacker_news import get_hacker_news_trending
from scripts.scrapers.en.perplexity_discover import get_perplexity_discover
from scripts.scrapers.en.reddit import get_reddit_trending
from scripts.scrapers.en.techmeme import get_techmeme_trending
from scripts.scrapers.en.yahoo_finance import get_yahoo_finance_trending
from scripts.scrapers.en.youtube import get_youtube_trending
from scripts.lib.aggregator import aggregate_topics
from scripts.lib.renderer import render_briefing, render_briefing_email_html, render_briefing_html

SINGLE_CHANNELS = {
    "bilibili": ("bilibili", get_bilibili_trending),
    "douyin": ("douyin", get_douyin_trending),
    "toutiao": ("toutiao", get_toutiao_trending),
    "weibo": ("weibo", get_weibo_trending),
    "github": ("github", get_github_trending),
    "gamesradar": ("gamesradar", get_gamesradar_trending),
    "google_news": ("google_news", get_google_news),
    "hacker_news": ("hacker_news", get_hacker_news_trending),
    "perplexity_discover": ("perplexity_discover", get_perplexity_discover),
    "reddit": ("reddit", get_reddit_trending),
    "techmeme": ("techmeme", get_techmeme_trending),
    "youtube": ("youtube", get_youtube_trending),
    "yahoo_finance": ("yahoo_finance", get_yahoo_finance_trending),
}

ALL_CHANNELS = SINGLE_CHANNELS
DEFAULT_EMAIL_TO = "674080@qq.com"


def log(stage, message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def send_email_via_gog(subject, body, html_body="", attachments=None, to_address=DEFAULT_EMAIL_TO):
    attachments = attachments or []
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as f:
        f.write(body)
        body_file = f.name

    cmd = [
        "gog",
        "gmail",
        "send",
        "--to",
        to_address,
        "--subject",
        subject,
        "--body-file",
        body_file,
    ]
    if html_body:
        cmd.extend(["--body-html", html_body])
    for attachment in attachments:
        if attachment:
            cmd.extend(["--attach", attachment])

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass


def fetch_group(name, fn):
    log("FETCH", f"{name} started")
    try:
        items = fn(limit=50)
        log("FETCH", f"{name} completed with {len(items)} items")
        return name, items
    except Exception as e:
        log("FETCH", f"{name} failed: {e}")
        return name, []


def main():
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="全球热点简报生成器")
    parser.add_argument("--topics-only", action="store_true")
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--html", action="store_true", help="输出 HTML 页面")
    parser.add_argument(
        "--channel",
        choices=sorted(SINGLE_CHANNELS.keys()),
        help="只运行单个 channel 的最小链路验证",
    )
    args = parser.parse_args()

    if args.channel:
        platform_name, fn = SINGLE_CHANNELS[args.channel]
        log("RUN", f"start single-channel mode: {args.channel}")
        all_items = fn(limit=50)
        log("FETCH", f"{platform_name} returned {len(all_items)} items")
        failed_platforms = [] if all_items else [platform_name]
    else:
        log("RUN", f"start global fetch with {len(ALL_CHANNELS)} parallel channels")

        all_items = []
        with ThreadPoolExecutor(max_workers=len(ALL_CHANNELS)) as pool:
            futures = {
                pool.submit(fetch_group, name, fn): name
                for name, (_, fn) in ALL_CHANNELS.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    _, items = future.result(timeout=90)
                    all_items.extend(items)
                    log("FETCH", f"{name} joined into result set with {len(items)} items")
                except Exception as e:
                    log("FETCH", f"{name} future failed: {e}")

    plat_counts = Counter(item["platform"] for item in all_items)
    log("FETCH", f"total collected items: {len(all_items)}")

    if not args.channel:
        all_platforms = set(name for name, _ in ALL_CHANNELS.values())
        failed_platforms = []
        for plat, count in plat_counts.items():
            log("FETCH", f"platform summary {plat}={count}")
            if count == 0:
                failed_platforms.append(plat)

        for name in all_platforms:
            plat_name = name
            if plat_counts.get(plat_name, 0) == 0 and plat_name not in failed_platforms:
                failed_platforms.append(plat_name)

        if failed_platforms:
            log("FETCH", f"platforms with no data: {', '.join(failed_platforms)}")
            all_items = [item for item in all_items if item["platform"] not in failed_platforms]

    if not all_items:
        log("RUN", "no valid items after fetch filtering")
        sys.exit(1)

    log("RUN", f"start NotebookLM aggregation with {len(all_items)} items")
    try:
        topics_data = aggregate_topics(all_items, failed_platforms if not args.channel else [])
    except Exception as e:
        log("RUN", f"NotebookLM aggregation failed: {e}")
        sys.exit(2)

    email_subject = topics_data.get("title") or f"全球热点简报 {datetime.now().strftime('%Y-%m-%d')}"
    email_body = render_briefing(topics_data)
    email_html_body = render_briefing_email_html(topics_data)
    attachments = []
    infographic_path = (topics_data.get("infographic_path") or "").strip()
    if infographic_path:
        attachments.append(infographic_path)
        log("OUTPUT", f"infographic attachment queued: {infographic_path}")

    if args.html:
        log("OUTPUT", "rendering HTML briefing")
        html_output = render_briefing_html(topics_data)
        out_path = os.path.expanduser(args.output) if args.output else "/tmp/hot-trends-daily.html"
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_output)
        log("OUTPUT", f"html briefing saved: {out_path}")
        attachments.append(out_path)
    else:
        log("OUTPUT", "rendering markdown briefing")
        if args.output:
            os.makedirs(os.path.dirname(os.path.expanduser(args.output)) or ".", exist_ok=True)
            with open(os.path.expanduser(args.output), "w", encoding="utf-8") as f:
                f.write(email_body)
            log("OUTPUT", f"markdown briefing saved: {os.path.expanduser(args.output)}")
            attachments.append(os.path.expanduser(args.output))
        else:
            log("OUTPUT", "printing markdown briefing to stdout")
            print("\n" + email_body)

    email_to = os.getenv("HOT_TRENDS_EMAIL_TO", DEFAULT_EMAIL_TO).strip() or DEFAULT_EMAIL_TO
    log("EMAIL", f"start sending email via gog to {email_to}")
    email_proc = send_email_via_gog(
        email_subject,
        email_body,
        html_body=email_html_body,
        attachments=attachments,
        to_address=email_to,
    )
    if email_proc.returncode != 0:
        log("EMAIL", f"email send failed: {email_proc.stderr.strip() or email_proc.stdout.strip()}")
        sys.exit(3)
    log("EMAIL", f"email sent successfully to {email_to}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

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


def write_artifacts_for_channel(text_path, media_path, run_dir):
    """将投递产物路径写入 last_run_artifacts.json，供 openclaw announce 机制拾取"""
    import json
    artifacts = {
        "status": "ok",
        "run_dir": str(run_dir),
        "channel_text_path": str(text_path),
        "channel_media_path": str(media_path) if media_path else None,
    }
    artifacts_path = Path(run_dir) / "last_run_artifacts.json"
    artifacts_path.parent.mkdir(parents=True, exist_ok=True)
    with artifacts_path.open("w", encoding="utf-8") as f:
        json.dump(artifacts, f, indent=2, ensure_ascii=False)
    log("OUTPUT", f"artifacts written to {artifacts_path}")
    # 同时写到固定位置供 announce 拾取
    stable_path = Path.home() / ".openclaw" / "workspace" / "outbound_media" / "hot_trends_artifacts.json"
    stable_path.parent.mkdir(parents=True, exist_ok=True)
    with stable_path.open("w", encoding="utf-8") as f:
        json.dump(artifacts, f, indent=2, ensure_ascii=False)
    log("OUTPUT", f"stable artifacts written to {stable_path}")


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


def _load_feishu_config():
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    feishu = ((cfg.get("channels") or {}).get("feishu") or {})
    app_id = (feishu.get("appId") or "").strip()
    app_secret = (feishu.get("appSecret") or "").strip()
    domain = (feishu.get("domain") or "open.feishu.cn").strip()
    if domain in {"feishu", "lark", "openclaw"} or "." not in domain:
        domain = "open.feishu.cn"
    if not app_id or not app_secret:
        raise RuntimeError("Feishu appId/appSecret not configured in ~/.openclaw/openclaw.json")
    return {"app_id": app_id, "app_secret": app_secret, "domain": domain}


def _normalize_feishu_chat_target(target: str) -> str:
    raw = (target or "").strip()
    if raw.startswith("chat:"):
        return raw.split(":", 1)[1].strip()
    return raw


def _feishu_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def _get_feishu_tenant_access_token(cfg: dict) -> str:
    url = f"https://{cfg['domain']}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu token failed: {data.get('msg') or data}")
    return data["tenant_access_token"]


def _send_feishu_text(token: str, domain: str, chat_id: str, text: str):
    url = f"https://{domain}/open-apis/im/v1/messages"
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = requests.post(url, params={"receive_id_type": "chat_id"}, headers={**_feishu_headers(token), "Content-Type": "application/json; charset=utf-8"}, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu text send failed: {data.get('msg') or data}")
    return data


def _upload_feishu_image(token: str, domain: str, image_path: str) -> str:
    url = f"https://{domain}/open-apis/im/v1/images"
    with open(image_path, "rb") as f:
        files = {"image": (Path(image_path).name, f, "image/png")}
        data = {"image_type": "message"}
        resp = requests.post(url, headers=_feishu_headers(token), data=data, files=files, timeout=180)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu image upload failed: {result.get('msg') or result}")
    return result["data"]["image_key"]


def _send_feishu_image(token: str, domain: str, chat_id: str, image_key: str):
    url = f"https://{domain}/open-apis/im/v1/messages"
    payload = {
        "receive_id": chat_id,
        "msg_type": "image",
        "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
    }
    resp = requests.post(url, params={"receive_id_type": "chat_id"}, headers={**_feishu_headers(token), "Content-Type": "application/json; charset=utf-8"}, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu image send failed: {data.get('msg') or data}")
    return data


def send_feishu_via_openclaw(text, media_path=None, target=None):
    """Send text + image directly via Feishu Open API.

    Why direct API instead of `openclaw message send`:
    - Feishu text sends work via CLI
    - but CLI send currently ignores/blocks media in the generic send action path,
      causing only the caption text to appear in chat.
    """
    raw_target = (target or os.getenv("HOT_TRENDS_FEISHU_TARGET", "")).strip()
    if not raw_target:
        log("FEISHU", "skip send: HOT_TRENDS_FEISHU_TARGET is empty")
        return True

    chat_id = _normalize_feishu_chat_target(raw_target)
    cfg = _load_feishu_config()
    token = _get_feishu_tenant_access_token(cfg)

    _send_feishu_text(token, cfg["domain"], chat_id, text)
    log("FEISHU", f"text sent to chat:{chat_id}")

    if media_path:
        image_key = _upload_feishu_image(token, cfg["domain"], media_path)
        _send_feishu_image(token, cfg["domain"], chat_id, image_key)
        log("FEISHU", f"media sent to chat:{chat_id}: {media_path}")

    return True


def fetch_group(name, fn):
    log("FETCH", f"{name} started")
    try:
        items = fn(limit=50)
        log("FETCH", f"{name} completed with {len(items)} items")
        return name, items
    except Exception as e:
        log("FETCH", f"{name} failed: {e}")
        return name, []


RETRY_STATE_FILE = Path.home() / ".openclaw" / "workspace" / "hot_trends_retry_state.json"
MAX_RETRY_HOURS = 3  # 最多尝试3次（00:00 / 01:00 / 02:00）


def _load_retry_state():
    """读取重试状态。返回 {'date': str, 'attempt': int, 'succeeded': bool}"""
    today = datetime.now().strftime("%Y-%m-%d")
    default = {"date": today, "attempt": 0, "succeeded": False}
    if not RETRY_STATE_FILE.exists():
        return default
    try:
        with RETRY_STATE_FILE.open() as f:
            state = json.load(f)
        # 新的一天，重置状态
        if state.get("date") != today:
            return default
        return state
    except Exception:
        return default


def _save_retry_state(state):
    RETRY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RETRY_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _check_and_increment_retry():
    """检查是否应该继续运行。返回 True=继续，False=跳过。"""
    state = _load_retry_state()
    today = datetime.now().strftime("%Y-%m-%d")

    if state["succeeded"]:
        log("RETRY", f"[{today}] 今日简报已发送成功，跳过")
        return False
    if state["attempt"] >= MAX_RETRY_HOURS:
        log("RETRY", f"[{today}] 已达最大重试次数（{MAX_RETRY_HOURS}次），跳过")
        return False

    state["attempt"] += 1
    _save_retry_state(state)
    log("RETRY", f"[{today}] 第 {state['attempt']}/{MAX_RETRY_HOURS} 次尝试")
    return True


def _mark_succeeded():
    state = _load_retry_state()
    state["succeeded"] = True
    _save_retry_state(state)
    log("RETRY", f"标记今日简报为已发送成功")


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
    parser.add_argument(
        "--feishu-target",
        help="飞书群目标 ID（已被废弃，保留仅防报错）",
    )
    parser.add_argument(
        "--run-dir",
        help="运行目录，用于存放产物文件（供 openclaw announce 拾取）",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="跳过重试检查，直接运行（用于手动单次触发）",
    )
    args = parser.parse_args()

    # 重试门槛检查（单 channel 调试模式跳过）
    if not args.channel and not args.no_retry:
        if not _check_and_increment_retry():
            return

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

    # ---- 写入 artifacts（供 openclaw announce 拾取）----
    run_dir = args.run_dir or os.getenv("HOT_TRENDS_RUN_DIR", "").strip() or tempfile.mkdtemp(prefix="hot-trends-")
    text_path = Path(run_dir) / "briefing.md"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    with text_path.open("w", encoding="utf-8") as f:
        f.write(email_body)
    log("OUTPUT", f"briefing text saved to {text_path}")

    # Stage infographic to outbound_media for announce拾取
    staged_media = None
    if infographic_path and os.path.isfile(infographic_path):
        outbound_dir = Path.home() / ".openclaw" / "workspace" / "outbound_media"
        outbound_dir.mkdir(parents=True, exist_ok=True)
        staged_media = outbound_dir / "hot_trends_infographic.png"
        shutil.copy2(infographic_path, staged_media)
        log("OUTPUT", f"infographic staged to {staged_media}")

    write_artifacts_for_channel(text_path, staged_media, run_dir)
    # ---- artifacts 写入完成 ----

    # 直接发送到飞书（修复：不再依赖不存在的 announce 自动拾取流程）
    feishu_target = (os.getenv("HOT_TRENDS_FEISHU_TARGET", "") or args.feishu_target or "").strip()
    if feishu_target:
        log("FEISHU", f"start sending to {feishu_target}")
        feishu_ok = send_feishu_via_openclaw(email_body, media_path=str(staged_media) if staged_media else None, target=feishu_target)
        if not feishu_ok:
            log("FEISHU", "send failed")
        else:
            log("FEISHU", "send completed")
            _mark_succeeded()
    else:
        log("FEISHU", "skip send: no HOT_TRENDS_FEISHU_TARGET provided")

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
        # 邮件失败不影响主流程（飞书已优先发送）
    else:
        log("EMAIL", f"email sent successfully to {email_to}")


if __name__ == "__main__":
    main()

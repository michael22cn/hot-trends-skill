#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import time
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
SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_CONFIG_PATH = SKILL_DIR / "config.json"
PUSH_TARGETS_PATH = SKILL_DIR / "config" / "push_targets.json"
HERMES_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"
HERMES_STATE_DIR = Path.home() / ".hermes" / "state" / "hot-trends-brief"
HERMES_OUTBOUND_DIR = Path.home() / ".hermes" / "workspace" / "outbound_media"


def log(stage, message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def write_artifacts_for_channel(text_path, media_path, run_dir, audio_path=None):
    """将投递产物路径写入 last_run_artifacts.json，并同步到 Hermes 稳定路径。"""
    import json
    artifacts = {
        "status": "ok",
        "run_dir": str(run_dir),
        "channel_text_path": str(text_path),
        "channel_media_path": str(media_path) if media_path else None,
        "channel_audio_path": str(audio_path) if audio_path else None,
    }
    artifacts_path = Path(run_dir) / "last_run_artifacts.json"
    artifacts_path.parent.mkdir(parents=True, exist_ok=True)
    with artifacts_path.open("w", encoding="utf-8") as f:
        json.dump(artifacts, f, indent=2, ensure_ascii=False)
    log("OUTPUT", f"artifacts written to {artifacts_path}")

    stable_path = HERMES_OUTBOUND_DIR / "hot_trends_artifacts.json"
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

    env = os.environ.copy()
    gog_account = os.getenv("GOG_ACCOUNT") or os.getenv("HOT_TRENDS_GOG_ACCOUNT")
    if gog_account:
        env["GOG_ACCOUNT"] = gog_account
    gog_keyring = os.getenv("GOG_KEYRING_PASSWORD") or os.getenv("HOT_TRENDS_GOG_KEYRING_PASSWORD")
    if gog_keyring:
        env["GOG_KEYRING_PASSWORD"] = gog_keyring

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass


def _load_skill_config() -> dict:
    if not SKILL_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(SKILL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log("CONFIG", f"failed to parse {SKILL_CONFIG_PATH}: {e}")
        return {}


def _load_push_targets() -> dict:
    """Load push_targets.json (channels config). Returns {'channels': [...]} or empty dict."""
    if not PUSH_TARGETS_PATH.exists():
        return {}
    try:
        return json.loads(PUSH_TARGETS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log("CONFIG", f"failed to parse {PUSH_TARGETS_PATH}: {e}")
        return {}


def _load_feishu_config():
    if not HERMES_CONFIG_PATH.exists():
        raise RuntimeError(f"Hermes config not found: {HERMES_CONFIG_PATH}")

    app_id = ""
    app_secret = ""
    domain = "open.feishu.cn"
    section = []

    for raw_line in HERMES_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))

        while section and indent <= section[-1][1]:
            section.pop()

        if stripped.endswith(":"):
            key = stripped[:-1].strip()
            section.append((key, indent))
            continue

        if ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        path = [name for name, _ in section] + [key]

        if path == ["platforms", "feishu", "extra", "app_id"]:
            app_id = value
        elif path == ["platforms", "feishu", "extra", "app_secret"]:
            app_secret = value
        elif path == ["platforms", "feishu", "extra", "domain"]:
            domain = value or domain

    if domain in {"feishu", "lark", "openclaw"} or "." not in domain:
        domain = "open.feishu.cn"
    if not app_id or not app_secret:
        raise RuntimeError(f"Feishu app_id/app_secret not configured in {HERMES_CONFIG_PATH}")
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
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as f:
        files = {"image": (Path(image_path).name, f, mime)}
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


def _upload_feishu_file(token: str, domain: str, file_path: str) -> str:
    url = f"https://{domain}/open-apis/im/v1/files"
    file_name = Path(file_path).name
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        files = {"file": (file_name, f, mime)}
        data = {"file_type": "stream", "file_name": file_name}
        resp = requests.post(url, headers=_feishu_headers(token), data=data, files=files, timeout=300)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu file upload failed: {result.get('msg') or result}")
    return result["data"]["file_key"]


def _send_feishu_file(token: str, domain: str, chat_id: str, file_key: str, file_name: str = ""):
    url = f"https://{domain}/open-apis/im/v1/messages"
    payload = {
        "receive_id": chat_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key, "file_name": file_name}, ensure_ascii=False),
    }
    resp = requests.post(url, params={"receive_id_type": "chat_id"}, headers={**_feishu_headers(token), "Content-Type": "application/json; charset=utf-8"}, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu file send failed: {data.get('msg') or data}")
    return data


def send_feishu_direct(text, media_path=None, audio_path=None, target=None):
    """Send text + image + optional audio file directly via Feishu Open API."""
    skill_cfg = _load_skill_config()
    raw_target = (
        target
        or os.getenv("HOT_TRENDS_FEISHU_TARGET", "")
        or skill_cfg.get("feishu_target", "")
    ).strip()
    if not raw_target:
        log("FEISHU", f"skip send: no target configured (env HOT_TRENDS_FEISHU_TARGET / {SKILL_CONFIG_PATH})")
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

    if audio_path and os.path.isfile(audio_path):
        try:
            file_key = _upload_feishu_file(token, cfg["domain"], audio_path)
            _send_feishu_file(token, cfg["domain"], chat_id, file_key, Path(audio_path).name)
            log("FEISHU", f"podcast sent to chat:{chat_id}: {audio_path}")
        except Exception as e:
            log("FEISHU", f"podcast skipped: {type(e).__name__}: {e}")

    return True


import base64
import uuid

import requests


def send_discord_webhook(webhook_url: str, text: str, image_path: str = None) -> bool:
    """Send text + optional image via Discord Webhook (multipart/form-data).
    Handles long text by splitting into multiple messages (Discord limit: 2000 chars)."""
    try:
        # Split text into chunks of 1900 chars (leaving room for safety margin)
        CHUNK_SIZE = 1900
        chunks = []
        for i in range(0, len(text), CHUNK_SIZE):
            chunks.append(text[i : i + CHUNK_SIZE])

        sent_any = False
        for idx, chunk in enumerate(chunks):
            if len(chunks) > 1:
                chunk = f"[{idx + 1}/{len(chunks)}]\n{chunk}"
            if image_path and os.path.isfile(image_path) and idx == 0:
                # Only send image with the first chunk
                img_bytes = open(image_path, "rb").read()
                boundary = f"==={uuid.uuid4().hex}==="
                mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
                filename = Path(image_path).name
                parts = [
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"content\"\r\n\r\n{chunk}\r\n".encode(),
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\nContent-Transfer-Encoding: base64\r\n\r\n{base64.b64encode(img_bytes).decode()}\r\n".encode(),
                    f"--{boundary}--\r\n".encode(),
                ]
                resp = requests.post(
                    webhook_url,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    data=b"".join(parts),
                    timeout=60,
                )
            else:
                resp = requests.post(
                    webhook_url,
                    json={"content": chunk},
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )
            if resp.status_code in (200, 204):
                sent_any = True
            else:
                log("DISCORD", f"chunk {idx+1} failed: status={resp.status_code} body={resp.text[:100]}")
        return sent_any
    except Exception as e:
        log("DISCORD", f"webhook error: {type(e).__name__}: {e}")
        return False


def fetch_group(name, fn):
    log("FETCH", f"{name} started")
    try:
        items = fn(limit=50)
        log("FETCH", f"{name} completed with {len(items)} items")
        return name, items
    except Exception as e:
        log("FETCH", f"{name} failed: {e}")
        return name, []


RETRY_STATE_FILE = HERMES_STATE_DIR / "retry_state.json"
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


def _prepare_image_for_delivery(image_path: str, output_dir: Path) -> str:
    """Convert large NotebookLM PNG infographic to compact JPEG for delivery."""
    if not image_path or not os.path.isfile(image_path):
        return ""
    ffmpeg = shutil.which("ffmpeg")
    max_bytes = int(os.getenv("HOT_TRENDS_IMAGE_MAX_BYTES", str(1024 * 1024)))
    if os.path.getsize(image_path) <= max_bytes and Path(image_path).suffix.lower() in {".jpg", ".jpeg"}:
        return image_path
    if not ffmpeg:
        log("OUTPUT", "ffmpeg missing; keep original infographic")
        return image_path
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "hot_trends_infographic.jpg"
    max_width = os.getenv("HOT_TRENDS_IMAGE_MAX_WIDTH", "1800")
    quality = os.getenv("HOT_TRENDS_IMAGE_JPEG_Q", "3")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", image_path, "-vf", f"scale='min({max_width},iw)':-2", "-q:v", quality, str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
        log("OUTPUT", f"infographic converted for delivery: {out} ({out.stat().st_size} bytes)")
        return str(out)
    log("OUTPUT", f"infographic conversion failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return image_path


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
        help="飞书群目标 ID（可选；默认读取 HOT_TRENDS_FEISHU_TARGET 或 skill config.json）",
    )
    parser.add_argument(
        "--run-dir",
        help="运行目录，用于存放产物文件和本次执行输出",
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
    topics_data = None
    nlm_error = None
    for attempt in range(1, 4):
        try:
            topics_data = aggregate_topics(all_items, failed_platforms if not args.channel else [])
            break
        except Exception as e:
            nlm_error = e
            log("RUN", f"NotebookLM aggregation attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                wait = int(os.getenv("HOT_TRENDS_NLM_OUTER_RETRY_WAIT", "60"))
                log("RUN", f"retrying in {wait}s...")
                time.sleep(wait)
    if topics_data is None:
        log("RUN", f"NotebookLM aggregation failed after 3 attempts: {nlm_error}")
        log("RUN", "critical path failed — skipping delivery (no partial results)")
        sys.exit(2)

    email_subject = topics_data.get("title") or f"全球热点简报 {datetime.now().strftime('%Y-%m-%d')}"
    email_body = render_briefing(topics_data)
    email_topics_data = dict(topics_data)
    email_topics_data.pop("audio_path", None)
    email_topics_data.pop("audio_artifact_id", None)
    email_topics_data.pop("audio_notebook_id", None)
    email_html_body = render_briefing_email_html(email_topics_data)
    attachments = []
    # infographic 由独立图片sub-agent处理，主流程不打包

    audio_artifact_id = (topics_data.get("audio_artifact_id") or "").strip()
    audio_notebook_id = (topics_data.get("audio_notebook_id") or "").strip()

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

    # ---- 写入 artifacts ----
    run_dir = args.run_dir or os.getenv("HOT_TRENDS_RUN_DIR", "").strip() or tempfile.mkdtemp(prefix="hot-trends-")
    text_path = Path(run_dir) / "briefing.md"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    with text_path.open("w", encoding="utf-8") as f:
        f.write(email_body)
    log("OUTPUT", f"briefing text saved to {text_path}")

    write_artifacts_for_channel(text_path, None, run_dir, None)
    # ---- artifacts 写入完成 ----

    delivery_succeeded = False

    # 直接发送到飞书（文字）
    feishu_target = (os.getenv("HOT_TRENDS_FEISHU_TARGET", "") or args.feishu_target or "").strip()
    effective_feishu_target = feishu_target or _load_skill_config().get("feishu_target", "")
    if effective_feishu_target:
        log("FEISHU", f"start sending text to {effective_feishu_target}")
        feishu_ok = send_feishu_direct(
            email_body,
            media_path=None,
            audio_path=None,
            target=effective_feishu_target,
        )
        if not feishu_ok:
            log("FEISHU", "send failed")
        else:
            log("FEISHU", "send completed")
            delivery_succeeded = True
    else:
        log("FEISHU", "skip send: no Feishu target provided or configured")

    # ---- Discord 文字发送 ----
    discord_webhook = os.getenv("HOT_TRENDS_DISCORD_WEBHOOK", "").strip()
    if not discord_webhook:
        push_targets = _load_push_targets()
        for ch in push_targets.get("channels", []):
            if ch.get("channel") == "discord":
                discord_webhook = ch.get("webhook_url", "")
                break
    if discord_webhook:
        log("DISCORD", f"start sending text to Discord")
        discord_ok = send_discord_webhook(discord_webhook, email_body, None)
        if discord_ok:
            log("DISCORD", "send completed")
            delivery_succeeded = True
        else:
            log("DISCORD", "send failed")
    else:
        log("DISCORD", "skip send: no HOT_TRENDS_DISCORD_WEBHOOK provided")

    # 日期（用于输出文件名）
    from datetime import date as _date
    briefing_date = _date.today().strftime("%Y%m%d")

    # ---- 并行写图片+音频任务文件 ----
    import json as _json

    img_artifact_id = (topics_data.get("infographic_artifact_id") or "").strip()
    img_notebook_id = (topics_data.get("infographic_notebook_id") or "").strip()
    img_task_file = HERMES_OUTBOUND_DIR / "image_task.json"
    if img_artifact_id and img_notebook_id:
        HERMES_OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)
        with open(img_task_file, "w", encoding="utf-8") as f:
            _json.dump({
                "artifact_id": img_artifact_id,
                "notebook_id": img_notebook_id,
                "feishu_target": effective_feishu_target,
                "discord_webhook": discord_webhook,
                "output_path": str(HERMES_OUTBOUND_DIR / f"hot_trends_{briefing_date}_infographic.jpg"),
            }, f, ensure_ascii=False)
        log("IMAGE", f"image task queued: {img_task_file}")

    # 音频任务
    aud_artifact_id = (topics_data.get("audio_artifact_id") or "").strip()
    aud_notebook_id = (topics_data.get("audio_notebook_id") or "").strip()
    aud_task_file = HERMES_OUTBOUND_DIR / "audio_task.json"
    if aud_artifact_id and aud_notebook_id:
        HERMES_OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)
        with open(aud_task_file, "w", encoding="utf-8") as f:
            _json.dump({
                "artifact_id": aud_artifact_id,
                "notebook_id": aud_notebook_id,
                "feishu_target": effective_feishu_target,
                "discord_webhook": discord_webhook,
                "output_path": str(HERMES_OUTBOUND_DIR / f"hot_trends_{briefing_date}_podcast.m4a"),
            }, f, ensure_ascii=False)
        log("AUDIO", f"audio task queued: {aud_task_file}")

    # ---- 邮件：纯文字，无附件 ----
    email_to = os.getenv("HOT_TRENDS_EMAIL_TO", DEFAULT_EMAIL_TO).strip() or DEFAULT_EMAIL_TO
    log("EMAIL", f"start sending text email to {email_to}")
    email_proc = None
    for attempt in range(3):
        email_proc = send_email_via_gog(
            email_subject,
            email_body,
            html_body="",
            attachments=[],
            to_address=email_to,
        )
        if email_proc.returncode == 0:
            break
        log("EMAIL", f"email send attempt {attempt + 1} failed, retrying in 30s...")
        time.sleep(30)
    if email_proc and email_proc.returncode != 0:
        log("EMAIL", f"email send failed after 3 attempts: {email_proc.stderr.strip() or email_proc.stdout.strip()}")
    else:
        log("EMAIL", f"email sent successfully to {email_to}")

    if delivery_succeeded:
        _mark_succeeded()
    else:
        log("RETRY", "no primary delivery channel succeeded; keep retry state open")


if __name__ == "__main__":
    main()

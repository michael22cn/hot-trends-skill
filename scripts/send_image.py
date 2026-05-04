#!/usr/bin/env python3
"""全球热点简报图片后处理：轮询NotebookLM信息图→下载→压缩→发飞书+Discord。
由 cron agent 在 hot_trends.py 完成后并行调用，或手动触发。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
SKILL_DIR = Path(__file__).resolve().parents[2]
HERMES_OUTBOUND_DIR = Path.home() / ".hermes" / "workspace" / "outbound_media"
IMAGE_TASK_FILE = HERMES_OUTBOUND_DIR / "image_task.json"
STATE_FILE = HERMES_OUTBOUND_DIR / "image_state.json"

# ── logging ────────────────────────────────────────────────────────────────
def log(stage, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{stage}] {msg}", flush=True)


# ── NLM helpers ───────────────────────────────────────────────────────────
def _run_nlm(args, timeout=180):
    env = os.environ.copy()
    env["PATH"] = f"{SKILL_DIR / '.venv' / 'bin'}:{env.get('PATH', '')}"
    result = subprocess.run(
        ["nlm"] + args, capture_output=True, text=True, timeout=timeout, env=env
    )
    return result


def _nlm_status(notebook_id, artifact_id):
    proc = _run_nlm(["status", "artifacts", notebook_id, "--json"], timeout=120)
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        return None
    for item in payload if isinstance(payload, list) else []:
        if item.get("id") == artifact_id:
            return item.get("status")
    return None


def _nlm_download_infographic(notebook_id, artifact_id, output_path):
    proc = _run_nlm(
        ["download", "infographic", notebook_id, "--id", artifact_id,
         "--output", output_path, "--no-progress"],
        timeout=300,
    )
    return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0


# ── Feishu helpers ────────────────────────────────────────────────────────
def _load_feishu_config():
    import yaml
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    feishu = cfg.get("platforms", {}).get("feishu", {}).get("extra", {})
    return {
        "app_id": feishu.get("app_id", ""),
        "app_secret": feishu.get("app_secret", ""),
        "domain": feishu.get("domain", "open.feishu.cn"),
    }


def _feishu_token(cfg):
    import requests
    url = f"https://{cfg['domain']}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu token: {data.get('msg')}")
    return data["tenant_access_token"]


def _upload_feishu_image(token, domain, image_path):
    import mimetypes
    import requests
    url = f"https://{domain}/open-apis/im/v1/images"
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as f:
        files = {"image": (Path(image_path).name, f, mime)}
        data = {"image_type": "message"}
        resp = requests.post(url,
                            headers={"Authorization": f"Bearer {token}"},
                            data=data, files=files, timeout=180)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu image upload: {result.get('msg')}")
    return result["data"]["image_key"]


def _send_feishu_image(token, domain, chat_id, image_key):
    import json
    import requests
    url = f"https://{domain}/open-apis/im/v1/messages"
    payload = {
        "receive_id": chat_id,
        "msg_type": "image",
        "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
    }
    resp = requests.post(url, params={"receive_id_type": "chat_id"},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json; charset=utf-8"},
                         json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu send image: {data.get('msg')}")


def send_feishu_image(image_path, chat_id):
    cfg = _load_feishu_config()
    token = _feishu_token(cfg)
    image_key = _upload_feishu_image(token, cfg["domain"], image_path)
    _send_feishu_image(token, cfg["domain"], chat_id, image_key)
    log("FEISHU", f"image sent: {image_path}")


# ── Discord helpers ──────────────────────────────────────────────────────
def send_discord_image(webhook_url, image_path, caption=""):
    import base64
    import mimetypes
    import requests
    import uuid
    from pathlib import Path
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as f:
        img_bytes = f.read()
    boundary = f"==={uuid.uuid4().hex}==="
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"content\"\r\n\r\n{caption}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{Path(image_path).name}\"\r\nContent-Type: {mime}\r\nContent-Transfer-Encoding: base64\r\n\r\n{base64.b64encode(img_bytes).decode()}\r\n".encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    resp = requests.post(webhook_url,
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                         data=b"".join(parts), timeout=300)
    if resp.status_code in (200, 204):
        log("DISCORD", f"image sent: {image_path}")
        return True
    log("DISCORD", f"image failed: {resp.status_code} {resp.text[:100]}")
    return False


# ── image processing ───────────────────────────────────────────────────────
def wait_and_download_infographic(notebook_id, artifact_id, output_path):
    wait_seconds = int(os.getenv("HOT_TRENDS_NLM_INFOGRAPHIC_WAIT", "900"))
    poll_seconds = int(os.getenv("HOT_TRENDS_NLM_INFOGRAPHIC_POLL", "10"))
    deadline = time.time() + wait_seconds

    while time.time() < deadline:
        status = _nlm_status(notebook_id, artifact_id)
        log("IMAGE", f"artifact status={status or 'unknown'}")
        if status == "completed":
            log("IMAGE", "artifact ready, downloading...")
            if _nlm_download_infographic(notebook_id, artifact_id, output_path):
                return True
            log("IMAGE", "download failed, retrying...")
            time.sleep(poll_seconds)
            continue
        if status in {"failed", "error"}:
            log("IMAGE", f"artifact {status}")
            return False
        time.sleep(poll_seconds)
    log("IMAGE", "timeout waiting for infographic artifact")
    return False


def compress_infographic(input_path, output_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log("IMAGE", "ffmpeg missing, copying original")
        shutil.copy2(input_path, output_path)
        return output_path
    max_width = os.getenv("HOT_TRENDS_IMAGE_MAX_WIDTH", "1800")
    quality = os.getenv("HOT_TRENDS_IMAGE_JPEG_Q", "3")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", input_path,
           "-vf", f"scale='min({max_width},iw)':-2",
           "-q:v", quality, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode == 0 and os.path.exists(output_path):
        log("IMAGE", f"compressed: {output_path} ({os.path.getsize(output_path)} bytes)")
        return output_path
    log("IMAGE", f"compression failed: {proc.stderr.strip()[:200]}")
    return input_path


# ── state ─────────────────────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_artifact_id": ""}


def save_state(state):
    HERMES_OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────
def main():
    if not IMAGE_TASK_FILE.exists():
        log("IMAGE", "no image_task.json found, skipping")
        return 0

    task = json.loads(IMAGE_TASK_FILE.read_text(encoding="utf-8"))
    artifact_id = task["artifact_id"]
    notebook_id = task["notebook_id"]
    feishu_target = task.get("feishu_target", "").strip()
    discord_webhook = task.get("discord_webhook", "").strip()
    output_path = task["output_path"]

    log("IMAGE", f"task: artifact={artifact_id} notebook={notebook_id}")

    state = load_state()
    if state.get("last_artifact_id") == artifact_id:
        log("IMAGE", "already processed this artifact, skipping")
        return 0
    save_state({"last_artifact_id": artifact_id})

    # 1. Download
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        ok = wait_and_download_infographic(notebook_id, artifact_id, output_path)
        if not ok:
            log("IMAGE", "download failed, giving up")
            return 1
    else:
        log("IMAGE", f"using cached: {output_path}")

    # 2. Compress to JPG
    compressed = output_path
    if not output_path.lower().endswith(".jpg"):
        compressed = output_path.rsplit(".", 1)[0] + ".jpg"
    compressed = compress_infographic(output_path, compressed)

    # 3. Send to Feishu
    if feishu_target:
        try:
            send_feishu_image(compressed, feishu_target)
        except Exception as e:
            log("FEISHU", f"send failed (non-fatal): {type(e).__name__}: {e}")

    # 4. Send to Discord
    if discord_webhook:
        try:
            send_discord_image(discord_webhook, compressed,
                               "🌐 全球热点信息图")
        except Exception as e:
            log("DISCORD", f"send failed (non-fatal): {type(e).__name__}: {e}")

    log("IMAGE", "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

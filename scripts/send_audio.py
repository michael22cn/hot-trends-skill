#!/usr/bin/env python3
"""全球热点简报音频后处理：下载 + 压缩 + 发飞书 + 发 Discord。
由 cron agent 在 hot_trends.py 完成后调用，或手动触发。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
SKILL_DIR = Path(__file__).resolve().parents[2]
HERMES_OUTBOUND_DIR = Path.home() / ".hermes" / "workspace" / "outbound_media"
AUDIO_TASK_FILE = HERMES_OUTBOUND_DIR / "audio_task.json"
STATE_FILE = HERMES_OUTBOUND_DIR / "audio_state.json"

# ── logging ────────────────────────────────────────────────────────────────
def log(stage, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{stage}] {msg}", flush=True)


# ── NLM helpers (mirrors aggregator.py logic) ─────────────────────────────
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


def _nlm_download_audio(notebook_id, artifact_id, output_path):
    proc = _run_nlm(
        ["download", "audio", notebook_id, "--id", artifact_id,
         "--output", output_path, "--no-progress"],
        timeout=300,
    )
    return proc.returncode == 0 and os.path.exists(output_path)


def _nlm_tool_python():
    nlm_path = shutil.which("nlm") or ""
    if not nlm_path or not os.path.isfile(nlm_path):
        return ""
    try:
        first = open(nlm_path, "r", encoding="utf-8").readline().strip()
    except OSError:
        return ""
    if first.startswith("#!"):
        candidate = first[2:].strip().split()[0]
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _download_audio_python_fallback(notebook_id, artifact_id, output_path):
    py = _nlm_tool_python()
    if not py:
        return False
    # refresh credentials
    subprocess.run(["nlm", "login"], capture_output=True, text=True, timeout=30)
    code = f"""
import os
from notebooklm_tools.cli.utils import get_client
nb = os.environ['NLM_NOTEBOOK_ID']
aid = os.environ['NLM_ARTIFACT_ID']
out = os.environ['NLM_OUTPUT_PATH']
with get_client() as client:
    path = client.download_audio(nb, out, artifact_id=aid)
print(path)
"""
    env = os.environ.copy()
    env.update({"NLM_NOTEBOOK_ID": notebook_id, "NLM_ARTIFACT_ID": artifact_id,
                "NLM_OUTPUT_PATH": output_path})
    proc = subprocess.run([py, "-c", code], capture_output=True, text=True,
                          timeout=300, env=env)
    return proc.returncode == 0 and os.path.exists(output_path)


# ── Feishu helpers (mirrors hot_trends.py logic) ──────────────────────────
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


def _upload_feishu_file(token, domain, file_path):
    import mimetypes
    import requests
    url = f"https://{domain}/open-apis/im/v1/files"
    file_name = Path(file_path).name
    mime = mimetypes.guess_type(file_path)[0] or "audio/mpeg"
    with open(file_path, "rb") as f:
        files = {"file": (file_name, f, mime)}
        data = {"file_type": "stream", "file_name": file_name}
        resp = requests.post(url,
                             headers={"Authorization": f"Bearer {token}"},
                             data=data, files=files, timeout=300)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu upload: {result.get('msg')}")
    return result["data"]["file_key"]


def _send_feishu_file(token, domain, chat_id, file_key, file_name):
    import json
    import requests
    url = f"https://{domain}/open-apis/im/v1/messages"
    payload = {
        "receive_id": chat_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key, "file_name": file_name}, ensure_ascii=False),
    }
    resp = requests.post(url, params={"receive_id_type": "chat_id"},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json; charset=utf-8"},
                         json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu send file: {data.get('msg')}")


def send_feishu_audio(file_path, chat_id):
    cfg = _load_feishu_config()
    token = _feishu_token(cfg)
    file_key = _upload_feishu_file(token, cfg["domain"], file_path)
    _send_feishu_file(token, cfg["domain"], chat_id, file_key, Path(file_path).name)
    log("FEISHU", f"audio sent: {file_path}")


# ── Discord helpers (mirrors hot_trends.py logic) ───────────────────────────
def send_discord_audio(webhook_url, file_path, caption):
    import base64
    import mimetypes
    import requests
    import uuid
    from pathlib import Path
    mime = mimetypes.guess_type(file_path)[0] or "audio/mpeg"
    with open(file_path, "rb") as f:
        img_bytes = f.read()
    boundary = f"==={uuid.uuid4().hex}==="
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"content\"\r\n\r\n{caption}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{Path(file_path).name}\"\r\nContent-Type: {mime}\r\nContent-Transfer-Encoding: base64\r\n\r\n{base64.b64encode(img_bytes).decode()}\r\n".encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    resp = requests.post(webhook_url,
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                         data=b"".join(parts), timeout=300)
    if resp.status_code in (200, 204):
        log("DISCORD", f"audio sent: {file_path}")
        return True
    log("DISCORD", f"audio failed: {resp.status_code} {resp.text[:100]}")
    return False


# ── audio processing ────────────────────────────────────────────────────────
def wait_and_download_audio(notebook_id, artifact_id, output_path):
    wait_seconds = int(os.getenv("HOT_TRENDS_NLM_AUDIO_WAIT", "1800"))
    poll_seconds = int(os.getenv("HOT_TRENDS_NLM_AUDIO_POLL", "20"))
    deadline = time.time() + wait_seconds

    while time.time() < deadline:
        status = _nlm_status(notebook_id, artifact_id)
        log("AUDIO", f"artifact status={status or 'unknown'}")
        if status == "completed":
            log("AUDIO", "artifact ready, downloading...")
            if _nlm_download_audio(notebook_id, artifact_id, output_path):
                return True
            log("AUDIO", "CLI download failed, trying Python fallback...")
            if _download_audio_python_fallback(notebook_id, artifact_id, output_path):
                return True
            log("AUDIO", "Python fallback also failed, retrying...")
            time.sleep(poll_seconds)
            continue
        if status in {"failed", "error"}:
            log("AUDIO", f"artifact {status}")
            return False
        time.sleep(poll_seconds)
    log("AUDIO", "timeout waiting for audio artifact")
    return False


def compress_audio(input_path, output_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log("AUDIO", "ffmpeg missing, copying original")
        shutil.copy2(input_path, output_path)
        return output_path
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", input_path, "-vn", "-ac", "1", "-ar", "22050", "-b:a", "32k", output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode == 0 and os.path.exists(output_path):
        log("AUDIO", f"compressed: {output_path} ({os.path.getsize(output_path)} bytes)")
        return output_path
    log("AUDIO", f"compression failed: {proc.stderr.strip()[:200]}")
    return input_path


# ── state ──────────────────────────────────────────────────────────────────
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


# ── main ───────────────────────────────────────────────────────────────────
def main():
    if not AUDIO_TASK_FILE.exists():
        log("AUDIO", "no audio_task.json found, skipping")
        return 0

    task = json.loads(AUDIO_TASK_FILE.read_text(encoding="utf-8"))
    artifact_id = task["artifact_id"]
    notebook_id = task["notebook_id"]
    feishu_target = task.get("feishu_target", "").strip()
    discord_webhook = task.get("discord_webhook", "").strip()
    output_path = task["output_path"]
    mp3_path = output_path.replace(".m4a", ".mp3") if not output_path.endswith(".mp3") else output_path

    log("AUDIO", f"task: artifact={artifact_id} notebook={notebook_id}")

    state = load_state()
    if state.get("last_artifact_id") == artifact_id:
        log("AUDIO", "already processed this artifact, skipping")
        return 0
    save_state({"last_artifact_id": artifact_id})

    # 1. Download
    m4a_path = output_path
    if not os.path.exists(m4a_path) or os.path.getsize(m4a_path) == 0:
        ok = wait_and_download_audio(notebook_id, artifact_id, m4a_path)
        if not ok:
            log("AUDIO", "download failed, giving up")
            return 1
    else:
        log("AUDIO", f"using cached: {m4a_path}")

    # 2. Compress
    compressed = compress_audio(m4a_path, mp3_path)

    # 3. Send to Feishu
    if feishu_target:
        try:
            send_feishu_audio(compressed, feishu_target)
        except Exception as e:
            log("FEISHU", f"send failed (non-fatal): {type(e).__name__}: {e}")

    # 4. Send to Discord
    if discord_webhook:
        try:
            send_discord_audio(discord_webhook, compressed,
                                "🎧 全球热点播客（社会 / 游戏 / 经济 / 科技）")
        except Exception as e:
            log("DISCORD", f"send failed (non-fatal): {type(e).__name__}: {e}")

    log("AUDIO", "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

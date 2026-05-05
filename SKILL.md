---
name: hot-trends-brief
description: 每日全球热点简报。流程：14个平台并行抓取热搜 → NotebookLM聚合 → 生成信息图 → 发送飞书 + Discord + Gmail。
version: 1.5.0
author: Hermes
tags: [cron, discord, gmail, notebooklm, webhook, hot-trends]
homepage: https://github.com/NousResearch/hermes-agent
---

# 全球热点简报 | Hot Trends Briefing

每日全球热点简报生成器。

## 完整流程

```
14个平台并行抓取 → NotebookLM聚合 → 生成信息图 → 飞书群 + Discord Webhook + Gmail
```

## SKILL 结构

路径：`~/.hermes/skills/social-media/hot-trends-brief/`

```
scripts/
  hot_trends.py          # 主脚本（文字→飞书+Discord+邮件，纯文字）
  send_image.py          # 图片sub-agent（轮询→下载→压缩→飞书+Discord）
  send_audio.py          # 音频sub-agent（轮询→下载→压缩→飞书+Discord）
  scrapers/              # 平台抓取（cn/ + en/）
  lib/
    aggregator.py        # NotebookLM 交互
    renderer.py          # HTML/文本渲染
config.json              # 飞书目标（当前指向办公助手群）
references/
  debugging-notes.md     # 音频/图片 sub-agent 静默失败排查链（含已验证的 Feishu API 参数）
.env.example             # 环境变量示例
SKILL.md
```

## 平台覆盖

**中文平台**: 微博热搜 / 今日头条热榜 / 抖音热榜 / B站排行榜
**英文平台**: Reddit / YouTube / Google News / GitHub / Techmeme / Yahoo Finance / Perplexity / GamesRadar / Hacker News

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `HOT_TRENDS_DISCORD_WEBHOOK` | ✅ | Discord Webhook URL（文字+图片发送） |
| `HOT_TRENDS_FEISHU_TARGET` | 否 | 飞书群 chat_id；不填则回退到 `config.json`（当前为办公助手群） |
| `HOT_TRENDS_EMAIL_TO` | 否 | 邮件收件人，默认 `674080@qq.com` |
| `HOT_TRENDS_NLM_NOTEBOOK_ID` | 否 | 指定已有 Notebook，否则创建临时 |
| `HOT_TRENDS_NLM_KEEP_NOTEBOOK` | 否 | `1`=保留（默认），`0`=删除 |
| `HOT_TRENDS_GOG_ACCOUNT` | 否 | Gmail 账号（默认 `magic22cn@gmail.com`） |

## 执行命令

```bash
cd ~/.hermes/skills/social-media/hot-trends-brief && \
HOT_TRENDS_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." \
HOT_TRENDS_NLM_AUDIO_WAIT=1800 \
.venv/bin/python scripts/hot_trends.py \
  --html \
  --run-dir /tmp/hot-trends-$(date +%Y%m%d) \
  --output /tmp/hot-trends-daily.html
```

## 飞书发送

- 脚本优先读取 `HOT_TRENDS_FEISHU_TARGET`，未设置时回退到 skill 目录下 `config.json`
- 当前 `config.json` 已配置为办公助手群：`oc_f01d8f72f3ea4a56cfc58a76f436cfdc`
- 发送方式为 **Feishu Open API 直连**：先发文本，再上传信息图并发图片消息
- Feishu 凭证从 `~/.hermes/config.yaml` 的 `platforms.feishu.extra` 读取

## Discord 发送（Webhooks）

**⚠️ Hermes cron session 系统级禁止 `send_message` 发 Discord，必须用 Webhook！**

使用 Discord Webhook multipart/form-data 协议，同时支持文字+图片：

```python
import requests, base64, uuid

def send_discord_webhook(webhook_url: str, text: str, image_path: str = None) -> bool:
    if image_path:
        boundary = f"==={uuid.uuid4().hex}==="
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"content\"\r\n\r\n{text}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"brief.png\"\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n\r\n{base64.b64encode(open(image_path,'rb').read()).decode()}\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        r = requests.post(webhook_url,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=b"".join(parts))
    else:
        r = requests.post(webhook_url, json={"content": text})
    return r.status_code in (200, 204)
```

**当前 Webhook**: `https://discord.com/api/webhooks/1497978221352845442/Q8zA_JnTfAsuqYCwJqqdYCG7TfODx-A2NUouMJPcHeGYLm5praszVyscUag7W0bloH7K`

## Gmail 发送（gog CLI）

与 finance-video-brief 共用 `~/.local/bin/gog`（已安装）。

```bash
gog gmail send \
  --account=magic22cn@gmail.com \
  --to=674080@qq.com \
  --subject="🌐 全球热点简报" \
  --body-html="$(cat report.html)" \
  --attach=infographic.png
```

## Hermes Cron 配置

- **Schedule**: `0 0 * * *`（每天北京时间 00:00）
- **Deliver**: `origin`（结果回当前对话）
- **Skills**: `["hot-trends-brief", "nlm-skill"]`
- **Model**: MiniMax-M2.7
- **执行方式**：三步顺序
  1. `hot_trends.py --html` → 主流程（文字+图片+邮件）
  2. 仅当主流程 exit_code=0 后，并行执行媒体 sub-agent
  3. `send_image.py` → 图片（轮询→下载→压缩→飞书+Discord）**并行**
  4. `send_audio.py` → 音频（轮询→下载→压缩→飞书+Discord）**并行**
- 防旧任务误发：`hot_trends.py` 启动时先删除旧的 `image_task.json` / `audio_task.json`；如果 NLM 聚合失败，不会留下可被 sub-agent 误处理的昨天 task。

## 历史背景

此前存在旧实现与历史归档；当前 Hermes 版本已独立运行，不依赖旧系统目录或文件。

## 初始化 / 依赖安装

首次部署或新增依赖后，必须使用 `.venv/bin/python -m pip install`，禁止使用 `.venv/bin/pip install`（后者是 shim，可能安装到错误的 venv）。

```bash
cd ~/.hermes/skills/social-media/hot-trends-brief

# 安装所有依赖
.venv/bin/python -m pip install -r requirements.txt 2>/dev/null || true

# 手动补充常用依赖（如果 requirements.txt 缺失）
.venv/bin/python -m pip install pyyaml requests Pillow playwright ffmpeg-python

# 验证
.venv/bin/python -c "import yaml, requests, PIL; print('OK')"
```

---

## 已知限制

1. **Hermes cron `send_message` 被禁**：系统级策略，cron session 禁止使用 `send_message` 发 Discord。必须用 Webhook。
2. **NotebookLM 认证需有效**：需定期执行 `nlm login` 刷新 OAuth token。

## 已知问题排查

> ⚠️ 完整的调试笔记见 `references/debugging-notes.md`（含音频 sub-agent 静默失败的完整排查链）

### 飞书音频发送失败

**症状**：飞书收不到音频，但 Discord 有。

**排查步骤**：

1. 检查 task 文件：`cat ~/.hermes/workspace/outbound_media/audio_task.json`
2. 检查 state 文件：`cat ~/.hermes/workspace/outbound_media/audio_state.json`  
   如果 `last_artifact_id` 匹配 task 文件，sub-agent 会静默跳过 → `rm ~/.hermes/workspace/outbound_media/audio_state.json`
3. 确认 artifact 状态：`nlm status artifacts <notebook_id> --json` 确认 `status: "completed"`
4. 手动强制重跑：`rm -f ~/.hermes/workspace/outbound_media/audio_state.json && cd /data/hermes/skills/social-media/hot-trends-brief && .venv/bin/python scripts/send_audio.py`

**已修复的 bug（2026-05-04）**：
- `send_audio.py` 缺少域名归一化：`feishu` 无法解析 → 已添加 `open.feishu.cn` 修复逻辑
- ffmpeg 输出 `.tmp` 文件需显式 `-f mp3` 格式声明，否则报错 `choose a standard extension`
- ffmpeg 输入输出路径相同时拒绝运行 → 改用 `.tmp` 中间文件再 `shutil.move()`
- `nlm download audio --id <artifact>` 永远失败（--id 不支持 audio 下载）→ 必须用 positional notebook_id，省略 --id

## 已知问题排查

### 图片 sub-agent 超时/误跳过

**症状**：`send_image.py` 等待 15 分钟后仍然 `artifact status=in_progress`，退出码 1；再次运行却显示 `already processed this artifact, skipping`。

**根因**：NotebookLM 信息图生成可能超过 15 分钟；旧版 `send_image.py` 在下载前就写入 `image_state.json`，导致失败也被标记为已处理。

**修复**：
- 图片和音频 sub-agent 都是**轮询**，不是一次性等待：每 60 秒查一次 artifact 状态，最多轮询 60 分钟
- 默认值：`HOT_TRENDS_NLM_INFOGRAPHIC_WAIT=3600` / `HOT_TRENDS_NLM_INFOGRAPHIC_POLL=60`
- 默认值：`HOT_TRENDS_NLM_AUDIO_WAIT=3600` / `HOT_TRENDS_NLM_AUDIO_POLL=60`
- `image_state.json` 只在下载、压缩、发送流程结束后写入
- 手动恢复：
```bash
rm -f ~/.hermes/workspace/outbound_media/image_state.json
cd /data/hermes/skills/social-media/hot-trends-brief && \
.venv/bin/python scripts/send_image.py
```

### YouTube / Bilibili / Perplexity Discover 返回 0 条

**原因：Playwright 浏览器缺失**
```
BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-1208/...
```
**解决**：重新安装浏览器（**两个都要装**，不同 scraper 依赖不同的浏览器）
```bash
cd ~/.hermes/skills/social-media/hot-trends-brief && \
.venv/bin/playwright install chromium && \
.venv/bin/playwright install chrome
```
- `chromium` → Bilibili、YouTube 等使用 `chromium_headless_shell`
- `chrome` → Perplexity Discover 使用 `chromium`（完整 Chrome）

**原因 2：YouTube DOM 结构变化（2024+）**
`#video-title` 已改为 `<yt-formatted-string>`（不再是 `<a>`），视频链接在**父级 `<a>`** 上。提取逻辑需用 `page.evaluate()` 从 DOM 树获取 `el.closest('a').href`。

**原因 3：YouTube 官方 Tech Trending URL 错误**
旧 URL：`youtube.com/feed/news_destination/technology` → 返回 "This page is not available."
正确 URL：`youtube.com/feed/news_destination/science_and_technology`
注意：`/feed/news_destination/` 下只有 `science_and_technology`（科技）可用，其他子分类已废弃。

### NotebookLM 认证过期（"Authentication expired"）

**症状**：NLM 聚合失败，所有尝试都报 `Error: Failed to create notebook: Authentication expired`

**诊断**：
```bash
nlm login --check
```

**根因**：Google OAuth 会话过期（通常是 20 分钟到几天，取决于账号策略）。`profiles/default/cookies.json` 中的 cookie 失效。

**解决方案（需要人工介入）**：

1. **方法 A：交互式登录（推荐）**
   ```bash
   # 在有桌面的机器上执行一次
   nlm login
   # 验证成功
   nlm login --check
   ```
   然后 cron 机器直接使用相同的 profile 文件。

2. **方法 B：headless Chrome + OpenClaw（实验性）**
   在 cron 机器上先启动 headless Chrome：
   ```bash
   /usr/bin/google-chrome --headless=new --remote-debugging-port=9222 \
     --user-data-dir=/tmp/nlm-chrome-test &
   sleep 3
   # 然后 nlm login 连接到该端口
   nlm login --provider openclaw --cdp-url http://127.0.0.1:9222 --force
   ```
   **注意**：必须先在 headless Chrome 中完成一次 Google 登录（OAuth consent）才能提取有效 cookie。

3. **方法 C：`--manual --file`（有破坏性）**
   ⚠️ **已知的 bug**：此选项会将 `cookies.json` 从正确的 `array-of-objects` 格式转换为简单的 `key-value dict`，导致 NLM 完全失效。如果误用，需从 `auth.json` 恢复。

**恢复被 `--manual --file` 破坏的 cookies**：
`auth.json` 中存有完整的 cookie 字典格式。用以下脚本重建 `profiles/default/cookies.json`：
```python
# auth.json 的 cookies 字段是 {name: value} 字典，需要转换回 array-of-objects 格式
import json

with open('auth.json') as f:
    auth = json.load(f)

cookie_list = []
for name, value in auth['cookies'].items():
    cookie_list.append({
        'name': name,
        'value': value,
        'domain': '.google.com',  # 大部分 cookie 用这个
        'path': '/',
        'secure': True,
        ...
    })

with open('profiles/default/cookies.json', 'w') as f:
    json.dump(cookie_list, f, indent=2)
```

**NLM 会话有效期**：从 `profiles/default/metadata.json` 的 `last_validated` 字段查看上次成功验证时间。当前有效 session 通常持续数天到数周。

### NotebookLM 502 / 超时 / 聚合失败排查

先验证 NLM 本身：
```bash
command -v nlm
nlm --version
nlm login --check
```
如果 `nlm login --check` 有效，而热搜简报仍失败，优先检查热搜专用脚本设计，不要先归因于账号失效。

重点对比财经视频简报：
- finance-video-brief 的 NLM 调用是串行加源、短间隔重试、检测 `502/503/504/timeout` 后立即重试。
- hot-trends-brief 当前容易一次性并发上传十几个 source：`scripts/lib/aggregator.py` 的 `_nlm_source_upload_workers()` 默认 `return len(file_specs)`，并发过高会放大 NotebookLM 502/Bad Gateway。
- hot-trends-brief 外层聚合失败后等待 30 分钟再重试；在 cron/手动触发中容易表现为“命令超时”，不是没执行。
- 如果日志出现 `source upload failed` 或某个分类 `technology query failed` 且包含 502，本质通常是 NotebookLM 服务端/网关瞬时失败 + 本脚本并发/重试策略太激进。

已修复项：
1. `scripts/lib/aggregator.py` 默认 `HOT_TRENDS_NLM_SOURCE_CONCURRENCY` 未设置时只并发 2 个 source，避免一次性十几个 source 打爆 NotebookLM。
2. `_add_source_to_notebook()`、`notebook create`、`notebook query`、信息图生成/下载均已增加短重试；命中 502/503/504/timeout 等瞬时错误会等待 8-15 秒后重试。
3. `scripts/hot_trends.py` 外层 NotebookLM 聚合失败重试等待从 30 分钟改为 `HOT_TRENDS_NLM_OUTER_RETRY_WAIT`，默认 60 秒，避免 cron/手动触发长时间假死。
4. cron job `b49a63ac96b1` 必须保持 skills 为 `["hot-trends-brief", "nlm-skill"]`，workdir 为 `/data/hermes/skills/social-media/hot-trends-brief`。
5. NLM 聚合流程会额外生成中文播客（audio overview）：围绕社会、游戏、经济、科技四个方面讨论当天全球热门；播客生成、下载、压缩、飞书/Discord 发送全部由独立音频脚本 `scripts/send_audio.py` 处理，不在主流程中阻塞。

**新架构（v1.5+）**：
- `hot_trends.py` 主流程：只返回 `infographic_artifact_id` + `audio_artifact_id`，不等待下载
- `send_image.py`：图片 sub-agent，轮询 NotebookLM → 下载 PNG → ffmpeg 压缩为 JPG → 飞书+Discord 发送
- `send_audio.py`：音频 sub-agent，轮询 NotebookLM → 下载 m4a → ffmpeg 压缩为 mp3 → 飞书+Discord 发送
- **输出文件名带日期**：`hot_trends_YYYYMMDD_infographic.jpg` / `hot_trends_YYYYMMDD_podcast.m4a`（由 `hot_trends.py` 在写任务文件时注入）
- 任务文件：`~/.hermes/workspace/outbound_media/image_task.json` / `audio_task.json`
- cron job 三步：主流程 → (图片sub-agent 并行) → (音频sub-agent 并行)
- 文字先到，图片/音频稍后（非关键路径，任何失败只写日志）

6. 邮件发送纯文字正文（无 HTML、无图片附件），不展示播客音频。
7. NotebookLM 信息图原始 PNG 常很大；发送前用 ffmpeg 转为 JPG（默认宽度 1800、`-q:v 3`），通常可从约 5MB 压到约 0.4MB，飞书/Discord/邮件附件都走压缩后的 JPG。

**重试规则（现状）：**
- NotebookLM 聚合失败时最多重试 **2 次**（加上首次共 3 次尝试）
- 重试间隔：30 分钟
- 任何一次重试成功则继续执行

**关键路径 fast-fail：**
- NotebookLM 是信息图 + AI 聚合摘要的**关键依赖**
- 3 次尝试全部失败后，**直接退出码 2，不发送任何内容**（不发送不伦不类的原始数据）
- 不会走 bypass 逻辑，不会发半成品到飞书/Discord/Gmail

## 调试命令

```bash
# 手动触发（加 --no-retry 跳过重试检查）
cd ~/.hermes/skills/social-media/hot-trends-brief && \
HOT_TRENDS_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." \
.venv/bin/python scripts/hot_trends.py \
  --html --no-retry \
  --run-dir /tmp/hot-trends-test \
  --output /tmp/hot-trends-daily.html

# ⚠️ 重新跑之前必须清除所有状态文件
rm -f ~/.hermes/state/hot-trends-brief/retry_state.json
rm -f ~/.hermes/workspace/outbound_media/audio_state.json
rm -f ~/.hermes/workspace/outbound_media/image_state.json

# 强制重跑音频 sub-agent（不走缓存）
cd ~/.hermes/skills/social-media/hot-trends-brief && \
  .venv/bin/python scripts/send_audio.py

# 手动下载音频（nlm download audio 禁止加 --id，必须用 positional）
notebook_id=...
nlm download audio $notebook_id --output /tmp/podcast.m4a    # 正确
nlm download audio $notebook_id --id <artifact> --output ...  # 错误：永远失败

# 查看 cron 状态
hermes cron list

# 手动触发 cron job
hermes cron run <job_id>

# 看日志
tail -f ~/.hermes/cron/output/<job_id>/*.log
```

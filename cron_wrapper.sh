#!/bin/bash
# 定时任务包装脚本：运行 hot-trends 简报并同步发到飞书群
# 依赖：openclaw cli, gog cli, nlm cli (NotebookLM)
#
# 执行顺序：抓取 → NotebookLM生成 → 飞书发送 → 邮件发送（飞书优先）

SKILL_DIR="/home/michael/.openclaw/agents/officemaster/skills/hot-trends-skill"
VENV_BIN="$SKILL_DIR/.venv/bin"
OUTPUT_HTML="/tmp/hot-trends-cron-$(date +%Y%m%d).html"

SKILL_CONFIG="$SKILL_DIR/config.json"

log() {
    echo "[$(date '+%H:%M:%S')] [CRON-FEISHU] $1"
}

# 从 skill 配置文件中读取飞书目标频道
FEISHU_TARGET=$(python3 -c "import json; print(json.load(open('$SKILL_CONFIG'))['feishu_target'])")

log "========== 开始执行 hot-trends 简报任务 =========="
log "输出文件: $OUTPUT_HTML"
log "飞书目标: $FEISHU_TARGET"

# 运行简报生成
# 脚本内部顺序：生成HTML → 飞书发送（优先）→ 邮件发送
cd "$SKILL_DIR"
HOT_TRENDS_EMAIL_TO="674080@qq.com" \
HOT_TRENDS_PERPLEXITY_HEADLESS=0 \
$VENV_BIN/python3 scripts/hot_trends.py \
    --html \
    --output "$OUTPUT_HTML" \
    --feishu-target "$FEISHU_TARGET" \
    2>&1 | tee /tmp/hot-trends-cron-run.log

EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -ne 0 ]; then
    log "⚠️ hot-trends 脚本执行异常，exit code: $EXIT_CODE"
    # 脚本内部已优先发飞书，仅记录异常
fi

log "========== 任务完成 =========="

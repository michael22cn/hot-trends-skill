---
name: hot-trends
version: "0.4"
description: "每日全球热点简报生成器。唯一流程：并行抓取中英文平台热搜，并行写入 NotebookLM，基于 NotebookLM 输出简报。"
argument-hint: '[可选：指定输出文件路径 -o]'
allowed-tools: Bash, Read, Write
user-invocable: true
---

# 全球热点简报 | Hot Trends Briefing

抓取中英文平台热搜，并行写入 NotebookLM，由 NotebookLM 输出可直接发布的简报。

## 频道配置

飞书目标频道写在 skill 目录的 `config.json` 中：

```json
{
  "feishu_target": "oc_f01d8f72f3ea4a56cfc58a76f436cfdc"
}
```

cron 包装脚本 `cron_wrapper.sh` 会自动从该文件读取并传给脚本，无需环境变量。

## 执行步骤

1. **生成简报**：在 officemaster 环境下运行 `cd /home/michael/.openclaw/agents/officemaster/skills/hot-trends-skill && .venv/bin/python3 scripts/hot_trends.py --html --output /tmp/hot-trends-daily.html --run-dir /tmp/hot-trends-$(date +%Y%m%d)`
2. **确认产物**：检查 `~/.openclaw/workspace/outbound_media/hot_trends_artifacts.json`，读取 `channel_text_path` 和 `channel_media_path`
3. **确认发送**：脚本会读取 `HOT_TRENDS_FEISHU_TARGET`，直接调用 `openclaw message send --channel feishu` 发送文本与图片；无需额外 announce 步骤

## 平台覆盖

**中文平台**: 微博热搜 / 今日头条热榜 / 抖音热榜 / B站排行榜
**英文平台**: Reddit (`r/technology` / `r/programming` / `r/artificial`) / YouTube (`gaming_trending_us` / `technology_news_us` / `focus_news_us`) / Twitter Trending / Google News / GitHub Trending API / Techmeme / Yahoo Finance Trending / Perplexity Discover / GamesRadar Games News

## 输出格式

```markdown
# 全球热点分类简报 | 2026年3月23日

## 社会热点

1. **[话题标题]**
   50-100字总结...

## 经济热点

1. **[话题标题]**
   50-100字总结...
```

## 使用方式

```bash
cd hot-trends-skill
python3 scripts/hot_trends.py

python3 scripts/hot_trends.py -o ~/Desktop/hot-trends.md
```

## NotebookLM 配置

脚本只支持 NotebookLM 单一路径，可通过环境变量控制：

```bash
export HOT_TRENDS_NLM_NOTEBOOK_ID="已有 notebook id"   # 可选
export HOT_TRENDS_NLM_PROFILE="default"               # 可选
export HOT_TRENDS_NLM_KEEP_NOTEBOOK=1                 # 可选，保留临时 notebook
export HOT_TRENDS_PERPLEXITY_HEADLESS=0               # 可选，Perplexity 默认走可视浏览器；设为 1 可切回无头
```

执行流程固定为：

1. `nlm notebook create`（如未提供 notebook id）
2. 所有 source 先全部准备完，再对全部 source 执行并行 `nlm source add --wait`
3. `nlm notebook query --json`（社会话题归纳）
4. `nlm notebook query --json`（经济话题归纳）
5. `nlm notebook query --json`（科技话题归纳）
6. `nlm notebook query --json`（游戏话题归纳）
7. `nlm infographic create` + `nlm download infographic`（如成功则在邮件和 HTML 中直接展示）

### 并行执行要求

- 整个 skill 只有一套流程，不允许根据模型能力、可用工具或失败情况切换到其他总结路径。
- 绝不允许使用 mock data、示例数据、伪造 source、静态样例输出或占位结果。
- 所有平台 source 必须并行抓取，不能只并行其中几个平台。
- 抓取阶段与 NotebookLM 上传阶段必须解耦；即使 NotebookLM 侧需要限流，也不能反向降低抓取阶段并发。
- 所有待上传到 NotebookLM 的 source 必须一次性全部并行提交，不能默认分批、限流到 2/4 个 worker。
- 只有在显式设置环境变量 `HOT_TRENDS_NLM_SOURCE_CONCURRENCY` 时，才允许限制 NotebookLM 上传并发。
- 默认行为应为：`max_workers = source 总数`，也就是"有多少 source 就开多少并发上传任务"。
- 抓取阶段默认行为应为：`max_workers = 平台总数`，先拿全量抓取结果，再进入 NotebookLM 上传阶段。
- 不允许回退到外部 LLM API、本地摘要、备用模板或本地信息图生成。

## 环境要求

- Python 3.8+
- Playwright (`playwright install chromium`)
- 本地可用的 `nlm` CLI

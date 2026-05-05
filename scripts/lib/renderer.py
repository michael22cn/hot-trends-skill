import base64
import html as html_lib
import mimetypes
import os
import re
from datetime import date
from typing import Dict


def _summary_to_html(text: str) -> str:
    text = text or ""
    lines = [line.rstrip() for line in text.splitlines()]
    parts = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        escaped = html_lib.escape(line)
        escaped = escaped.replace("**", "<strong>", 1).replace("**", "</strong>", 1) if escaped.count("**") >= 2 else escaped

        if line.startswith("### "):
            parts.append(f'<h3 class="rich-h3" style="font-size:17px;color:#f3f3f3;line-height:1.35;margin:18px 0 8px;">{html_lib.escape(line[4:])}</h3>')
        elif line.startswith("## "):
            parts.append(f'<h2 class="rich-h2" style="font-size:20px;color:#f3f3f3;line-height:1.35;margin:18px 0 8px;">{html_lib.escape(line[3:])}</h2>')
        elif line.startswith("**") and line.endswith("**") and len(line) > 4:
            parts.append(f'<h3 class="rich-h3" style="font-size:17px;color:#f3f3f3;line-height:1.35;margin:18px 0 8px;">{html_lib.escape(line[2:-2])}</h3>')
        elif line.startswith("*   ") or line.startswith("* "):
            bullet = line.split("*", 1)[1].strip()
            bullet = html_lib.escape(bullet)
            bullet = bullet.replace("**", "<strong>", 1).replace("**", "</strong>", 1) if bullet.count("**") >= 2 else bullet
            parts.append(f'<div class="rich-bullet" style="font-size:15px;color:#d2d2d2;line-height:1.68;margin:10px 0;padding:10px 12px;border-left:3px solid #f0c040;background:rgba(240,192,64,0.08);">{bullet}</div>')
        elif line == "---":
            parts.append('<div class="rich-divider"></div>')
        else:
            parts.append(f'<p class="rich-p" style="font-size:15px;color:#b6b6b6;line-height:1.72;margin:0 0 10px;">{escaped}</p>')

    return "".join(parts)

def _group_topics_by_category(topics):
    ordered_categories = ["社会", "经济", "科技", "游戏"]
    grouped = {category: [] for category in ordered_categories}
    for topic in topics:
        category = topic.get("category", "").strip() or "其他"
        grouped.setdefault(category, []).append(topic)
    return [(category, grouped[category]) for category in grouped if grouped[category]]


def render_briefing(data: Dict) -> str:
    today = date.today().strftime("%Y年%m月%d日")
    title = data.get("title", f"全球热点日报 | {today}")
    failed_platforms = data.get("failed_platforms", [])

    output = []

    output.append(title)
    output.append("=" * 40)
    output.append("")

    if failed_platforms:
        output.append(f"⚠️ 数据异常平台: {', '.join(f'✗ {p}' for p in failed_platforms)}")
        output.append("")

    topics = data.get("topics", [])
    references = data.get("references", [])
    infographic_path = data.get("infographic_path", "")

    for category, category_topics in _group_topics_by_category(topics):
        output.append(f"{category}热点")
        output.append("-" * 20)
        for i, topic in enumerate(category_topics, 1):
            output.append(f"{i}. {topic.get('title', '')}")
            output.append(f"   {topic.get('summary', '')}")
            output.append("")

    if references:
        output.append("引用说明")
        output.append("-" * 20)
        for ref in references:
            label = ref.get("label", "")
            excerpt = (ref.get("excerpt", "") or "").strip()
            if excerpt:
                output.append(f"{ref.get('number')}、{label} | {excerpt}")
            else:
                output.append(f"{ref.get('number')}、{label}")
        output.append("")

    if infographic_path:
        output.append(f"信息图：{infographic_path}")
        output.append("")

    output.append(f"由 AI 自动生成 | {today}")

    return "\n".join(output)


def _embed_file_base64(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _topic_card_html(topic: Dict, index: int) -> str:
    title = html_lib.escape(topic.get("title", ""))
    summary = _summary_to_html(topic.get("summary", ""))
    citations = " ".join(f"[{num}]" for num in topic.get("citations", [])[:6])
    citations_html = f'<p class="topic-meta">引用 {html_lib.escape(citations)}</p>' if citations else ""
    sources = topic.get("sources", []) or []
    sources_html = ""
    if sources:
        chips = "".join(f'<span class="chip">{html_lib.escape(str(src))}</span>' for src in sources[:6])
        sources_html = f'<div class="chips">{chips}</div>'
    return f"""
      <div class="entry topic-card">
        <p class="topic-index">{index:02d}</p>
        <h3>{title}</h3>
        <div class="topic-summary">{summary}</div>
        {sources_html}
        {citations_html}
      </div>
"""


def render_briefing_html(data: Dict) -> str:
    today = date.today().strftime("%Y-%m-%d")
    report_title = data.get("title", "全球热点日报")
    topics = data.get("topics", [])
    references = data.get("references", [])
    infographic_path = data.get("infographic_path", "")
    audio_path = data.get("audio_path", "")
    failed_platforms = data.get("failed_platforms", [])
    notebook_url = data.get("notebook_url", "")

    # 不把信息图/音频 base64 内嵌到邮件 HTML：
    # gog 只支持 --body-html 字符串，内嵌大文件会触发 Argument list too long。
    has_infographic = bool(infographic_path and os.path.isfile(infographic_path))

    failed_section = ""
    if failed_platforms:
        failed_items = "".join(f"<li>{html_lib.escape(fp)} 数据异常</li>" for fp in failed_platforms)
        failed_section = f"""
    <section class="section alert">
      <h2>数据异常平台</h2>
      <ul>{failed_items}</ul>
    </section>
"""

    infographic_section = ""
    if has_infographic:
        infographic_section = """
    <section class="section">
      <h2>信息图</h2>
      <p>信息图已随邮件附件发送，也已同步到飞书和 Discord。</p>
    </section>
"""

    audio_section = ""
    if audio_path:
        audio_section = f"""
    <section class="section">
      <h2>播客</h2>
      <p>根据社会、游戏、经济、科技四个方面探讨今天的全球热门。</p>
      <p>播客音频已发送到飞书和 Discord；邮件不附带音频，避免附件过大。</p>
    </section>
"""

    topic_sections = []
    for category, category_topics in _group_topics_by_category(topics):
        cards = "".join(_topic_card_html(topic, i) for i, topic in enumerate(category_topics, 1))
        topic_sections.append(f"""
    <section class="section">
      <h2>{html_lib.escape(category)}热点</h2>
      {cards}
    </section>
""")

    refs_section = ""
    if references:
        refs = []
        for ref in references:
            label = html_lib.escape(ref.get("label", ""))
            excerpt = html_lib.escape((ref.get("excerpt", "") or "").strip())
            suffix = f" — {excerpt}" if excerpt else ""
            refs.append(f'<li><strong>{ref.get("number")}</strong>、{label}{suffix}</li>')
        refs_section = f"""
    <section class="section">
      <h2>引用说明</h2>
      <ul>{''.join(refs)}</ul>
    </section>
"""

    notebook_link = ""
    if notebook_url:
        safe_url = html_lib.escape(notebook_url, quote=True)
        notebook_link = f'<p>NotebookLM: <a href="{safe_url}">{html_lib.escape(notebook_url)}</a></p>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html_lib.escape(report_title)} {today}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1ea;
      --card: #fffdf9;
      --ink: #1f2328;
      --muted: #5f6b76;
      --accent: #0f5c4d;
      --border: #d7d1c7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px 18px;
      background:
        radial-gradient(circle at top left, rgba(15, 92, 77, 0.08), transparent 34%),
        linear-gradient(180deg, #f8f4ed 0%, var(--bg) 100%);
      color: var(--ink);
      font: 16px/1.7 Georgia, "Times New Roman", "Noto Serif SC", "Songti SC", serif;
    }}
    .wrap {{ max-width: 960px; margin: 0 auto; }}
    .hero {{
      padding: 28px 32px;
      border: 1px solid var(--border);
      background: linear-gradient(135deg, #fffdf8 0%, #f5efe5 100%);
      border-radius: 18px;
      box-shadow: 0 10px 30px rgba(31, 35, 40, 0.06);
    }}
    .eyebrow {{ margin: 0 0 10px; color: var(--accent); font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: 34px; line-height: 1.2; }}
    .meta {{ margin-top: 12px; color: var(--muted); font-size: 15px; }}
    .section {{
      margin-top: 22px;
      padding: 24px 28px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: 0 8px 24px rgba(31, 35, 40, 0.04);
    }}
    .alert {{ border-color: rgba(185, 28, 28, 0.35); background: #fff8f4; }}
    h2 {{ margin: 0 0 16px; font-size: 24px; line-height: 1.3; }}
    h3 {{ margin: 0 0 8px; font-size: 19px; line-height: 1.35; }}
    p, ul {{ margin: 0 0 14px; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 8px; }}
    .entry {{ margin-bottom: 18px; padding-bottom: 16px; border-bottom: 1px solid #ebe3d7; }}
    .entry:last-child {{ margin-bottom: 0; padding-bottom: 0; border-bottom: 0; }}
    .topic-index {{ margin:0 0 4px; color:var(--accent); font-size:13px; letter-spacing:0.08em; font-weight:bold; }}
    .topic-summary p {{ margin-bottom: 10px; }}
    .topic-meta {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
    .chips {{ margin-top: 10px; }}
    .chip {{ display:inline-block; margin:0 6px 6px 0; padding:2px 8px; border:1px solid var(--border); border-radius:999px; color:var(--muted); font-size:12px; background:#f7f1e7; }}
    code {{ padding: 0.1em 0.35em; background: #f1ece2; border-radius: 4px; font-size: 0.92em; }}
    a {{ color: var(--accent); }}
    strong {{ color: var(--ink); }}
    .footer {{ margin-top: 20px; color: var(--muted); font-size: 14px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <p class="eyebrow">Global Hot Trends Brief</p>
      <h1>{html_lib.escape(report_title)}</h1>
      <div class="meta">{today} · 社会 / 游戏 / 经济 / 科技 · NotebookLM 聚合生成</div>
    </section>
{failed_section}{infographic_section}{audio_section}{''.join(topic_sections)}{refs_section}
    <section class="section">
      <h2>链接与说明</h2>
      {notebook_link}
      <p class="footer">本简报由 AI 自动生成，用于趋势观察和信息参考。</p>
    </section>
  </div>
</body>
</html>"""


def render_briefing_email_html(data: Dict) -> str:
    full_html = render_briefing_html(data)
    match = re.search(r"(?is)<body[^>]*>(.*)</body>", full_html)
    if not match:
        return full_html
    body_inner = match.group(1).strip()
    style_match = re.search(r"(?is)<style>(.*)</style>", full_html)
    styles = style_match.group(1).strip() if style_match else ""
    style_block = f"<style>{styles}</style>" if styles else ""
    return style_block + body_inner

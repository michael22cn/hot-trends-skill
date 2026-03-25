import html as html_lib
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


def render_briefing_html(data: Dict) -> str:
    today = date.today().strftime("%Y年%m月%d日")
    report_title = data.get("title", "全球热点日报")
    topics = data.get("topics", [])
    references = data.get("references", [])
    infographic_path = data.get("infographic_path", "")
    failed_platforms = data.get("failed_platforms", [])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_lib.escape(report_title)} | {today}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html {{
    background: #0f0f0f;
    color-scheme: dark;
  }}
  body {{
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif;
    background: #0f0f0f;
    color: #e0e0e0;
    line-height: 1.65;
    padding: 40px 20px;
    margin: 0;
  }}
  .mail-bg {{
    width: 100%;
    background: #0f0f0f;
    padding: 32px 0;
  }}
  .wrap {{
    max-width: 620px;
    margin: 0 auto;
    background: #0f0f0f;
  }}
  .hero {{
    background: #181818;
    border: 1px solid #2a2a2a;
    padding: 22px 20px;
    margin-bottom: 32px;
  }}
  .hero h1 {{ font-size: 24px; font-weight: 400; color: #f0f0f0; margin-bottom: 6px; }}
  .hero .sub {{ font-size: 14px; color: #555; }}
  .section-head {{
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #f0c040;
    border-bottom: 2px solid #f0c040;
    padding-bottom: 6px;
    margin-bottom: 18px;
  }}
  .item {{
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px solid #1a1a1a;
  }}
  .item:last-child {{ border-bottom: none; }}
  .item .platform {{
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #f0c040;
    margin-bottom: 3px;
  }}
  .item .title {{
    font-size: 18px;
    font-weight: 600;
    color: #e8e8e8;
    margin-bottom: 5px;
  }}
  .item .summary {{
    font-size: 15px;
    color: #888;
    line-height: 1.6;
  }}
  .hero-media {{
    margin-top: 18px;
    border: 1px solid #2d2d2d;
    background: #121212;
    padding: 12px;
  }}
  .hero-media img {{
    width: 100%;
    display: block;
    border: 1px solid #202020;
  }}
  .hero-media .label {{
    font-size: 12px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #f0c040;
    margin-bottom: 10px;
  }}
  .rich-h2, .rich-h3 {{
    color: #f3f3f3;
    margin: 18px 0 8px;
    line-height: 1.35;
  }}
  .rich-h2 {{ font-size: 20px; font-weight: 700; }}
  .rich-h3 {{ font-size: 17px; font-weight: 700; }}
  .rich-p {{
    font-size: 15px;
    color: #b6b6b6;
    line-height: 1.72;
    margin: 0 0 10px;
  }}
  .rich-bullet {{
    font-size: 15px;
    color: #d2d2d2;
    line-height: 1.68;
    margin: 10px 0;
    padding: 10px 12px;
    border-left: 3px solid #f0c040;
    background: rgba(240,192,64,0.08);
  }}
  .rich-divider {{
    height: 1px;
    background: linear-gradient(90deg, transparent, #3a3a3a, transparent);
    margin: 20px 0;
  }}
  strong {{ color: #f6e3a1; font-weight: 700; }}
  .footer {{
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid #2a2a2a;
    font-size: 13px;
    color: #444;
  }}
  .refs-grid {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px 14px;
  }}
  .refs-head {{
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #f0c040;
    margin-bottom: 12px;
  }}
  .ref-item {{
    font-size: 14px;
    color: #9c9c9c;
    line-height: 1.6;
    margin-bottom: 0;
    white-space: nowrap;
  }}
  .failed-platforms {{
    margin-bottom: 24px;
  }}
  .failed-item {{
    background: rgba(239,68,68,0.12);
    border: 1px solid rgba(239,68,68,0.3);
    color: #f87171;
    font-size: 14px;
    padding: 8px 12px;
    border-radius: 6px;
    margin-bottom: 6px;
  }}
</style>
</head>
<body>
<div class="mail-bg">
<div class="wrap">
  <div class="failed-platforms">
"""

    for fp in failed_platforms:
        html += f'    <div class="failed-item" style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#f87171;font-size:14px;padding:8px 12px;border-radius:6px;margin-bottom:6px;">⚠️ {html_lib.escape(fp)} 数据异常</div>\n'

    html += "  </div>\n"
    html += f"""  <div class="hero">
    <h1 style="font-size:24px;font-weight:400;color:#f0f0f0;margin-bottom:6px;">{html_lib.escape(report_title)}</h1>
    <div class="sub" style="font-size:14px;color:#555;">全球热点简报 · 由 AI 自动生成</div>
"""
    if infographic_path:
        safe_path = html_lib.escape(infographic_path)
        html += f"""
    <div class="hero-media">
      <div class="label" style="font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:#f0c040;margin-bottom:10px;">Infographic</div>
      <img src="{safe_path}" alt="infographic" />
    </div>
"""
    html += f"""
  </div>

"""

    for category, category_topics in _group_topics_by_category(topics):
        html += f"""  <div class="section-head" style="font-size:13px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#f0c040;border-bottom:2px solid #f0c040;padding-bottom:6px;margin-bottom:18px;">{html_lib.escape(category)}热点</div>
"""
        for i, topic in enumerate(category_topics, 1):
            summary_html = _summary_to_html(topic.get("summary", ""))
            citations = " ".join(f'[{num}]' for num in topic.get("citations", [])[:6])
            citations_html = f'<div class="platform" style="font-size:12px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#f0c040;margin-bottom:3px;">{html_lib.escape(citations)}</div>' if citations else ""
            html += f"""  <div class="item">
    <div class="title" style="font-size:18px;font-weight:600;color:#e8e8e8;margin-bottom:5px;">{i}. {topic.get('title', '')}</div>
    <div class="summary" style="font-size:15px;color:#888;line-height:1.6;">{summary_html}</div>
    {citations_html}
  </div>
"""

    if references:
        html += """  <div class="footer">
    <div class="refs-head" style="font-size:13px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#f0c040;margin-bottom:12px;">引用说明</div>
    <div class="refs-grid">
"""
        for ref in references:
            label = html_lib.escape(ref.get("label", ""))
            excerpt = html_lib.escape((ref.get("excerpt", "") or "").strip())
            suffix = f" | {excerpt}" if excerpt else ""
            html += f'    <div class="ref-item" style="font-size:14px;color:#9c9c9c;line-height:1.6;margin-bottom:0;white-space:nowrap;">{ref.get("number")}、{label}{suffix}</div>\n'
        html += "    </div>\n  </div>\n"

    html += """
</div>
</div>
</body>
</html>"""

    return html


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

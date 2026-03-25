import os
import re
import shutil
import subprocess
import tempfile
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple


CATEGORY_SPECS = [
    ("social", "社会"),
    ("economy", "经济"),
    ("technology", "科技"),
    ("gaming", "游戏"),
]


def _find_skill_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _log(stage: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def _clean_text(text: str) -> str:
    text = (text or "").strip()
    return re.sub(r"\s+", " ", text)


def _item_title(item: Dict) -> str:
    return _clean_text(item.get("title") or item.get("name") or item.get("description") or "")


def _clean_reddit_title(title: str) -> str:
    title = re.sub(r"\s*\[[^\]]+\]\s*", " ", title)
    title = re.sub(r"https?://\S+", "", title)
    title = title.replace("Reddit", "").replace("/r/", "r/")
    return _clean_text(title)[:220]


def _ensure_nlm_available() -> None:
    if shutil.which("nlm") is None:
        raise RuntimeError("NotebookLM CLI 不可用：未找到 `nlm` 命令")
    _log("NLM", f"cli detected: {shutil.which('nlm')}")


def _nlm_base_cmd() -> List[str]:
    return [shutil.which("nlm") or "nlm"]


def _run_nlm(args: List[str], timeout: int = 180) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{os.path.join(_find_skill_root(), '.venv', 'bin')}:{env.get('PATH', '')}"
    profile = os.getenv("HOT_TRENDS_NLM_PROFILE", "").strip()
    cmd = _nlm_base_cmd() + args
    if profile:
        cmd.extend(["--profile", profile])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _extract_uuid(text: str) -> Optional[str]:
    match = re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        text,
        re.IGNORECASE,
    )
    return match.group(0) if match else None


def _extract_answer_text(payload):
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        for item in payload:
            answer = _extract_answer_text(item)
            if answer:
                return answer
        return None
    if isinstance(payload, dict):
        for key in ("answer", "response", "text", "content", "message"):
            if key in payload:
                answer = _extract_answer_text(payload[key])
                if answer:
                    return answer
        for value in payload.values():
            answer = _extract_answer_text(value)
            if answer:
                return answer
    return None


def _extract_query_value(payload):
    if isinstance(payload, dict) and isinstance(payload.get("value"), dict):
        return payload["value"]
    return payload if isinstance(payload, dict) else {}


def _extract_json_blob(text: str):
    text = (text or "").strip()
    if not text:
        return None
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _extract_reference_rows(value: Dict, source_id_to_title: Dict[str, str]) -> List[Dict]:
    references = []
    for ref in value.get("references", []) if isinstance(value, dict) else []:
        source_id = ref.get("source_id", "")
        citation_number = ref.get("citation_number")
        if citation_number is None:
            continue
        references.append(
            {
                "number": citation_number,
                "label": _short_reference_label(source_id_to_title.get(source_id, source_id or "unknown source")),
                "excerpt": "",
            }
        )
    references.sort(key=lambda item: item["number"])
    return references


def _citation_tokens(text: str) -> List[int]:
    numbers = []
    for raw in re.findall(r"\[([0-9,\-\s]+)\]", text or ""):
        for part in re.split(r"\s*,\s*", raw.strip()):
            if not part:
                continue
            if "-" in part:
                bounds = [token.strip() for token in part.split("-", 1)]
                if len(bounds) == 2 and all(token.isdigit() for token in bounds):
                    start, end = int(bounds[0]), int(bounds[1])
                    if start <= end:
                        numbers.extend(range(start, end + 1))
                    else:
                        numbers.extend(range(end, start + 1))
                continue
            if part.isdigit():
                numbers.append(int(part))
    return numbers


def _replace_citation_block(match: re.Match, number_map: Dict[int, int]) -> str:
    original = match.group(1)
    mapped = []
    for number in _citation_tokens(f"[{original}]"):
        new_number = number_map.get(number)
        if new_number is not None and new_number not in mapped:
            mapped.append(new_number)
    if not mapped:
        return ""
    return "[" + ", ".join(str(number) for number in mapped) + "]"


def _normalize_reference_numbers(text: str, number_map: Dict[int, int]) -> str:
    updated = re.sub(r"\[([0-9,\-\s]+)\]", lambda match: _replace_citation_block(match, number_map), text or "")
    updated = re.sub(r"\s{2,}", " ", updated)
    updated = re.sub(r"\s+([。；，])", r"\1", updated)
    return updated.strip()


def _dedupe_and_remap_references(references: List[Dict], topics: List[Dict], observation: str) -> Tuple[List[Dict], List[Dict], str]:
    label_to_number = {}
    old_to_new = {}
    normalized_references = []

    for ref in sorted(references, key=lambda item: item["number"]):
        label = ref.get("label", "")
        if label not in label_to_number:
            label_to_number[label] = len(label_to_number) + 1
            normalized_references.append({"number": label_to_number[label], "label": label, "excerpt": ""})
        old_to_new[ref["number"]] = label_to_number[label]

    for topic in topics:
        topic["summary"] = _normalize_reference_numbers(topic.get("summary", ""), old_to_new)
        topic["citations"] = sorted({old_to_new[number] for number in _citation_tokens(topic.get("summary", "")) if number in old_to_new})

    normalized_observation = _normalize_reference_numbers(observation, old_to_new)
    return normalized_references, topics, normalized_observation


def _scratch_notebook_title() -> str:
    return f"每日热搜简报{date.today().isoformat()}"


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "source"


def _short_reference_label(label: str) -> str:
    match = re.match(r"^([a-z_]+)(?:-[a-z0-9_]+)?-\d{4}-\d{2}-\d{2}(?:-part-(\d+))?$", label)
    if not match:
        return label
    platform, part = match.groups()
    return f"{platform}-{part}" if part else platform


def _platform_source_blocks(items: List[Dict]) -> List[Dict[str, str]]:
    grouped: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for item in items:
        platform = item.get("platform", "unknown")
        if platform == "youtube":
            channel = "all"
        else:
            channel = item.get("channel", "main")
        grouped[(platform, channel)].append(item)

    blocks = []
    for (platform, channel), platform_items in grouped.items():
        chunk_size = 20 if platform == "reddit" else len(platform_items)
        for chunk_index in range(0, len(platform_items), chunk_size):
            chunk = platform_items[chunk_index : chunk_index + chunk_size]
            lines = [f"# {platform} 热搜", f"channel={channel}", ""]
            for idx, item in enumerate(chunk, 1):
                title = _item_title(item)
                if platform == "reddit":
                    title = _clean_reddit_title(title)
                if not title:
                    continue
                meta = []
                for key in ("hot", "score", "channel", "source", "language", "published", "author", "comments"):
                    value = item.get(key)
                    if value:
                        meta.append(f"{key}={value}")
                line = f"{idx}. {title}" if title else f"{idx}."
                if meta:
                    line = f"{line} | {'; '.join(str(x) for x in meta)}"
                lines.append(line)

            suffix = ""
            if chunk_size < len(platform_items):
                suffix = f"-part-{(chunk_index // chunk_size) + 1}"
            safe_channel = _safe_slug(channel).replace("-", "_")
            title = f"{platform}-{safe_channel}-{date.today().isoformat()}{suffix}"
            blocks.append({"title": title, "slug": _safe_slug(title), "text": "\n".join(lines)})

    return blocks


def _write_platform_source_files(items: List[Dict]) -> List[Dict[str, str]]:
    file_specs = []
    temp_dir = tempfile.mkdtemp(prefix="hot-trends-nlm-")
    _log("NLM", f"preparing source files in {temp_dir}")
    for block in _platform_source_blocks(items):
        path = os.path.join(temp_dir, f"{block['slug']}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(block["text"])
        file_specs.append({"title": block["slug"], "path": path, "display_title": block["title"]})
        _log("NLM", f"source file ready: {block['title']} -> {path}")
    return file_specs


def _nlm_source_upload_workers(file_specs: List[Dict[str, str]]) -> int:
    if not file_specs:
        return 0
    configured = os.getenv("HOT_TRENDS_NLM_SOURCE_CONCURRENCY", "").strip()
    if configured:
        try:
            return max(1, min(int(configured), len(file_specs)))
        except ValueError:
            pass
    return len(file_specs)


def _add_source_to_notebook(notebook_id: str, block: Dict[str, str]) -> Dict[str, str]:
    _log("NLM", f"upload start: {block['display_title']} -> notebook {notebook_id}")
    add_proc = _run_nlm(
        [
            "source",
            "add",
            notebook_id,
            "--file",
            block["path"],
            "--title",
            block["title"],
            "--wait",
        ],
        timeout=240,
    )
    if add_proc.returncode != 0:
        raise RuntimeError(
            f"NotebookLM 添加 source 失败({block['display_title']}): "
            f"{add_proc.stderr.strip() or add_proc.stdout.strip()}"
        )
    source_id = _extract_source_id(add_proc.stdout + "\n" + add_proc.stderr)
    result = dict(block)
    result["source_id"] = source_id or ""
    _log("NLM", f"upload done: {block['display_title']} source_id={result['source_id'] or 'unknown'}")
    return result


def _build_category_prompt(category_label: str) -> str:
    return f"""你是一名中文热点编辑。请只基于当前 notebook 中的 source，筛选并归纳“{category_label}”领域的热门话题。

输出要求：
1. 只输出 JSON，不要 markdown，不要代码块。
2. JSON 结构必须为：
{{
  "category": "{category_label}",
  "topics": [
    {{
      "title": "话题标题",
      "summary": "50-100字中文摘要，保留引用编号如[1][2]",
      "sources": ["平台:channel", "平台:channel"]
    }}
  ]
}}
3. `topics` 返回 5-10 条，按热度和代表性排序。
4. 只保留属于“{category_label}”领域的话题，不要混入其他领域。
5. `summary` 必须是信息密度高的完整陈述，不要写成口号，不要分点。
6. `sources` 只写 source 中真实出现的平台或 channel，优先保留子来源，如 `reddit:r/technology`、`weibo:main_hot_search`。
7. 不要输出中外观点差异、延伸分析、整体观察，也不要编造 source 中没有的信息。
"""


def _extract_source_id(text: str) -> Optional[str]:
    match = re.search(r"Source ID:\s*([0-9a-f-]{36})", text, re.IGNORECASE)
    return match.group(1) if match else None


def _citation_numbers(text: str) -> List[int]:
    return sorted({int(match) for match in re.findall(r"\[(\d+)\]", text or "")})


def _parse_category_response(answer: str, category_label: str) -> List[Dict]:
    payload = _extract_json_blob(answer)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{category_label} 话题归纳结果不是有效 JSON")

    topics = []
    for raw in payload.get("topics", []) if isinstance(payload.get("topics"), list) else []:
        if not isinstance(raw, dict):
            continue
        heading = _clean_text(raw.get("title", ""))
        body = _clean_text(raw.get("summary", ""))
        if not heading or not body:
            continue
        sources = []
        if isinstance(raw.get("sources"), list):
            sources = [str(item).strip() for item in raw.get("sources", []) if str(item).strip()]
        topics.append(
            {
                "category": category_label,
                "title": heading,
                "summary": body,
                "sources": sources,
                "citations": _citation_numbers(body),
            }
        )

    return topics


def _infographic_focus_title() -> str:
    return date.today().strftime("%Y年%m月%d日全球热点一览")


def _start_infographic_generation(notebook_id: str) -> Optional[str]:
    create_proc = _run_nlm(
        [
            "infographic",
            "create",
            notebook_id,
            "--language",
            "zh-CN",
            "--style",
            "editorial",
            "--detail",
            "concise",
            "--focus",
            _infographic_focus_title(),
            "--confirm",
        ],
        timeout=180,
    )
    if create_proc.returncode != 0:
        _log("NLM", f"infographic create failed: {create_proc.stderr.strip() or create_proc.stdout.strip()}")
        return None
    return _extract_uuid(create_proc.stdout + "\n" + create_proc.stderr)


def _artifact_status(notebook_id: str, artifact_id: str) -> Optional[str]:
    status_proc = _run_nlm(["status", "artifacts", notebook_id, "--json"], timeout=120)
    if status_proc.returncode != 0:
        return None
    try:
        payload = json.loads(status_proc.stdout)
    except Exception:
        return None
    for item in payload if isinstance(payload, list) else []:
        if item.get("id") == artifact_id:
            return item.get("status")
    return None


def _wait_and_download_infographic(notebook_id: str, artifact_id: str, output_dir: str) -> Optional[str]:
    wait_seconds = int(os.getenv("HOT_TRENDS_NLM_INFOGRAPHIC_WAIT", "900"))
    poll_seconds = int(os.getenv("HOT_TRENDS_NLM_INFOGRAPHIC_POLL", "10"))
    deadline = time.time() + wait_seconds
    output_path = os.path.join(output_dir, f"{notebook_id}_infographic.png")

    while time.time() < deadline:
        status = _artifact_status(notebook_id, artifact_id)
        if status == "completed":
            download_proc = _run_nlm(
                [
                    "download",
                    "infographic",
                    notebook_id,
                    "--id",
                    artifact_id,
                    "--output",
                    output_path,
                    "--no-progress",
                ],
                timeout=240,
            )
            if download_proc.returncode == 0 and os.path.exists(output_path):
                return output_path
            return None
        if status in {"failed", "error"}:
            return None
        time.sleep(poll_seconds)

    return None


def _query_via_nlm(items: List[Dict]) -> Dict:
    notebook_id = os.getenv("HOT_TRENDS_NLM_NOTEBOOK_ID", "").strip()
    created_notebook = False
    query_timeout = int(os.getenv("HOT_TRENDS_NLM_QUERY_TIMEOUT", "480"))
    query_subprocess_timeout = int(os.getenv("HOT_TRENDS_NLM_QUERY_PROCESS_TIMEOUT", "600"))

    _ensure_nlm_available()
    _log("NLM", f"aggregation start with {len(items)} input items")

    try:
        if not notebook_id:
            _log("NLM", "creating scratch notebook")
            create_proc = _run_nlm(["notebook", "create", _scratch_notebook_title()])
            if create_proc.returncode != 0:
                raise RuntimeError(
                    f"NotebookLM 创建 notebook 失败: {create_proc.stderr.strip() or create_proc.stdout.strip()}"
                )
            notebook_id = _extract_uuid(create_proc.stdout + "\n" + create_proc.stderr)
            if not notebook_id:
                raise RuntimeError("NotebookLM 创建 notebook 后未返回 notebook id")
            created_notebook = True
            _log("NLM", f"scratch notebook created: {notebook_id}")
        else:
            _log("NLM", f"using existing notebook: {notebook_id}")

        file_specs = _write_platform_source_files(items)
        if not file_specs:
            raise RuntimeError("没有可上传到 NotebookLM 的平台热搜文件")

        upload_workers = _nlm_source_upload_workers(file_specs)
        _log("NLM", f"upload plan: {len(file_specs)} sources, workers={upload_workers}")
        source_id_to_title = {}
        with ThreadPoolExecutor(max_workers=upload_workers) as pool:
            futures = {
                pool.submit(_add_source_to_notebook, notebook_id, block): block["display_title"]
                for block in file_specs
            }
            for future in as_completed(futures):
                display_title = futures[future]
                block = future.result()
                if block.get("source_id"):
                    source_id_to_title[block["source_id"]] = display_title
                _log("NLM", f"source registered: {display_title}")

        _log("NLM", "start infographic generation")
        infographic_artifact_id = _start_infographic_generation(notebook_id)

        topics = []
        references = []
        for category_key, category_label in CATEGORY_SPECS:
            _log("QUERY", f"start {category_key} query: notebook={notebook_id} timeout={query_timeout}s")
            summary_proc = _run_nlm(
                [
                    "notebook",
                    "query",
                    notebook_id,
                    _build_category_prompt(category_label),
                    "--json",
                    "--timeout",
                    str(query_timeout),
                ],
                timeout=query_subprocess_timeout,
            )
            if summary_proc.returncode != 0:
                raise RuntimeError(
                    f"NotebookLM {category_label}话题归纳失败: {summary_proc.stderr.strip() or summary_proc.stdout.strip()}"
                )

            payload = json.loads(summary_proc.stdout)
            value = _extract_query_value(payload)
            answer = _extract_answer_text(value) or ""
            if not answer:
                raise RuntimeError(f"NotebookLM {category_label}话题归纳返回为空")
            _log("QUERY", f"{category_key} query completed, answer_length={len(answer)}")

            references.extend(_extract_reference_rows(value, source_id_to_title))
            category_topics = _parse_category_response(answer, category_label)
            if not category_topics:
                raise RuntimeError(f"NotebookLM 未返回可解析的{category_label}话题结构")
            topics.extend(category_topics)

        references, topics, _ = _dedupe_and_remap_references(references, topics, "")
        title = f"全球热点分类简报 | {date.today().strftime('%Y年%m月%d日')}"
        _log("QUERY", f"parsed briefing: title={title}, topics={len(topics)}, references={len(references)}")

        infographic_path = ""
        if infographic_artifact_id:
            _log("NLM", f"waiting for infographic artifact: {infographic_artifact_id}")
            infographic_path = _wait_and_download_infographic(notebook_id, infographic_artifact_id, tempfile.gettempdir()) or ""
            if infographic_path:
                _log("NLM", f"infographic downloaded: {infographic_path}")
            else:
                _log("NLM", "infographic unavailable")

        return {
            "title": title,
            "topics": topics,
            "references": references,
            "infographic_path": infographic_path,
            "notebook_id": notebook_id,
            "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}" if notebook_id else "",
        }
    finally:
        if created_notebook and notebook_id and os.getenv("HOT_TRENDS_NLM_KEEP_NOTEBOOK", "1") == "0":
            try:
                _log("NLM", f"deleting scratch notebook: {notebook_id}")
                _run_nlm(["delete", "notebook", notebook_id, "--confirm"], timeout=120)
            except Exception:
                pass


def aggregate_topics(all_items: List[Dict], failed_platforms: List[str] = None) -> Dict:
    if not all_items:
        return {"topics": [], "failed_platforms": failed_platforms or []}

    _log("RUN", f"aggregate_topics called with items={len(all_items)} failed_platforms={len(failed_platforms or [])}")
    parsed = _query_via_nlm(all_items)
    _log("RUN", "NotebookLM aggregation completed successfully")
    parsed["failed_platforms"] = failed_platforms or []
    return parsed

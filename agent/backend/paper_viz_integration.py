"""将可视化集成到论文生成流程（数据驱动版）

从各章节已生成的 Markdown 表格里解析数值，自动成图并插回正文，
使 Results / Sensitivity / Solve 等章节带上真实数据图表，而非纯文本。
"""
import re
from typing import Dict, List, Optional

from .paper_viz import PaperVisualizer


def _parse_markdown_tables(content_md: str) -> List[Dict]:
    """从 Markdown 正文中解析出所有表格，带行号区间。

    返回 [{"headers", "rows", "start_line", "end_line"}, ...]。
    start_line/end_line 为 0-based 行号，end_line 是表格结束后一行（开区间）。
    """
    lines = content_md.split("\n")
    tables = []
    i, n = 0, len(lines)
    while i < n:
        if "|" not in lines[i]:
            i += 1
            continue
        start = i
        while i < n and "|" in lines[i]:
            i += 1
        end = i
        parsed = _parse_table_block(lines[start:end])
        if parsed:
            parsed["start_line"] = start
            parsed["end_line"] = end
            tables.append(parsed)
    return tables


def _parse_table_block(block: List[str]) -> Optional[Dict]:
    """把连续的 | 行解析为 {headers, rows}；分隔行（---）跳过。"""

    def _cells(line: str) -> List[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    body = []
    for raw in block:
        if "---" in raw and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", raw):
            continue  # 分隔行
        body.append(_cells(raw))

    if len(body) < 2:  # 至少表头 + 1 行数据
        return None
    headers = body[0]
    if not headers or all(not h for h in headers):
        return None

    ncol = len(headers)
    rows = []
    for r in body[1:]:
        r = r[:ncol]
        r += [""] * (ncol - len(r))
        rows.append(r)
    return {"headers": headers, "rows": rows}


def add_visualizations_to_section(
    section_key: str,
    content_md: str,
    heading: str = "",
) -> str:
    """为指定章节注入数据驱动图表（非致命，失败返回原样）。

    只处理图表价值高的章节；对正文里的每张 Markdown 表格，若含数值列则成图，
    紧跟表格插入。无法成图时保持原文不变。
    """
    if section_key not in ("solve", "analyze", "sensitivity"):
        return content_md
    if not content_md or "|" not in content_md:
        return content_md

    viz = PaperVisualizer()
    tables = _parse_markdown_tables(content_md)
    if not tables:
        return content_md

    lines = content_md.split("\n")
    insertions = []  # [(insert_after_line_index, chart_md)]
    for t in tables:
        try:
            chart_md = viz.chart_table(t["headers"], t["rows"])
        except Exception:
            chart_md = ""
        if not chart_md:
            continue
        insertions.append((t["end_line"], chart_md.rstrip()))

    if not insertions:
        return content_md

    # 从后往前插入，避免行号偏移
    for pos, chart_md in sorted(insertions, key=lambda x: -x[0]):
        lines.insert(pos, "")
        lines.insert(pos + 1, chart_md)

    return "\n".join(lines)

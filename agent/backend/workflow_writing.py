"""Evidence-grounded draft material and resumable confirmed-paper generation.

Draft material is deliberately assembled from source fields without synthesizing
new numerical claims. Academic prose is generated only from a confirmed snapshot.
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from urllib.parse import quote, urlparse

from . import literature_search, paper_db, paper_gen, paper_literature, solve_flow, solve_sessions
from .config import config


def _quote_text(text):
    return "\n".join("> " + line for line in str(text).splitlines())


def _source_url(paper):
    for field in ("url", "pdf_url"):
        value = paper.get(field) or ""
        if urlparse(value).scheme in {"https", "http"} and urlparse(value).netloc:
            return value
    identifier = quote(str(paper.get("id") or paper.get("ext_id") or ""), safe="")
    if paper.get("source") == "arxiv":
        return "https://arxiv.org/abs/" + identifier
    if paper.get("source") == "semantic_scholar":
        return "https://www.semanticscholar.org/paper/" + identifier
    return ""


def _citations(papers):
    result, seen = [], set()
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        identifier = str(paper.get("id") or paper.get("ext_id") or "")
        source = paper.get("source")
        identity = (source, identifier)
        if (source not in {"arxiv", "semantic_scholar"} or not identifier
                or not paper.get("title") or identity in seen):
            continue
        seen.add(identity)
        result.append({"id": identifier, "source": source, "title": paper["title"],
                       "authors": paper.get("authors") or [],
                       "published": paper.get("published") or str(paper.get("year") or ""),
                       "abstract": paper.get("abstract") or "", "url": _source_url(paper),
                       "pdf_url": paper.get("pdf_url"), "source_kind": "abstract"})
    return result


def draft_material(session, step_id, notes, evidence, literature_query=""):
    """Return an editable Markdown draft, source metadata, and nonfatal warnings."""
    steps = session.get("problem", {}).get("steps", [])
    step = next((s for s in steps if s.get("id") == step_id), None)
    if step is None:
        raise ValueError("建模步骤不存在")
    key = step.get("stage_key") or "build"
    parts = [f"## {step.get('title') or paper_gen.section_heading(key)}（待审草稿）",
             "本节整理已记录的思路和验证材料，研究判断仍需学生确认。"]
    warnings, citations = [], []
    if session.get("initial_thought"):
        parts.extend(["### 学生原始思路", _quote_text(session["initial_thought"])])
    if isinstance(notes, dict):
        notes = notes.get("items", notes.get("notes", [notes]))
    notes = notes if isinstance(notes, list) else []
    relevant_notes = [n for n in notes if not isinstance(n, dict)
                      or n.get("step_id", step_id) == step_id]
    if relevant_notes:
        parts.append("### 讨论记录与学习笔记")
        for note in relevant_notes:
            if not isinstance(note, dict):
                parts.append(_quote_text(note))
                continue
            origin = note.get("origin") or note.get("source") or note.get("attribution") or note.get("role") or "未标注"
            label = "AI 建议（待学生判断）" if str(origin) in {"assistant", "ai", "ai_proposal"} else f"笔记来源：{origin}"
            parts.append(f"**{note.get('title') or label}**")
            original = note.get("student_original") or note.get("original_quote") or note.get("quote")
            if original:
                parts.append("原话：\n" + _quote_text(original))
            for field in ("text", "summary", "idea", "content", "reason", "basis", "limits", "assumptions", "questions"):
                if note.get(field) and note[field] != original:
                    parts.append(_quote_text(note[field]))
            if note.get("source_message_ids"):
                parts.append("对话出处：" + ", ".join(map(str, note["source_message_ids"])))
    else:
        warnings.append("本步尚无结构化笔记；已保留学生原始思路。")

    records = [e for e in (evidence or []) if isinstance(e, dict)
               and e.get("step_id", step_id) == step_id]
    # A restrained academic paragraph, followed by the exact source appendix.
    # Template composition cannot introduce new model choices or computed values.
    thought = session.get("initial_thought") or "题目提出的建模问题"
    paragraph = f"本节围绕“{step.get('title') or paper_gen.section_heading(key)}”开展建模讨论。学生提出的初始研究思路为“{thought}”，该思路作为后续验证的出发点予以保留。"
    purposes = list(dict.fromkeys(str(e["purpose"]) for e in records if e.get("purpose")))
    if purposes:
        paragraph += "本轮验证目的为“" + "；".join(purposes) + "”。"
    successful = [e for e in records if e.get("status") == "success"]
    if successful:
        paragraph += "已归档的计算记录显示程序完成执行，原始输出见下文相应验证记录。执行完成仅说明计算过程结束，不能单独证明模型假设、结论可靠性或实际适用性；这些判断仍需对照研究目标和验证依据。"
    elif records:
        paragraph += "现有材料尚未提供成功计算的记录，因此本节保留已取得的依据与运行诊断，不据此宣称完成数值验证。"
    else:
        paragraph += "目前尚未归档计算验证材料，因此研究结论仍处于待验证状态，本节不报告未经计算的数值结果。"
    paragraph += "该草稿保留学生原始判断，相关结论将在学生审阅证据、说明局限后确认。"
    source_ids = [str(e.get("id")) for e in records if e.get("id")]
    if source_ids:
        paragraph += " 验证出处：" + "、".join(source_ids) + "。"
    parts.extend(["### 论文段落草稿", paragraph])
    parts.append("### 计算验证与依据")
    if not records:
        parts.append("尚无本步运行或外部依据，不能报告计算结果或认定假设成立。")
        warnings.append("尚无本步验证依据，论文草稿不生成数值结果。")
    for record in records:
        status = record.get("status") or "未标注"
        parts.append(f"**验证记录 {record.get('id', '')}** · 来源：{record.get('source', '未标注')} · 状态：{status}")
        if record.get("purpose"):
            parts.append("验证目的：" + str(record["purpose"]))
        if record.get("stdout"):
            output = str(record["stdout"])
            fence = "`" * max(3, max((len(x) + 1 for x in re.findall(r"`+", output)), default=3))
            parts.append(f"原始输出：\n\n{fence}text\n{output}\n{fence}")
        if record.get("stderr"):
            parts.append("运行诊断：\n" + _quote_text(record["stderr"]))
        if status != "success":
            parts.append("此记录未报告成功执行，不应写成已验证的计算结论。")
        parts.append("执行成功不等于假设成立；需结合验证目标、对照结果和适用条件作出判断。")
    if session.get("is_example"):
        parts.append("本项目为示例项目，数据具有模拟性质，不能替代真实观测。")
    parts.extend(["### 局限与待确认事项", "以上材料仍需检查数据来源、模型假设与结论之间的对应关系；缺失依据应保留为待验证事项。"])

    if literature_query.strip():
        try:
            citations = _citations(literature_search.search_literature(literature_query.strip(), max_per_source=3))
            if not citations:
                warnings.append("文献检索未返回可核验来源；草稿已保留，可稍后重试检索。")
        except Exception:
            warnings.append("文献检索暂时失败；草稿和验证材料已保留。")
    if citations:
        parts.append("### 文献依据（检索摘要，尚未读取全文）")
        for citation in citations:
            parts.append(f"- [{citation['source']}:{citation['id']}] {citation['title']}\n  {citation['url']}")
            parts.append(_quote_text(citation["abstract"]) if citation["abstract"] else "未返回摘要；当前仅可核对题录，不用于支持具体方法或结果。")
    return {"content": "\n\n".join(parts), "citations": citations, "warnings": warnings}


_WORKFLOW_DDL = """
CREATE TABLE IF NOT EXISTS workflow_paper_sections (
    session_id TEXT NOT NULL REFERENCES paper_sessions(session_id),
    section_key TEXT NOT NULL,
    phase TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY(session_id, section_key)
);
"""


def _prepare_literature(pid, session, source, warnings):
    """Save real abstracts as searchable quotes; unavailable literature is nonfatal."""
    with closing(paper_literature.get_lit_conn()) as conn:
        papers = paper_literature.load_papers(conn, pid)
        if not papers:
            collected = []
            # The workflow owns draft artifacts; walk only their citations fields.
            def collect(value):
                if isinstance(value, dict):
                    if isinstance(value.get("citations"), list):
                        collected.extend(value["citations"])
                    for k, child in value.items():
                        if k != "citations" and isinstance(child, (dict, list)):
                            collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)
            collect(session.get("workflow", {}))
            if not collected:
                collected = literature_search.search_literature(source.get("title") or "mathematical modeling", max_per_source=3)
            valid = _citations(collected)
            paper_literature.store_papers(conn, pid, valid)
            papers = paper_literature.load_papers(conn, pid)
        if not papers:
            warnings.append("未取得可核验文献，保留现有论文内容；参考文献待补充。")
        for paper in papers:
            abstract = paper.get("abstract") or ""
            exists = conn.execute("SELECT 1 FROM literature_chunks WHERE session_id=? AND paper_id=? AND content=?",
                                  (pid, paper["paper_id"], abstract)).fetchone()
            if len(abstract) >= 10 and not exists:
                paper_literature._store_chunks(conn, pid, paper["paper_id"], [{"quote": abstract, "relevance": "检索原始摘要，尚未阅读全文"}])
        return paper_literature.build_literature_context(conn, pid, max_chunks=6)


def generate_confirmed_paper(sid, on_event=None, cancel_event=None, allow_partial=False):
    """Resume a frozen paper without overwriting any existing student edits.

    Owned outline revisions may be expanded; any changed or unknown revision is
    preserved. Cancellation is cooperative between generation events/stages.
    allow_partial=True 时只依据学生已确认的步骤成稿，未确认步骤在论文里标注为待补充。
    """
    emit = on_event or (lambda event: None)
    cancelled = lambda: cancel_event is not None and cancel_event.is_set()
    if cancelled():
        return {"paper_session_id": None, "status": "cancelled", "warnings": [],
                "completed_sections": [], "preserved_sections": []}
    created = solve_flow.create_paper(sid, allow_partial=allow_partial)
    pid = created["paper_session_id"]
    warnings, preserved, failed = [], set(), set()
    snapshot = (solve_sessions.load(sid) or {})
    partial_now = solve_flow.partial_status(snapshot)
    if partial_now.get("partial"):
        warnings.append(
            f"本稿只依据已确认的 {partial_now['confirmed']} 步整理；还有 {partial_now['missing']} 步未确认，"
            "未确认的步骤在论文中只作为待补充，不作为结论。")
    emit({"type": "progress", "stage": "paper", "step": "session_created", **created})
    with closing(paper_db.get_conn()) as conn:
        source = solve_flow.paper_source(conn, pid)
        if not source or not source.get("frozen"):
            raise ValueError("正式论文需要已确认并冻结的研究材料")
        conn.executescript(_WORKFLOW_DDL)
        try:
            literature = _prepare_literature(pid, solve_sessions.load(sid) or {}, source, warnings)
        except Exception:
            literature = ""
            warnings.append("文献检索或归档失败，正文仍可继续，参考文献待补充。")

        for phase in ("outline", "full"):
            for key in paper_db.SECTION_ORDER:
                if cancelled():
                    break
                row = paper_db.load_section(conn, pid, key) or {}
                marker = conn.execute("SELECT * FROM workflow_paper_sections WHERE session_id=? AND section_key=?",
                                      (pid, key)).fetchone()
                has_text = bool((row.get("content_md") or "").strip())
                owned_outline = bool(marker and marker["phase"] == "outline" and marker["revision"] == row.get("revision", 0))
                if row.get("status") in {"approved", "skipped"}:
                    continue
                if has_text and (phase == "outline" or not owned_outline):
                    if not owned_outline:
                        preserved.add(key)
                    continue
                if phase == "full" and not has_text:
                    # Failed outline stages are resumed on the next request.
                    continue
                emit({"type": "progress", "stage": "paper", "phase": phase, "section_key": key,
                      "message": f"正在生成{paper_gen.section_heading(key)}{'大纲' if phase == 'outline' else '正文'}"})
                generator = (paper_gen.generate_outline_section(conn, pid, key, literature_context=literature,
                                                                 expected_revision=row.get("revision", 0))
                             if phase == "outline" else
                             paper_gen.generate_section(conn, pid, key, source, expected_revision=row.get("revision", 0)))
                succeeded = False
                try:
                    for event in generator:
                        if event.get("type") in {"outline_section", "result"}:
                            revision = event.get("revision")
                            if revision is not None:
                                conn.execute("INSERT INTO workflow_paper_sections VALUES (?,?,?,?) ON CONFLICT(session_id,section_key) DO UPDATE SET phase=excluded.phase,revision=excluded.revision",
                                             (pid, key, phase, revision))
                                conn.commit()
                            succeeded = True
                            failed.discard(key)
                        if event.get("type") == "error":
                            failed.add(key)
                            warnings.append(f"{key}: {event.get('message', '生成未完成')}")
                        emit({**event, "stage": "paper", "phase": phase, "paper_session_id": pid})
                        if cancelled():
                            break
                except Exception as exc:
                    failed.add(key)
                    warnings.append(f"{key}: {type(exc).__name__}，已保存内容可继续生成。")
                    emit({"type": "error", "stage": "paper", "section_key": key, "message": warnings[-1]})
                finally:
                    generator.close()
                if not succeeded:
                    failed.add(key)
            if cancelled():
                break

        sections = paper_db.list_sections(conn, pid)
        complete = [s["section_key"] for s in sections if s["status"] in {"approved", "skipped"}]
        status = "cancelled" if cancelled() else "needs_review" if preserved else "partial"
        markdown = paper_gen.assemble(conn, pid)
        if not cancelled() and all(key in complete for key in paper_db.SECTION_ORDER):
            try:
                from . import export_docx
                output = export_docx.to_docx(markdown)
                target = config.DATA_DIR / f"paper_{pid}.docx"
                target.write_bytes(output)
                paper_db.mark_complete(conn, pid)
                status = "complete"
            except Exception:
                warnings.append("正文已保存，Word 导出失败；可重试或从论文页导出。")
        result = {**created, "status": status, "completed_sections": complete,
                  "preserved_sections": sorted(preserved), "failed_sections": sorted(failed),
                  "warnings": warnings, "markdown": markdown,
                  "url": f"/paper?session_id={pid}"}
        emit({"type": "paper_ready", "stage": "paper", **result})
        return result

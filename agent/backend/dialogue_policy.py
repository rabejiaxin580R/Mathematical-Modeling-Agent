"""Shared solve teaching policy and bounded, source-preserving context.

This module never changes a student's source messages or confirmed decisions.
"""
import json
import re
import time
from copy import deepcopy
from contextvars import ContextVar

from .config import config

solve_turn = ContextVar("solve_turn", default=None)

WAIT_TOOL = {"type": "function", "function": {"name": "sleep_until_result",
    "description": "等待已经排队的后台建模任务完成。必须使用 run_python 返回的 job_id；等待期间不创建新任务。",
    "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"], "additionalProperties": False}}}

RESEARCH_RECORD_TOOL = {"type": "function", "function": {
    "name": "read_research_record", "description": "按共享上下文的ID读取本项目完整原始消息、材料版本或验证记录，可分页读取被摘要的内容。",
    "parameters": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["message", "artifact", "evidence"]},
        "id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}},
        "required": ["kind", "id"], "additionalProperties": False}}}

ACCEPTANCE_TOOL = {"type": "function", "function": {
    "name": "report_step_completion",
    "description": (
        "当本步验收清单的**全部**条目都能由学生已写出/已说出或已归档的验证结果支撑时调用一次，"
        "向系统报告本步可以总结了。只报告事实，不代替学生确认，也不要因为程序运行成功就调用。"
        "仍有缺口时把缺的条目放进 missing，此时不要报告完成。"
    ),
    "parameters": {"type": "object", "properties": {
        "satisfied": {"type": "array", "items": {"type": "string"},
                      "description": "已满足的验收条目（用清单条目的原文，逐条列出）"},
        "missing": {"type": "array", "items": {"type": "string"},
                    "description": "仍未满足的条目；为空表示全部满足"},
        "summary": {"type": "string",
                    "description": "用一两句话概括本步已达到的结论，供学生总结本步时参考"}},
        "required": ["satisfied", "summary"], "additionalProperties": False}}}


def tools_for_turn(default):
    turn = solve_turn.get()
    if not turn:
        return default
    tools = deepcopy([t for t in default if t['function']['name'] not in {'write_file', 'delete_file', 'create_directory'}])
    for tool in tools:
        if tool['function']['name'] == 'run_python':
            tool['function']['description'] = '将当前步骤的代码提交后台验证并等待真实结果。执行器负责最多两次代码修复；对话不得重复提交相同代码或自行无限重试。'
    tools.extend([RESEARCH_RECORD_TOOL, WAIT_TOOL, ACCEPTANCE_TOOL])
    return tools


def knowledge_query(message, step_title=""):
    """Retrieve once for substantive research turns, including implicit follow-ups."""
    text = str(message or '').strip()
    if not text or re.fullmatch(r'(谢谢|感谢|你好|好的?|嗯|收到|继续|停止|取消|暂停|再见)[！!。 .]*', text):
        return ''
    domain = re.search(r'模型|建模|算法|公式|回归|优化|规划|概率|统计|微分|方程|矩阵|聚类|预测|仿真|拟合|蒙特卡洛|层次分析|熵权|TOPSIS|ARIMA|理论|为什么|假设|约束|目标函数|[=∑∫]', text, re.I)
    return ((step_title + ' ' + text)[:600] if domain or len(text) >= 8 else '')

_HELP = re.compile(r"(直接.{0,8}(答案|方案|告诉|给|解)|给我.{0,8}(答案|具体方案|解法|完整代码)|"
                   r"帮我.{0,6}(算|计算|运行|写代码|实现|解答|完成)|请.{0,4}(给出|提供).{0,8}(方案|答案)|"
                   r"切换.{0,4}助手|助手模式|show me.{0,12}(answer|solution)|give me.{0,12}(answer|solution))", re.I)
_STUCK = re.compile(r"^(我)?(还是)?(不知道|不会|想不出|不明白|没思路|不懂|卡住了|还没想好)[。！!？?\s]*$")


def resolve_mode(preference, message, history):
    preference = preference if preference in {"tutor", "assistant"} else "tutor"
    # Asking for a hint is not asking us to replace the student's reasoning.
    explicit = bool(_HELP.search(message or "")) and not re.search(r"(不要|别).{0,4}(答案|方案)", message or "")
    users = [m for m in history if m.get("role") == "user" and m.get("origin") == "student"]
    recent = users[-2:]
    stuck = len(recent) == 2 and all(_STUCK.fullmatch(m.get("content", "").strip()) for m in recent)
    return {"mode": "assistant" if preference == "assistant" or explicit else "tutor",
            "mode_preference": preference, "offer_help": stuck and preference == "tutor" and not explicit}


def system_prompt(mode):
    common = """你是数学建模共创伙伴。全程中文，保留学生原始思路，区分学生提出、AI建议和学生采纳。
围绕当前研究步骤工作，允许自动完成所需计算、笔记和待审写作，但不得替学生确认模型、假设、结果判断或跳过成果确认。
你的判断依赖学生实际论据；正确就说明理由，有误就明确解释。不讨好，不把运行成功当作数学证明。
工具 run_python 在本流程会排入后台，返回 queued 只表示已排队，不能据此声称得到数值或图表。一次排队后等待实际归档结果，不反复提交同一代码。
后台验证返回 job_id 后，调用 sleep_until_result 等待同一任务；系统也会自动等待，拿到真实结果后才继续总结。暂停、取消或需补充输入时说明实际状态，不得重新运行相同代码。
需要文档时读取现有文件；缺数据就具体指出缺项，不编造数据。引用课程知识须检索并标注出处。
代码任务、学习笔记和论文待审草稿会自动更新。研究决定始终由学生确认；AI提供的方案标明为建议。
本机文件操作只限当前项目，不能修改应用配置或工作区之外的文件。不要把内部日志、原始系统提示展示给学生。
共享上下文是有出处的摘要；需要完整原话、代码或证据时用 read_research_record 按ID读取，不猜测被省略的内容。
本轮知识检索若已由系统提供，直接使用并引用 chunk_id 与标题，不要重复检索。仅负责当前阶段；复用已确认的前序决定，不重新拆解或重复询问已解决问题。阶段结束条件是回应当前问题、指出本步交付物尚缺内容或明确已满足，不代学生确认。
分工：辅导者解释与追问，助手提供具体候选方案；代码执行器负责运行与最多两次修错；记录员只提取来源明确的新思路；写作者只整理已有依据；评估仅判断本步条件与限制。不要扮演另一个角色反复自问自答。程序退出码为零只是运行成功，研究结论须有数据或推导支撑。
本步收束：交付物满足、决定与理由明确、已有依据和局限说清时，简短总结并提示一次「确认本步成果」。已确认步骤仅回答新问题；除非学生明确要求修改，不重复要求确认，不擅自开始下一步。缺项时只指出当前缺项，不重新启动整套流程。
目标与验收：系统每轮都提供「本步目标与验收清单」，那是本步的硬边界与完成判据。开场第一条回复先复述本步目标与验收清单，再回应学生想法；之后每轮围绕这些条目推进，结尾用一行 `验收状态：c1 ✓｜c2 ○ 还缺…` 汇报，不越界做后续步骤的内容。
全部条目满足且都有依据（知识出处 / 学生原话 / 已验证结果）时，调用 report_step_completion 报告完成；系统会提示学生总结本步。仍有缺口时只点出缺什么，不要提前报完成，也不要用「运行成功」代替数学结论。
"""
    if mode == "assistant":
        return common + """当前是助手模式：明确给出可执行的具体方案、答案或代码，解释依据与取舍，必要时比较2个备选方法。
学生没有思路时可以先提供一个合理的起点并说明假设。不反复要求学生先回答才能获得帮助；未知结论如实标注。
"""
    return common + """当前是辅导模式：每轮最多聚焦一个关键问题，先回应学生已有思路，再解释必要知识、指出问题或给少量提示。
不要连续用空泛问题拖住学生。已有方法明确时可以完整编写和调试实现代码。
学生明确求答案时会切到本轮助手模式。单纯停顿不会触发代答；持续卡住时提醒可点击「给我一个具体方案」。
"""


def bounded_history(history, max_turns=12):
    """Keep complete tool groups, recent turns and retrievable older-source excerpts."""
    user_positions = [i for i, m in enumerate(history) if m.get("role") == "user"]
    begin = user_positions[-max_turns] if len(user_positions) > max_turns else 0
    recent = deepcopy(history[begin:])
    older = history[:begin]
    excerpts = [{"message_id": m.get("id"), "origin": m.get("origin", "legacy_unknown"),
                 "excerpt": (m.get("content") or "")[:500]}
                for m in older if m.get("role") == "user"][-24:]
    summary = ("更早对话索引（原文完整保留；摘录不能视为已确认结论）：\n" +
               json.dumps(excerpts, ensure_ascii=False)) if excerpts else ""
    # The final request is budgeted jointly by bounded_prompt after system
    # additions and tool schemas exist; do not independently drop source IDs here.
    # Bound oversized tool outputs without breaking call/result pairing.
    for item in recent:
        if item.get("role") == "tool" and len(item.get("content", "")) > 4000:
            item["content"] = item["content"][:4000] + "\n[完整输出保存在运行记录中]"
        if item.get("id") and item.get("role") in {"user", "assistant", "tool"}:
            # Agent.build_messages intentionally removes storage-only fields.
            # Keep an address in the text so clipped content remains retrievable.
            item["content"] = f"[原始记录 message:{item['id']}；可用 read_research_record 按ID读取全文]\n" + (item.get("content") or "")
    return recent, summary


_SOURCE_REF = re.compile(r"message:([A-Za-z0-9_:\-]{1,180})")


def _byte_length(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def prompt_size(messages, tools):
    """Conservative input-token bound including tool schemas and wire overhead.

    UTF-8 bytes deliberately overestimate mixed Chinese/code token counts. The
    additional allowance covers message/function framing across compatible APIs.
    This is an input budget; output-token limits remain the provider's setting.
    """
    return _byte_length({"messages": messages, "tools": tools}) + 512 + 16 * len(messages) + 32 * len(tools)


def _clip_text(value, byte_limit, head_only=False):
    text = str(value or "")
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    marker = "\n[内容已截取；原始记录完整保留，请按来源ID分页读取]\n"
    available = max(0, byte_limit - len(marker.encode("utf-8")))
    head = available if head_only else available * 3 // 4
    tail = available - head
    return encoded[:head].decode("utf-8", "ignore") + marker + (encoded[-tail:].decode("utf-8", "ignore") if tail else "")


def _tool_groups(messages):
    """Yield valid groups, stripping only unmatched calls/orphan results."""
    groups, index = [], 0
    while index < len(messages):
        message = deepcopy(messages[index])
        index += 1
        if message.get("role") == "tool":
            continue
        if message.get("role") == "assistant" and message.get("tool_calls"):
            results = []
            while index < len(messages) and messages[index].get("role") == "tool":
                results.append(deepcopy(messages[index]))
                index += 1
            result_by_id = {m.get("tool_call_id"): m for m in results}
            calls = []
            seen = set()
            for call in message["tool_calls"]:
                identity = call.get("id")
                if identity and identity in result_by_id and identity not in seen:
                    calls.append(call)
                    seen.add(identity)
            if calls:
                message["tool_calls"] = calls
                groups.append([message, *(result_by_id[call["id"]] for call in calls)])
            else:
                message.pop("tool_calls", None)
                if message.get("content"):
                    groups.append([message])
        else:
            groups.append([message])
    return groups


def bounded_prompt(messages, available_tools, budget=None):
    """Bound the final request as a whole, including every new tool iteration.

    Call immediately before each model request, after all system additions and
    tool outputs exist. Returns copies; neither source messages nor arguments of
    retained tool calls are modified. It is a no-op outside the solve workflow.
    """
    if not solve_turn.get():
        return messages
    budget = int(budget if budget is not None else config.get_workflow_context_budget())
    if budget < prompt_size([], available_tools) + 1800:
        raise ValueError("WORKFLOW_CONTEXT_TOKENS 太小，无法容纳工具定义和基础对话；请增加上下文预算")
    groups = _tool_groups(messages)
    if not groups or groups[0][0].get("role") != "system":
        groups.insert(0, [{"role": "system", "content": system_prompt("tutor")}])
    original_system = groups[0][0].get("content") or ""
    omitted = []
    def flattened():
        return [message for group in groups for message in group]
    def protected(index):
        # A thinking tool chain is a protocol transcript, not prose. Replacing
        # it with a synthetic assistant summary drops required provider fields.
        latest_user = max((i for i, g in enumerate(groups) if g[0].get("role") == "user"), default=0)
        return index > latest_user and "reasoning_content" in groups[index][0]
    def remove(index):
        removed = groups.pop(index)
        for message in removed:
            omitted.extend(_SOURCE_REF.findall(message.get("content") or ""))
    # A long new request must not retain dozens of complete old user turns.
    while sum(group[0].get("role") == "user" for group in groups) > 12:
        first_user = next(i for i, g in enumerate(groups) if g[0].get("role") == "user")
        next_user = next(i for i in range(first_user + 1, len(groups)) if groups[i][0].get("role") == "user")
        for _ in range(next_user - first_user):
            remove(first_user)
    if prompt_size(flattened(), available_tools) <= budget:
        return flattened()
    # Preserve the full teaching/ownership policy at the system prompt's start;
    # the rest is task context, retrievable through the indexed source records.
    system_limit = max(1800, min(6000, (budget - prompt_size([], available_tools)) // 3))
    groups[0][0]["content"] = _clip_text(original_system, system_limit, head_only=True)
    while prompt_size(flattened(), available_tools) > budget and sum(g[0].get("role") == "user" for g in groups) > 1:
        first_user = next(i for i, g in enumerate(groups) if g[0].get("role") == "user")
        next_user = next(i for i in range(first_user + 1, len(groups)) if groups[i][0].get("role") == "user")
        for _ in range(next_user - first_user):
            remove(first_user)
    if omitted:
        refs = list(dict.fromkeys(omitted))[-32:]
        groups[0][0]["content"] += "\n已省略历史的完整消息ID：" + ", ".join(refs)
    while prompt_size(flattened(), available_tools) > budget:
        # An enormous function argument cannot be truncated into invalid JSON.
        # Replace the entire paired group with excerpts of actual tool results.
        large_tools = [(i, g) for i, g in enumerate(groups) if len(g) > 1]
        excessive = [(i, g) for i, g in large_tools if not protected(i) and _byte_length(g) > max(1200, budget // 4)]
        if excessive:
            index, group = max(excessive, key=lambda item: _byte_length(item[1]))
            names = ", ".join(str(c.get("function", {}).get("name", "tool")) for c in group[0]["tool_calls"])
            refs = list(dict.fromkeys(identity for m in group for identity in _SOURCE_REF.findall(m.get("content") or "")))
            excerpts = "\n".join(str(m.get("content") or "") for m in group[1:])
            summary = "[工具调用记录摘要；调用与结果整组省略] " + names + "\n来源消息ID：" + ", ".join(refs)
            groups[index] = [{"role": "assistant", "content": summary + "\n实际工具输出摘录：\n" + _clip_text(excerpts, 1200)}]
            continue
        # Keep the current user's beginning/end and stable source address. For
        # large tool outputs this truncates result text, never function arguments.
        candidates = [m for i, group in enumerate(groups[1:], 1) for m in group
                      if not (protected(i) and m.get("role") == "assistant")
                      and len(str(m.get("content") or "").encode("utf-8")) > 320]
        if candidates:
            message = max(candidates, key=lambda m: len(str(m.get("content") or "").encode("utf-8")))
            current_bytes = len(str(message.get("content") or "").encode("utf-8"))
            message["content"] = _clip_text(message["content"], max(320, current_bytes // 2))
            continue
        # Many small tool calls in the current turn can also exhaust the budget.
        removable = next((i for i, group in enumerate(groups[1:], 1) if group[0].get("role") != "user" and not protected(i)), None)
        if removable is not None:
            remove(removable)
            continue
        if len(groups[0][0]["content"].encode("utf-8")) > 1800:
            groups[0][0]["content"] = _clip_text(groups[0][0]["content"], 1800, head_only=True)
            continue
        raise ValueError("上下文预算不足，原始讨论已保留；本轮工具协议不能截断，请增加 WORKFLOW_CONTEXT_TOKENS")
    return flattened()


def dispatch_solve_tool(name, args, run_id, level=""):
    """Queue solve computation; ordinary/practice tools retain their existing behavior."""
    from .tools import dispatch_tool
    args = args if isinstance(args, dict) else {}
    turn = solve_turn.get()
    if turn and name == 'search_knowledge':
        query = str(args.get('query', '')).strip()
        cache = turn.setdefault('knowledge_cache', {})
        if query not in cache:
            cache[query] = dispatch_tool(name, args, run_id, level=level)
        return cache[query]
    if turn and name == 'read_document':
        from . import solve_flow, solve_sessions
        data = solve_sessions.load(turn['sid']) or {}
        filename = str(args.get('filename', ''))
        attachments = solve_flow.active_attachments(data, turn['step_id'])
        matches = [item for item in attachments if filename in (item['storage_name'], item['filename'])]
        if len(matches) > 1:
            return {'content': '同名附件有多个版本，请使用附件清单中的 storage_name 读取指定版本。', 'display': {'type': 'notice'}}
        if matches:
            result = dispatch_tool(name, {'filename': matches[0]['storage_name']},
                                   solve_flow.attachment_run_id(turn['sid'], turn['step_id']), level=level)
            result['display']['filename'] = matches[0]['filename']
            return result
    if turn and name == "read_research_record":
        from . import solve_sessions, storage
        data = solve_sessions.load(turn["sid"]) or {}
        kind, identity = args.get("kind"), args.get("id")
        records = []
        if kind == "message":
            for conv_id in data.get("step_convs", {}).values():
                conv = storage.load(conv_id, base=config.SOLVE_CONVERSATIONS_DIR) or {}
                records.extend(conv.get("messages", []))
        elif kind == "artifact":
            records = data.get("workflow", {}).get("artifacts", [])
        elif kind == "evidence":
            records = data.get("evidence", [])
        record = next((r for r in records if r.get("id") == identity), None)
        try:
            value = args.get("offset", 0)
            offset = max(0, int(value)) if not isinstance(value, bool) else 0
        except (TypeError, ValueError, OverflowError):
            offset = 0
        raw = json.dumps(record, ensure_ascii=False) if record else "当前项目中不存在此记录"
        offset = min(offset, len(raw))
        # A page fits alongside schema, policy and the student's current turn.
        page_size = 2000
        value = {"id": identity, "kind": kind, "content": raw[offset:offset+page_size], "offset": offset,
                 "found": record is not None, "next_offset": offset+page_size if len(raw)>offset+page_size else None}
        return {"content": json.dumps(value, ensure_ascii=False), "display": {"type": "research_record", **value}}
    if turn and name == "run_python":
        from . import workflow
        # Deduplicate across replay and editor autosave; new turns can run new checks.
        operation_id = 'chat-code:' + workflow._digest([turn['message_id'], turn['step_id'], args.get('code', '')])
        job = workflow.enqueue_code(turn["sid"], turn["step_id"], args.get("code", ""),
                                    turn.get("purpose", "验证当前思路"), turn["message_id"], operation_id=operation_id)
        return {"content": json.dumps({"status": "queued", "job_id": job["id"],
                                       "message": "后台验证已排队。立即调用 sleep_until_result 并传入此 job_id，等待真实结果；不得重提代码。"}, ensure_ascii=False),
                "display": {"type": "workflow_queued", "job_id": job["id"], "code": args.get("code", "")}}
    if turn and name == "sleep_until_result":
        import threading
        from . import workflow, solve_chat_stream
        job_id = str(args.get("job_id", ""))
        source = workflow.snapshot(turn["sid"])
        job = next((j for j in source.get("jobs", []) if j["id"] == job_id
                    and j.get("source_message_id") == turn["message_id"]), None)
        if not job:
            return {"content": "等待失败：找不到本轮任务。请勿重提代码；学生仍可在后台任务面板查看。",
                    "display": {"type": "workflow_wait", "status": "missing", "job_id": job_id}}
        sleeper = threading.Event()
        while job.get("status") not in workflow._TERMINAL | {'paused'}:
            if solve_chat_stream.is_cancel_requested(turn.get("conversation_id", ""), turn.get("client_message_id", "")):
                return {"content": "本轮等待已停止；任务实际状态请查看任务面板。",
                        "display": {"type": "workflow_wait", "status": "stopped", "job_id": job_id}}
            sleeper.wait(0.5)
            source = workflow.snapshot(turn["sid"])
            job = next((j for j in source.get("jobs", []) if j["id"] == job_id), {"status": "interrupted", "error": "任务记录已移除"})
        status = job.get("status")
        from . import solve_sessions
        data = solve_sessions.load(turn['sid']) or {}
        payload = {key: job.get(key) for key in ("id", "status", "phase", "result", "error", "warnings", "missing_inputs", "evidence_ids", "artifact_ids")}
        payload['evidence'] = [{key: ev.get(key) for key in ('id', 'stdout', 'stderr', 'metrics', 'execution_success', 'research_verdict')}
                               for ev in data.get('evidence', []) if ev['id'] in job.get('evidence_ids', [])]
        content = "后台任务当前真实状态与材料（执行成功不等于研究结论成立）：\n" + json.dumps(payload, ensure_ascii=False)
        return {"content": content, "display": {"type": "workflow_wait", "status": status, "job_id": job_id}}
    if turn and name == "report_step_completion":
        from . import framework, solve_sessions
        data = solve_sessions.load(turn["sid"]) or {}
        steps = data.get("problem", {}).get("steps", [])
        step = next((s for s in steps if s.get("id") == turn["step_id"]), {})
        spec = framework.acceptance(step)
        def as_list(value):
            if isinstance(value, str):
                return [value]
            return list(value) if isinstance(value, (list, tuple)) else []
        satisfied = [str(x).strip() for x in as_list(args.get("satisfied")) if str(x).strip()]
        missing = [str(x).strip() for x in as_list(args.get("missing")) if str(x).strip()]
        summary = str(args.get("summary") or "").strip()
        # 以条目数量为准判断是否覆盖全部验收项（允许模型复述时措辞不同）。
        ready = not missing and len(satisfied) >= len(spec["checklist"])
        state = {"step_id": turn["step_id"], "satisfied": satisfied, "missing": missing,
                 "summary": summary, "ready": ready, "updated_at": time.time()}
        with solve_sessions.lock:
            latest = solve_sessions.load(turn["sid"])
            if latest is not None:
                latest.setdefault("workflow", {})["step_acceptance"] = state
                solve_sessions.save(latest)
        content = ("已记录本步验收：全部满足，可以提示学生总结本步。"
                   if ready else
                   "已记录本步验收：仍有未满足的条目，请继续在本步内补齐，不要开始下一步，也不要提前报完成。")
        return {"content": content,
                "display": {"type": "step_ready", **state,
                            "objective": spec["objective"], "checklist": spec["checklist"]}}
    if turn and name in {"write_file", "delete_file", "read_file", "list_dir", "create_directory"}:
        from pathlib import Path
        root = Path(turn["workdir"]).resolve()
        raw = args.get("path") or args.get("directory") or str(root)
        target = Path(raw).expanduser()
        target = (target if target.is_absolute() else root / target).resolve()
        if not target.is_relative_to(root):
            return {"content": "自动操作仅限当前研究项目目录。", "display": {"type": "error", "message": "路径不在当前项目内"}}
        if name == "write_file":
            # Proposed files are versioned material; students explicitly export/save them.
            return {"content": "请在回复中展示建议内容。系统会保存为待审材料，不覆盖学生文件。",
                    "display": {"type": "file_suggestion", "path": str(target), "content": args.get("content", "")}}
        if name == "delete_file":
            return {"content": "请由学生通过项目文件工具删除文件；自动流程保留历史版本。", "display": {"type": "notice"}}
        args = {**args, "path": str(target)}
    return dispatch_tool(name, args, run_id, level=level)

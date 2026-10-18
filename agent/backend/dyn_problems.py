"""工作台「做题」模式：把用户上传/粘贴的一道题，用 LLM 按建模框架动态拆成分步题目。

产出与静态题库同 schema（见 problems.py / framework.py），写入 data/dynamic_problems/<id>.json，
之后所有现有 practice 接口（load_public / step-chat / assess）对它零改动即可工作。
"""
import json
import logging
import re
import uuid
import time

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from .config import config
from . import framework
from .documents import read_document

logger = logging.getLogger(__name__)


def failure_detail(error):
    """Keep provider diagnostics in logs; show an actionable, non-PDF error."""
    if isinstance(error, (APITimeoutError, TimeoutError)):
        return 504, "模型服务响应超时，题目拆解尚未完成。输入和附件仍保留；可稍后重试，或打开下方已有存档继续。"
    if isinstance(error, APIConnectionError):
        return 502, "连接模型服务时中断，题目拆解尚未完成。这不表示 PDF 不受支持。输入和附件仍保留；可稍后重试，或打开下方已有存档继续。"
    if isinstance(error, APIStatusError):
        if error.status_code == 402:
            return 402, "模型 API 余额不足，请充值或更换有额度的模型配置。输入和附件仍保留。"
        if error.status_code in (401, 403):
            return 502, "模型服务拒绝访问，请检查 API Key 和模型权限。输入和附件仍保留。"
        if error.status_code == 429:
            return 429, "模型服务当前请求过多或额度已用尽，请稍后重试或检查额度。输入和附件仍保留。"
        return 502, "模型服务未能完成题目拆解，请检查模型配置或稍后重试。输入和附件仍保留。"
    if isinstance(error, ValueError):
        return 422, str(error)
    return 500, "题目拆解暂时失败，输入和附件仍保留。请稍后重试。"


def _generate_content(client, model, messages):
    # Do not multiply the SDK's retries with a broad format fallback. Stream
    # responses so gateways can forward progress before the entire JSON exists.
    client = client.with_options(timeout=httpx.Timeout(45.0, connect=10.0), max_retries=0)
    deadline = time.monotonic() + 180
    kwargs = dict(model=model, messages=messages, temperature=0.3, stream=True,
                  response_format={"type": "json_object"})
    for attempt in range(2):
        parts = []
        stream = None
        try:
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                if time.monotonic() > deadline:
                    raise TimeoutError("题目拆解超过等待上限")
                if chunk.choices:
                    text = chunk.choices[0].delta.content
                    if text:
                        parts.append(text)
            return "".join(parts)
        except APIStatusError as error:
            # Only a specific unsupported-format rejection permits a second
            # request. Connection, billing and server failures must surface.
            detail = str(error).lower()
            unsupported = (error.status_code in (400, 422) and
                           "response_format" in detail and
                           any(word in detail for word in ("unsupported", "not support", "not allowed", "unknown parameter")))
            if attempt == 0 and not parts and unsupported:
                kwargs.pop("response_format", None)
                continue
            raise
        finally:
            if stream is not None:
                stream.close()


def _client_and_model():
    # 延迟导入，避免与 agent 的循环依赖
    from .agent import agent
    return agent.client, config.get_llm_model()


def _framework_brief() -> str:
    lines = [f"共 {len(framework.FRAMEWORK)} 个模块。主线那一组必须全部用到（每个一次），"
             f"其它情景组按这一组真正需要什么取用；顺序由你根据题目特点决定："]
    for s in framework.FRAMEWORK:
        lines.append(
            f"- {s['key']}｜{s['name']}（modality={s['modality']}，建议占比{s['default_weight']}）："
            f"{s['goal']}；交付物：{s['deliverable']}"
        )
    return "\n".join(lines)


_SYS = """你是数学建模助教。学生给你一道赛题题面，你要把它按「建模框架」拆成一份**一步步一起做**的引导路线图。

你**不是**在出答案、也**不是**在给这道题定标准答案/评分标尺。你只是把这道题该走的阶段排好，并在每个阶段抛出**针对本题的引导性问题**，让学生和你一步步一起把它做出来。

你必须严格输出一个 JSON 对象（不要任何额外解释、不要代码块包裹），结构如下：
{
  "title": "给这道题起的简短标题",
  "background": "题面的精炼复述（保留关键数据与问法，可适当条理化，Markdown）",
  "requirements": [
    {
      "id": "R1",
      "text": "这条要求要交出什么结论（中文一句话，不要写答案）",
      "quote": "题面里对应的原句片段（英文题面保留英文原句，可截断）"
    }
  ],
  "tasks": [
    {
      "id": "T1",
      "title": "这一组的情景名（例：基本场景：单层办公楼 / 情景A：三层别墅）",
      "goal": "这一组做完要得出什么（一句话，不要写答案）",
      "requirement_ids": ["R2", "R3"]
    }
  ],
  "steps": [
    {
      "task_id": "T1",                    // 必须取自上面 tasks 里的 id
      "stage_key": "restate",              // 必须取自下方框架的 key
      "title": "本步标题",
      "prompt": "本步要一起想清楚什么。用要点列出 2-4 条【本步重点】，每条是一个**针对本题的引导性问题或要思考的点**，而不是答案。",
      "objective": "本步做完应该得到什么结论（一句话，写清交付什么，不要写出答案本身）",
      "checklist": ["可核对的验收条目1", "可核对的验收条目2"],  // 2-4 条，描述"满足什么才算完成"，不要是答案
      "covers": ["R2"],                    // 本步认领的题面要求 id，必须是上面 requirements 里出现过的
      "hint": "一两条思考方向的提示（可空，同样不能是答案）"
    }
  ]
}

# 框架模块（共 {size} 个，必须各用一次；先后顺序由你按这道题的特点决定）
{framework}

# 硬性要求

# 要求清单（requirements）
- requirements 要**逐条**覆盖题面：编号要求里的每一个子项、a/b/c 子项、"至少两个""1-2 页"这类数量与交付要求，各算一条；通常 5-12 条，宁可拆细，不要合并成一句空话。
- text 是你改写的简体中文一句话（说清要交出什么，不是答案）；quote 保留题面原句片段，便于学生核对。

# 情景分组（tasks）
- 题面只要求一套模型/一个场景时，就给 1 个 task（可以叫"整道题"）。
- 题面要求多个场景（例如"再设计两个建筑布局""换一种建筑和人数"）或明显分阶段交付（例如"扩展模型""给委员会写一封信"）时，**按场景/交付分组**，每组一个 task。
- **第一个 task 是主线，必须包含全部 {size} 个模块（正好 {size} 条步骤）**；其它 task 按这一组真正需要什么给模块（例如只做情景应用，可以只给 build / solve / analyze 三条），但每组至少 1 条。
- task 的 requirement_ids 写这一组负责回答的要求 id。

# 步骤（steps）
- 每个 task 内 stage_key **不能重复**；模块的先后顺序按这一组推进最顺的方式排（不要所有题目都套同一套顺序）。
  · 例如：以数据仿真/求解为主的题，可以更早进入"模型求解/结果分析"；以评价、推广为主的题，可以更早进入"模型评价/推广"；条件含糊的题，可以先澄清"模型假设"再回头"问题重述"。
- 主线那组通常以 restate 开头（先分点想清楚已知条件、要回答的子问题与关键约束，这一步是文字梳理，不写代码、不建公式）。
- 步骤按要做的先后顺序排列：先主线那一组的 {size} 条，再依次是各情景组。
- 每一步的 prompt 都要显式列出【本步重点】（2-4 条），且必须是**引导性问题/要点**的口吻。
- 每一步都要给出 objective 与 checklist：objective 说明本步做完应得到什么结论，checklist 是 2-4 条**可核对**的验收条目（例："列出至少 3 条关键约束"、"给出符号表且单位齐全"），描述完成标准而非答案。
- 情景组的步骤标题要带上情景名（例："情景A · 模型建立"），让学生一眼看出属于哪一组。
- 每条 requirement **至少要被一个步骤的 covers 认领**（一条要求可以被多个步骤认领，例如模型建立和结果分析都负责它）。

# 其它
- 紧扣这道题的具体情境与数据来提问，不要套空话模板。
- **全程中文**：无论题面是中文还是英文，你输出的 title、background、每一步的 title/prompt/hint **一律用简体中文**（题面里的专有名词、变量名、公式可保留原文）。
- **绝对禁止**：不要写出这道题的答案、结论、具体公式、求解结果；不要输出评分维度（criteria）、参考标尺（reference_outline）或任何「标准做法」。你只负责把路线和问题摆出来，答案由学生和你在后续对话里一起得出。
- 只输出 JSON。"""


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    # 去掉可能的 ```json 包裹
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 兜底：截取第一个 { 到最后一个 }
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


# 自动认领要求时的关键词线索：先按模块语义猜，再退回主线最后一步。
_STAGE_HINTS = {
    "restate": ["重述", "理解", "要求", "子问题", "约束", "已知", "identify", "understand", "requirement"],
    "assume": ["假设", "简化", "前提", "assumption", "assume"],
    "notation": ["符号", "变量", "单位", "notation", "symbol", "variable"],
    "build": ["模型", "建立", "方程", "机理", "model", "formulate", "layout", "布局", "geometry"],
    "solve": ["求解", "算法", "代码", "数值", "结果", "时间", "solve", "algorithm", "simulat", "time"],
    "analyze": ["分析", "检验", "验证", "合理性", "对比", "analysis", "valid", "compare"],
    "sensitivity": ["灵敏", "扰动", "稳定性", "sensitivity", "robust"],
    "evaluate": ["评价", "优缺点", "局限", "advantage", "disadvantage", "limitation"],
    "extend": ["扩展", "拓展", "推广", "技术", "传感", "自动化", "建议", "信", "委员会", "推荐",
               "letter", "committee", "technolog", "sensor", "extend", "future", "recommend"],
}


def _requirements(raw: dict) -> list[dict]:
    """题面要求清单：模型给出的每一项都要带上稳定 id。"""
    items = []
    for index, entry in enumerate(raw.get("requirements") or []):
        if not isinstance(entry, dict):
            text = str(entry).strip()
            entry = {"text": text}
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        rid = str(entry.get("id") or "").strip() or f"R{index + 1}"
        items.append({"id": rid, "text": text[:400], "quote": str(entry.get("quote") or "").strip()[:400]})
    seen = set()
    unique = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique


def _tasks(raw: dict, requirement_ids: set[str]) -> list[dict]:
    """情景分组：模型没给分组时，退化成单组（行为与拆分前一致）。"""
    items = []
    for index, entry in enumerate(raw.get("tasks") or []):
        if not isinstance(entry, dict):
            entry = {"title": str(entry)}
        title = str(entry.get("title") or "").strip()
        tid = str(entry.get("id") or "").strip() or f"T{index + 1}"
        reqs = [str(x).strip() for x in (entry.get("requirement_ids") or []) if str(x).strip()]
        items.append({"id": tid, "title": title[:120], "goal": str(entry.get("goal") or "").strip()[:400],
                      "requirement_ids": [x for x in reqs if x in requirement_ids]})
    seen = set()
    unique = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique or [{"id": "T1", "title": "", "goal": "", "requirement_ids": []}]


def _build_step(key: str, source: dict, task_id: str, index: int, requirement_ids: set[str]) -> dict:
    stage = framework.get_stage(key) or {}
    checklist_in = source.get("checklist") if isinstance(source.get("checklist"), list) else []
    checklist = [str(item).strip() for item in checklist_in if str(item).strip()][:6]
    covers = [str(x).strip() for x in (source.get("covers") or []) if str(x).strip()]
    return {
        "id": f"s{index + 1}",
        "task_id": task_id,
        "stage_key": key,
        "modality": framework.modality_of(key),
        "guide_style": stage.get("guide_style", ""),
        "deliverable": stage.get("deliverable", ""),
        "title": str(source.get("title") or stage.get("name") or f"第 {index + 1} 步").strip()[:120],
        "prompt": str(source.get("prompt") or stage.get("goal") or "").strip()[:4000],
        "objective": str(source.get("objective") or "").strip()[:600],
        "checklist": checklist,
        "covers": [x for x in dict.fromkeys(covers) if x in requirement_ids],
        "covers_assumed": bool(source.get("covers_assumed")),
        "hint": str(source.get("hint") or "").strip()[:600],
        "criteria": [],
        "max_score": stage.get("default_weight", 10),
        "reference_outline": "",
        "paper_points": [],
    }


def _auto_assign_covers(problem: dict) -> list[dict]:
    """没人认领的要求：按关键词挑一个最像的步骤补上，并标记为「程序自动认领」。

    宁可显式标记，也不静默丢掉题面的要求：这些条目会在开局对照表里提示学生，
    并允许在「编辑路线」里改到别的步骤。
    """
    requirements = framework.requirements_of(problem)
    steps = problem.get("steps") or []
    if not requirements or not steps:
        return []
    rows = framework.coverage_map(problem)
    missing = [row for row in rows if not row["covered"]]
    if not missing:
        return []
    main_tid = next((task["id"] for task in framework.tasks_of(problem)), "")
    for row in missing:
        text = (row["text"] + " " + row["quote"]).lower()
        best, best_score = None, 0
        for position, step in enumerate(steps):
            key = step.get("stage_key", "")
            haystack = " ".join([str(step.get("title") or ""), str(step.get("prompt") or ""),
                                 str(step.get("deliverable") or "")]).lower()
            score = sum(1 for token in _STAGE_HINTS.get(key, []) if token in text)
            score += sum(1 for token in ("模型", "求解", "分析", "布局", "时间") if token in haystack and token in text)
            if framework.step_task_id(step) == main_tid:
                score += 0.5  # 同分时优先落在主线上
            if score > best_score:
                best, best_score = position, score
        target = best if best is not None else len(steps) - 1
        steps[target].setdefault("covers", [])
        if row["id"] not in steps[target]["covers"]:
            steps[target]["covers"].append(row["id"])
        steps[target]["covers_assumed"] = True
    return [row["id"] for row in missing]


def _normalize(raw: dict, statement: str) -> dict:
    """把 LLM 产出归一为题库 schema：补 id、modality，并归一「要求→步骤」归属。

    动态题是「一步步一起做」模式：不生成评分维度(criteria)与参考标尺(reference_outline)，
    max_score 仅为保持 schema 合法而回退到框架默认权重（动态题不打分）。

    主线那一组固定 {size} 个模块（各一次）；其它情景组按模型给的内容取用，
    组内模块不重复 —— 同一个模块可以在不同情景组里重复出现。
    """
    valid_keys = list(framework.stage_keys())
    requirements = _requirements(raw)
    requirement_ids = {item["id"] for item in requirements}
    tasks = _tasks(raw, requirement_ids)
    default_tid = tasks[0]["id"]

    grouped: dict[str, list] = {task["id"]: [] for task in tasks}
    for entry in raw.get("steps") or []:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("task_id") or "").strip()
        if tid not in grouped:
            tid = default_tid
        grouped[tid].append(entry)

    steps: list[dict] = []
    kept_tasks: list[dict] = []
    for position, task in enumerate(tasks):
        picked, used = [], set()
        for entry in grouped.get(task["id"]):
            key = entry.get("stage_key", "")
            if key not in valid_keys or key in used:
                continue
            used.add(key)
            picked.append(entry)
        if position == 0:
            # 主线：9 个模块一个不漏，漏掉的按框架默认补齐。
            for key in valid_keys:
                if key not in used:
                    used.add(key)
                    picked.append({"stage_key": key})
        elif not picked:
            continue  # 空情景直接丢弃，不产生空组
        kept_tasks.append(task)
        for entry in picked:
            steps.append(_build_step(entry.get("stage_key", ""), entry, task["id"], len(steps), requirement_ids))
    if not steps:
        raise ValueError("生成的步骤为空或 stage_key 不合法")

    pid = "dyn_" + uuid.uuid4().hex[:10]
    problem = {
        "id": pid,
        "schema_version": 3,
        "dynamic": True,
        "title": (raw.get("title") or "我的题目").strip()[:60],
        "background": raw.get("background") or statement,
        "requirements": requirements,
        "tasks": kept_tasks,
        "difficulty": "",
        "tags": [],
        "data_files": [],
        "total_max_score": sum(s["max_score"] for s in steps),
        "steps": steps,
    }
    assumed = _auto_assign_covers(problem)
    if assumed:
        logger.info("动态题 %s：以下要求由程序自动认领，需学生确认 %s", pid, assumed)
    return problem


def generate(statement: str, run_id: str | None = None, files: list[str] | None = None, initial_thought: str = "", student_question: str = "") -> dict:
    """根据题面（+可选已上传文件）生成动态题目，落盘后返回完整 dict。

    抛出异常由调用方转成 HTTP 错误。
    """
    statement = (statement or "").strip()
    # 若提供了上传的题面文档，读出正文拼进题面
    for fname in (files or []):
        if not run_id:
            break
        try:
            doc = read_document(fname, run_id)
            txt = (doc.get("content") or "").strip()
            if txt:
                statement += f"\n\n[文件 {fname} 内容]\n{txt[:6000]}"
        except Exception:
            logger.warning("做题模式读取上传文件失败：%s", fname)

    if not statement:
        raise ValueError("题面为空")

    client, model = _client_and_model()
    system = (_SYS.replace("{framework}", _framework_brief())
                  .replace("{size}", str(len(framework.FRAMEWORK))))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"题面如下：\n\n{statement[:8000]}\n\n学生初步想法（待检查，不是既定结论）：{initial_thought}\n学生疑问：{student_question}\n围绕学生想法安排检查点，保留实际适用的框架步骤。"},
    ]
    content = _generate_content(client, model, messages)
    raw = _extract_json(content)
    if not raw:
        raise ValueError("AI 未返回可解析的题目结构")

    problem = _normalize(raw, statement)

    config.ensure_dirs()
    out = config.DYNAMIC_PROBLEMS_DIR / f"{problem['id']}.json"
    out.write_text(json.dumps(problem, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = framework.coverage_map(problem)
    logger.info("生成动态题目 %s（%d 组 / %d 步；题面要求 %d 条，未认领 %d 条）",
                problem["id"], len(problem.get("tasks") or []), len(problem["steps"]),
                len(rows), len([r for r in rows if not r["covered"]]))
    return problem

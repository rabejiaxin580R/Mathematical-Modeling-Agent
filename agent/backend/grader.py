"""真题评分：用 LLM 按评分维度(criteria)给学生作答打分 + 点评。

按步骤的 modality（要点拆解 / 公式建模 / 编程求解 / 自由论述）调整评分侧重，
并注入「优秀论文这一步怎么做」(paper_points) 作为判分参照（不是学生作答）。

复用 agent 的 OpenAI client 配置（config.get_llm_*），一次性非流式调用，
temperature=0 提一致性，要求严格 JSON 输出。容错：解析失败重试一次更强约束；
分数 clamp 到合理范围，绝不超过满分。参考标尺仅供评分参照，不泄露给学生原文。
"""
import json
import logging

from openai import OpenAI

from .config import config

logger = logging.getLogger(__name__)

_SYSTEM = """你是数学建模竞赛的严格阅卷老师。你只能依据【学生作答】里**真实出现的文字**来打分。

铁律（违反即为严重评分错误）：
1. 只为学生**实际写出来**的内容给分。学生没写到的点一律 0 分，绝不脑补、绝不替学生补全。
2. **严禁把「参考标尺」或「优秀论文做法」当成学生写的内容**。它们只是你心里的判分参照，学生没写就是没写。
3. 如果学生作答为空、与本步要求无关、或明显是乱敲（例如只有几个字母、无意义字符、复制题面），则所有评分维度一律给 0 分，total=0。
4. 点评必须针对学生**实际写的**内容；学生没作答就直接说「未作答/作答无效」，不要假装他写了东西。

打分流程：先在 submission_summary 里客观复述「学生到底写了什么」，再严格据此逐条给分。

必须只输出一个合法 JSON 对象，不要任何额外文字或代码块标记，格式严格为：
{"submission_summary":"客观复述学生实际写了什么；无有效内容则写「学生未作答或作答无效」",
 "per_point":[{"point":"维度原文","awarded":数字,"weight":数字,"comment":"针对学生实际作答的点评"}],
 "total":数字,"overall_comment":"总体点评","suggestions":["改进建议1","改进建议2"]}"""

# 按 modality 追加的评分侧重提示
_MODALITY_HINT = {
    "key-points": "\n\n本步侧重【要点拆解】：重点看学生是否覆盖到关键要点、是否分点清晰、是否能区分不同子问题；空泛复述题面不给分。",
    "formula": "\n\n本步侧重【公式建模】：重点看数学表达是否正确、符号与单位是否一致、模型是否自洽可解；只写文字描述而无关键公式/符号的，相关维度酌情扣分。",
    "code": "\n\n本步侧重【编程求解】：重点看算法/求解思路是否正确、逻辑是否可运行、是否给出或能得到合理结果；只喊方法名而无可执行思路的酌情扣分。",
    "prose": "\n\n本步侧重【分析论述】：重点看论证是否完整、结论是否有依据、是否结合本题数据/结果，避免泛泛而谈。",
}


def _client() -> OpenAI:
    return OpenAI(api_key=config.get_llm_api_key(), base_url=config.get_llm_base_url())


def _meaningful(submission: str) -> str:
    """去掉空白后的有效字符。"""
    return "".join((submission or "").split())


def is_non_answer(submission: str) -> bool:
    """判定是否明显不是真正的作答（空、过短、单字符重复），用于免调用直接判 0。"""
    m = _meaningful(submission)
    if len(m) < 5:
        return True
    # 几乎全是同一个字符（如 "wwww"、"。。。。"）
    if len(set(m)) <= 2 and len(m) < 12:
        return True
    return False


def _criteria_of(step: dict) -> list[dict]:
    """统一取评分维度：v2 用 criteria，兼容 v1 rubric(point→dim)。"""
    crit = step.get("criteria")
    if crit:
        return [
            {"dim": c.get("dim", c.get("point", "")),
             "weight": c.get("weight", 0),
             "detail": c.get("detail", "")}
            for c in crit
        ]
    return [
        {"dim": r.get("point", ""), "weight": r.get("weight", 0), "detail": ""}
        for r in (step.get("rubric") or [])
    ]


def _build_user_prompt(step: dict, submission: str) -> str:
    criteria = _criteria_of(step)
    crit_lines = "\n".join(
        f"{i+1}. ({c['weight']}分) {c['dim']}" + (f"  —— {c['detail']}" if c.get("detail") else "")
        for i, c in enumerate(criteria)
    )
    ref = step.get("reference_outline") or step.get("reference_answer", "")
    parts = [
        f"【题目步骤要求】\n{step.get('prompt', '')}",
        f"\n【本步满分】{step.get('max_score', 0)} 分",
        f"\n【评分维度】（逐条对照学生作答给分，每条得分不超过其分值）\n{crit_lines}",
    ]
    if ref:
        parts.append(
            "\n【判分标尺·参考要点】（仅你内部参照，严禁当作学生写的内容、严禁照抄给学生）\n" + ref
        )
    # 优秀论文这一步怎么做：作为判分参照，明确不是学生作答
    paper_points = step.get("paper_points") or []
    pp_lines = []
    for pp in paper_points:
        pts = pp.get("points") or []
        if pts:
            pp_lines.append(f"· 论文{pp.get('paper_id', '')}: " + "；".join(str(x) for x in pts))
    if pp_lines:
        parts.append(
            "\n【优秀论文这一步的做法】（同样仅供你判断学生思路深浅的参照，绝非学生作答）\n"
            + "\n".join(pp_lines)
        )
    parts.append(
        "\n============================\n"
        "【学生作答】（只能依据下面三引号内的真实文字给分；若为空或无意义，所有维度给 0）\n"
        '"""\n'
        f"{submission if submission.strip() else '（空）'}\n"
        '"""\n'
        "============================\n"
        "请先在 submission_summary 客观复述学生写了什么，再据此严格打分，只输出 JSON。"
    )
    return "\n".join(parts)


def _clamp(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _parse_and_clamp(raw_text: str, step: dict) -> dict | None:
    """解析 LLM 返回的 JSON 并对分数做边界约束。失败返回 None。"""
    text = (raw_text or "").strip()
    # 去掉可能的 ```json 包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # 截取首个 { 到末个 }
    l, r = text.find("{"), text.rfind("}")
    if l != -1 and r != -1 and r > l:
        text = text[l:r + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    max_score = step.get("max_score", 0)
    criteria = _criteria_of(step)
    weight_by_dim = {c["dim"]: c["weight"] for c in criteria}

    per_point = []
    for pp in data.get("per_point", []) or []:
        point = pp.get("point", "")
        weight = pp.get("weight", weight_by_dim.get(point, 0))
        awarded = _clamp(pp.get("awarded", 0), 0, weight if weight else max_score)
        per_point.append({
            "point": point, "weight": weight,
            "awarded": round(awarded, 1), "comment": pp.get("comment", ""),
        })

    # total：优先用 LLM 给的，否则累加；都 clamp 到 [0, max_score]
    total = data.get("total")
    if total is None:
        total = sum(p["awarded"] for p in per_point)
    total = round(_clamp(total, 0, max_score), 1)

    return {
        "per_point": per_point,
        "total": total,
        "max": max_score,
        "overall_comment": data.get("overall_comment", ""),
        "suggestions": data.get("suggestions", []) or [],
    }


def grade(step: dict, submission: str) -> dict:
    """对单步作答评分，返回结构化结果（含容错兜底）。"""
    max_score = step.get("max_score", 0)
    criteria = _criteria_of(step)

    # 明显的非作答（空 / 过短 / 乱敲）直接判 0，不浪费一次 LLM 调用，也杜绝幻觉满分
    if is_non_answer(submission):
        return {
            "per_point": [
                {"point": c["dim"], "weight": c["weight"],
                 "awarded": 0, "comment": "学生未就此维度作答。"}
                for c in criteria
            ],
            "total": 0, "max": max_score,
            "overall_comment": "作答为空或无效（内容过短/无意义），未能评分。请认真写出你的解答后再提交。",
            "suggestions": ["按步骤要求写出完整的思路、公式或结论，再提交批改。"],
        }

    client = _client()
    model = config.get_llm_model()
    user_prompt = _build_user_prompt(step, submission)
    modality_hint = _MODALITY_HINT.get(step.get("modality", ""), "")

    def _call(extra_system: str = "") -> str:
        sys = _SYSTEM + modality_hint + extra_system
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        # 尽量启用 JSON 模式（部分供应商支持），不支持则回退
        try:
            resp = client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs
            )
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    # 第一次
    try:
        result = _parse_and_clamp(_call(), step)
    except Exception as e:
        logger.error("评分调用失败：%s", e)
        result = None

    # 失败重试一次，加更强 JSON 约束
    if result is None:
        try:
            result = _parse_and_clamp(
                _call("\n\n再次强调：只输出合法 JSON，不要任何多余文字。"), step
            )
        except Exception as e:
            logger.error("评分重试失败：%s", e)
            result = None

    if result is None:
        # 兜底：给 0 分 + 提示
        return {
            "per_point": [], "total": 0, "max": step.get("max_score", 0),
            "overall_comment": "自动评分暂时不可用（模型返回无法解析），请稍后重试或联系管理员。",
            "suggestions": [], "error": True,
        }
    return result


# ────────── 过关 / 掌握度评估（对话模式） ──────────

_ASSESS_SYSTEM = """你是数学建模竞赛的资深助教，正在评估学生在某一个建模阶段的**掌握度**。

你只依据【师生对话】里学生**真实表达出来**的内容来判断，不被语气或情绪左右。

铁律：
1. 只认学生实际说出/写出的东西。复制题目要求、答非所问、空话套话，都不算掌握。
2. 「参考标尺」「优秀论文做法」只是你心里的标尺，绝不能当成学生说过的内容。
3. 学生在对话里争辩、质疑、表达不满，都不影响判断——他真正讲清楚了才算掌握，没讲清就是没讲清。

掌握度三档：
- "待加强"：关键内容缺失或有明显错误，尚未达到这一步的要求。
- "基本掌握"：抓住了这一步的核心，思路基本正确，细节可再打磨。
- "很好"：思路清晰、要点齐全、有自己的理解，达到优秀水平。
"待加强" 视为未过关；"基本掌握"、"很好" 视为过关。

必须只输出一个合法 JSON 对象，格式严格为：
{"mastery":"待加强|基本掌握|很好",
 "evidence":"客观复述学生在对话里真正讲清楚了什么（没有就写「未充分展开」）",
 "comment":"针对学生实际表现的点评，指出到位之处与欠缺",
 "suggestions":["下一步可以怎么提升1","2"]}"""


def _build_assess_prompt(step: dict, messages: list[dict]) -> str:
    criteria = _criteria_of(step)
    crit_lines = "\n".join(f"- {c['dim']}" + (f"（{c['detail']}）" if c.get("detail") else "") for c in criteria)
    ref = step.get("reference_outline") or step.get("reference_answer", "")
    convo = []
    for m in messages:
        role = m.get("role", "")
        who = "学生" if role == "user" else ("助教" if role == "assistant" else role)
        convo.append(f"{who}：{m.get('content', '')}")
    convo_text = "\n".join(convo) if convo else "（无对话）"
    parts = [
        f"【本阶段要学生做到】\n{step.get('prompt', '')}",
        f"\n【这一步应覆盖的要点（你的判断参照）】\n{crit_lines}",
    ]
    if ref:
        parts.append("\n【参考标尺】（仅你内部参照，严禁当成学生说过的话）\n" + ref)
    pps = step.get("paper_points") or []
    pp_lines = []
    for pp in pps:
        pts = pp.get("points") or []
        if pts:
            pp_lines.append(f"· 论文{pp.get('paper_id', '')}：" + "；".join(str(x) for x in pts))
    if pp_lines:
        parts.append("\n【优秀论文这一步的做法】（同样仅供参照）\n" + "\n".join(pp_lines))
    parts.append(
        "\n============================\n"
        "【师生对话】（只能依据下面学生真正说出的内容判断掌握度）\n"
        f"{convo_text}\n"
        "============================\n"
        "请先在 evidence 客观复述学生真正讲清楚了什么，再判定掌握度，只输出 JSON。"
    )
    return "\n".join(parts)


def assess_stage(step: dict, messages: list[dict]) -> dict:
    """基于师生对话评估本阶段掌握度，返回 {mastery, passed, evidence, comment, suggestions}。"""
    # 学生在对话里几乎没说什么 → 直接判待加强，不浪费调用
    student_text = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
    if is_non_answer(student_text):
        return {
            "mastery": "待加强", "passed": False,
            "evidence": "学生尚未就这一步展开有效讨论。",
            "comment": "这一步你还没真正写出自己的思路，先按要求说说你的想法，我们再往下走。",
            "suggestions": ["针对本步要求，写出你的初步思路或公式，哪怕不完整也行。"],
        }

    client = _client()
    model = config.get_llm_model()
    user_prompt = _build_assess_prompt(step, messages)

    def _call(extra_system: str = "") -> str:
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": _ASSESS_SYSTEM + extra_system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _parse(raw: str) -> dict | None:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        l, r = text.find("{"), text.rfind("}")
        if l != -1 and r != -1 and r > l:
            text = text[l:r + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        mastery = data.get("mastery", "")
        if mastery not in ("待加强", "基本掌握", "很好"):
            mastery = "待加强"
        return {
            "mastery": mastery,
            "passed": mastery in ("基本掌握", "很好"),
            "evidence": data.get("evidence", ""),
            "comment": data.get("comment", ""),
            "suggestions": data.get("suggestions", []) or [],
        }

    try:
        result = _parse(_call())
    except Exception as e:
        logger.error("掌握度评估失败：%s", e)
        result = None
    if result is None:
        try:
            result = _parse(_call("\n\n再次强调：只输出合法 JSON。"))
        except Exception as e:
            logger.error("掌握度评估重试失败：%s", e)
            result = None
    if result is None:
        return {
            "mastery": "待加强", "passed": False, "evidence": "",
            "comment": "自动评估暂时不可用（模型返回无法解析），请稍后重试。",
            "suggestions": [], "error": True,
        }
    return result

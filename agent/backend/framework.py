"""建模框架：真题练习的「唯一固定骨架」。

题目的内容（题面/数据/论文）是动态的，但建模的基本流程是稳定的。
本模块把这条流程定义为一组有序「阶段(stage)」，每个阶段带一个 modality，
决定该步的输入形态、引导风格与评分侧重：

  key-points  要点拆解（问题分析 / 假设 / 猜题）——重要点覆盖与区分度
  formula     公式建模（符号定义 / 模型建立）——重公式正确性与符号一致
  code        编程求解（算法 / 求解 / 可视化）——重逻辑可运行性与结果
  prose       自由论述（结果分析 / 评价 / 推广 / 写作）——重论证完整

录入题目时，AI 针对本题把每个阶段「实例化」成具体的 prompt / criteria /
reference_outline / paper_points，存进题目 JSON 的 steps（可人工校对）。
"""

MODALITY_KEY_POINTS = "key-points"
MODALITY_FORMULA = "formula"
MODALITY_CODE = "code"
MODALITY_PROSE = "prose"

MODALITIES = {MODALITY_KEY_POINTS, MODALITY_FORMULA, MODALITY_CODE, MODALITY_PROSE}

# 有序阶段列表。default_weight 之和 = 100。
FRAMEWORK = [
    {
        "key": "restate", "name": "问题分析与重述", "modality": MODALITY_KEY_POINTS,
        "goal": "拆出本题真正要回答的子问题与关键约束",
        "guide_style": "苏格拉底式追问，引导分点列举，不直接给答案",
        "deliverable": "子问题清单 + 关键约束", "default_weight": 10,
    },
    {
        "key": "assume", "name": "模型假设", "modality": MODALITY_KEY_POINTS,
        "goal": "提出必要且合理的简化假设并说明理由",
        "guide_style": "质疑每条假设的必要性与合理性",
        "deliverable": "假设清单 + 理由", "default_weight": 10,
    },
    {
        "key": "notation", "name": "符号说明与变量定义", "modality": MODALITY_FORMULA,
        "goal": "规范定义变量 / 参数 / 单位",
        "guide_style": "检查符号一致性与单位是否齐全",
        "deliverable": "符号表", "default_weight": 10,
    },
    {
        "key": "build", "name": "模型建立", "modality": MODALITY_FORMULA,
        "goal": "写出核心方程 / 模型结构",
        "guide_style": "推敲方程是否自洽、可解、贴合题意",
        "deliverable": "核心方程组", "default_weight": 20,
    },
    {
        "key": "solve", "name": "模型求解", "modality": MODALITY_CODE,
        "goal": "用算法 / 代码求出数值结果",
        "guide_style": "逐步调试，关注可运行性与结果合理",
        "deliverable": "可运行代码 + 结果", "default_weight": 15,
    },
    {
        "key": "analyze", "name": "结果分析与检验", "modality": MODALITY_PROSE,
        "goal": "解读结果合理性、做拟合 / 检验",
        "guide_style": "追问结果是否可信、与现实是否吻合",
        "deliverable": "结果解读", "default_weight": 10,
    },
    {
        "key": "sensitivity", "name": "灵敏度分析", "modality": MODALITY_CODE,
        "goal": "扰动关键参数观察输出变化",
        "guide_style": "引导设计扰动实验并解读",
        "deliverable": "灵敏度表 / 图", "default_weight": 10,
    },
    {
        "key": "evaluate", "name": "模型评价", "modality": MODALITY_PROSE,
        "goal": "客观列出优缺点与改进方向",
        "guide_style": "对照假设找局限，避免空泛",
        "deliverable": "优缺点 + 改进", "default_weight": 10,
    },
    {
        "key": "extend", "name": "模型推广与论文写作", "modality": MODALITY_PROSE,
        "goal": "推广思路 + 摘要 / 表达要点",
        "guide_style": "强调创新点与清晰表达",
        "deliverable": "推广 + 摘要要点", "default_weight": 5,
    },
]

_BY_KEY = {s["key"]: s for s in FRAMEWORK}


# 每阶段的通用验收条目：本步交付物是否达成的可核对判据。
# 动态题会在生成时给出更贴合题面的 objective / checklist；这里作为回退与默认。
ACCEPTANCE_HINTS = {
    "restate": [
        "分点列出这道题真正要回答的子问题",
        "标出关键约束、已知条件与已有数据",
        "确认没有漏掉题目的问法或附加要求",
    ],
    "assume": [
        "给出必要且彼此不矛盾的关键假设",
        "每条假设说明理由，并指出它简化了什么",
        "指出假设带来的风险或不适用情形",
    ],
    "notation": [
        "定义全部变量、参数与单位",
        "符号前后一致，没有重复定义或漏定义",
        "说明各符号的取值范围或物理含义",
    ],
    "build": [
        "写出核心方程或模型结构",
        "说明每条公式的建模依据与来源",
        "说明模型成立的条件与边界",
    ],
    "solve": [
        "给出可运行的求解算法或代码",
        "跑出数值结果并说明参数设置",
        "检查结果量级、范围与常识是否相符",
    ],
    "analyze": [
        "解释结果的含义，并判断是否合理",
        "与题意或现实情形做对照",
        "指出结果的不确定性或局限",
    ],
    "sensitivity": [
        "选取关键参数做扰动实验",
        "给出结果随参数变化的表或图",
        "解释哪个参数最敏感及其原因",
    ],
    "evaluate": [
        "客观列出模型的优点",
        "列出缺点、适用范围与限制",
        "给出可操作的改进方向",
    ],
    "extend": [
        "给出模型的推广思路",
        "列出摘要与论文表达要点",
    ],
}


def acceptance(step: dict) -> dict:
    """把一步归一为「目标结论 + 可验收清单」，供前端展示与提示词硬约束使用。

    优先使用步骤自带的 objective / checklist（动态题生成时给出）；
    缺失时回退到框架阶段的 deliverable / goal 与通用验收条目。
    """
    step = step or {}
    stage = get_stage(str(step.get("stage_key") or "").strip()) or {}

    items = []
    raw = step.get("checklist")
    if isinstance(raw, list):
        for index, entry in enumerate(raw):
            if isinstance(entry, dict):
                text = str(entry.get("text") or entry.get("item") or entry.get("title") or "").strip()
                identity = str(entry.get("id") or f"c{index + 1}").strip() or f"c{index + 1}"
            else:
                text, identity = str(entry).strip(), f"c{index + 1}"
            if text:
                items.append({"id": identity, "text": text})
    if not items:
        items = [{"id": f"c{i + 1}", "text": text}
                 for i, text in enumerate(ACCEPTANCE_HINTS.get(step.get("stage_key", ""), []))]
    if not items:
        items = [{"id": "c1", "text": "完成本步要交付的内容，并能说明理由与限制"}]

    deliverable = str(step.get("deliverable") or stage.get("deliverable") or "").strip()
    objective = str(step.get("objective") or "").strip()
    if not objective:
        goal = str(stage.get("goal") or "").strip()
        objective = f"完成本步交付物：{deliverable}" if deliverable else "完成本步要解决的问题"
        if goal:
            objective += f"（{goal}）"
    return {"objective": objective, "deliverable": deliverable, "checklist": items}


def acceptance_prompt(step: dict) -> str:
    """本步目标与验收清单的提示词块：每轮都注入，作为不越界的硬约束。"""
    spec = acceptance(step)
    lines = [
        "## 本步目标与验收清单（硬约束）",
        f"本步目标结论：{spec['objective']}",
    ]
    if spec["deliverable"]:
        lines.append(f"本步交付物：{spec['deliverable']}")
    lines.append("完成本步必须同时满足以下条目：")
    lines.extend(f"- [{item['id']}] {item['text']}" for item in spec["checklist"])
    lines += [
        "",
        "规则：",
        "- 以上条目是这一步的边界与完成判据；只推进本步，不要开始后续步骤的内容。",
        "- 本步开场（第一条回复）必须先复述本步目标与验收清单，再回应学生的想法。",
        "- 之后每次回复都围绕这些条目推进，并在结尾用一行汇报进度："
        "`验收状态：c1 ✓｜c2 ○ 还缺…`（✓=已满足，○=未满足并写明缺什么）。",
        "- 只有当全部条目都能由学生已写出/已说出或已归档的验证结果支撑时，才调用 report_step_completion 报告完成；"
        "程序运行成功不等于数学结论成立。",
        "- 每条验收都要有依据（知识出处 / 学生原话 / 验证记录）；证据不足就写「证据不足」，不要凑数。",
        "- 报告完成后不要自行确认成果，由学生点击「总结本步」。",
    ]
    return "\n".join(lines)


def stage_keys() -> list[str]:
    return [s["key"] for s in FRAMEWORK]


def get_stage(key: str) -> dict | None:
    return _BY_KEY.get(key)


def default_step(stage_key: str, step_id: str, index: int = 0) -> dict:
    """为「编辑路线」时换入的模块构造一个通用步骤。

    题目专属的 prompt/objective 由拆题模型生成；当学生在路线编辑里把一个槽位
    换成别的模块时，这里给出该模块的默认引导语，验收清单与目标由
    acceptance() 按阶段回退补齐。
    """
    stage = get_stage(stage_key) or {}
    return {
        "id": step_id,
        "stage_key": stage_key,
        "modality": modality_of(stage_key),
        "guide_style": stage.get("guide_style", ""),
        "deliverable": stage.get("deliverable", ""),
        "title": stage.get("name", f"第 {index + 1} 步"),
        "prompt": stage.get("goal", ""),
        "objective": "",
        "checklist": [],
        "hint": "",
        "criteria": [],
        "max_score": stage.get("default_weight", 10),
        "reference_outline": "",
        "paper_points": [],
    }


def public_stages() -> list[dict]:
    """供前端「编辑路线」使用：可选模块清单（不含评分细则与参考答案）。"""
    return [{"key": s["key"], "name": s["name"], "modality": s["modality"],
             "goal": s["goal"], "deliverable": s["deliverable"]}
            for s in FRAMEWORK]


def modality_of(key: str) -> str:
    """阶段 key → modality；未知 key 回退 prose。"""
    stage = _BY_KEY.get(key)
    return stage["modality"] if stage else MODALITY_PROSE


# ── 题面要求 → 责任步骤（覆盖度） ──────────────────────────────────────────────
#
# 一道题除了「建模流程」还有「题面要求」：题面里的每一条要求（含 a/b/c 子项、
# 数量要求、交付要求）都必须有某一步负责给出可核对的结论。拆题模型产出
# requirements 与每个 step 的 covers；这里负责把它们拼成开局对照表、拼成每轮
# 注入的硬约束，并算出「没有任何步骤认领」的要求。

def requirements_of(problem: dict) -> list[dict]:
    """题面要求清单；静态题库与旧存档没有这个字段则返回空表。"""
    raw = (problem or {}).get("requirements")
    if not isinstance(raw, list):
        return []
    items = []
    for index, entry in enumerate(raw):
        if isinstance(entry, dict):
            text = str(entry.get("text") or entry.get("title") or "").strip()
            rid = str(entry.get("id") or f"R{index + 1}").strip() or f"R{index + 1}"
            quote = str(entry.get("quote") or "").strip()
        else:
            text, rid, quote = str(entry).strip(), f"R{index + 1}", ""
        if text:
            items.append({"id": rid, "text": text, "quote": quote})
    return items


def tasks_of(problem: dict) -> list[dict]:
    """情景/任务分组清单；没有分组（旧存档、静态题库）则返回空表。"""
    raw = (problem or {}).get("tasks")
    if not isinstance(raw, list):
        return []
    items = []
    for index, entry in enumerate(raw):
        if isinstance(entry, dict):
            tid = str(entry.get("id") or f"T{index + 1}").strip() or f"T{index + 1}"
            title = str(entry.get("title") or "").strip()
            goal = str(entry.get("goal") or "").strip()
            reqs = [str(x).strip() for x in (entry.get("requirement_ids") or []) if str(x).strip()]
        else:
            tid, title, goal, reqs = f"T{index + 1}", str(entry).strip(), "", []
        items.append({"id": tid, "title": title, "goal": goal, "requirement_ids": reqs})
    return items


def step_covers(step: dict) -> list[str]:
    raw = (step or {}).get("covers")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def step_task_id(step: dict) -> str:
    return str((step or {}).get("task_id") or "").strip()


def task_title(problem: dict, task_id: str) -> str:
    for task in tasks_of(problem):
        if task["id"] == task_id:
            return task["title"]
    return ""


def step_label(problem: dict, step: dict) -> str:
    """步骤在对照表里的显示名：多情景时带上情景名（情景A · 模型建立）。"""
    steps = (problem or {}).get("steps") or []
    index = next((i for i, item in enumerate(steps) if item is step
                  or (item.get("id") and item.get("id") == step.get("id"))), -1)
    title = str((step or {}).get("title") or "").strip() or f"第 {index + 1} 步"
    group = task_title(problem, step_task_id(step))
    if group and len(tasks_of(problem)) > 1:
        return f"{group} · {title}"
    return title


def task_groups(problem: dict) -> list[dict]:
    """按情景分组后的步骤清单，供路线图与对照表使用。

    没有 tasks 的旧存档归入一个匿名分组，行为与分组前一致。
    """
    steps = (problem or {}).get("steps") or []
    tasks = tasks_of(problem)
    order: list[str] = []
    buckets: dict[str, list] = {}
    for index, step in enumerate(steps):
        tid = step_task_id(step) or (tasks[0]["id"] if tasks else "T1")
        if tid not in buckets:
            buckets[tid] = []
            order.append(tid)
        buckets[tid].append({"index": index, "step": step})
    known = {t["id"]: t for t in tasks}
    groups = []
    for tid in order:
        meta = known.get(tid, {})
        groups.append({"id": tid, "title": meta.get("title") or "",
                       "goal": meta.get("goal") or "",
                       "requirement_ids": meta.get("requirement_ids") or [],
                       "steps": buckets[tid]})
    return groups


def coverage_map(problem: dict) -> list[dict]:
    """每条要求 → 由哪些步骤负责（开局对照表与提示词都用它）。"""
    requirements = requirements_of(problem)
    if not requirements:
        return []
    steps = (problem or {}).get("steps") or []
    owners: dict[str, list[int]] = {item["id"]: [] for item in requirements}
    for index, step in enumerate(steps):
        for rid in step_covers(step):
            if rid in owners and index not in owners[rid]:
                owners[rid].append(index)
    rows = []
    for item in requirements:
        indexes = owners[item["id"]]
        rows.append({
            **item,
            "step_indexes": indexes,
            "step_labels": [step_label(problem, steps[i]) for i in indexes],
            "covered": bool(indexes),
            # 全部归属都是程序自动补的 → 需要在界面上让学生确认。
            "assumed": bool(indexes) and all(steps[i].get("covers_assumed") for i in indexes),
        })
    return rows


def uncovered_requirements(problem: dict) -> list[dict]:
    return [row for row in coverage_map(problem) if not row["covered"]]


def coverage_prompt(problem: dict, step: dict) -> str:
    """本步视角的覆盖度硬约束：题面每条要求都必须有人负责。"""
    rows = coverage_map(problem)
    if not rows:
        return ""
    steps = (problem or {}).get("steps") or []
    current_index = next((i for i, item in enumerate(steps)
                          if item.get("id") and item.get("id") == step.get("id")), -1)

    lines = ["## 题面要求与责任步骤（硬约束：每条要求都要有归属）",
             f"本题题面共 {len(rows)} 条要求，每条都必须由某一步给出可核对的结论："]
    for row in rows:
        if row["covered"]:
            owners = "、".join(f"第 {index + 1} 步 {label}"
                             for index, label in zip(row["step_indexes"], row["step_labels"]))
            if row.get("assumed"):
                owners += "（程序自动补的归属，请确认是否真的属于这一步，不属于就说出来）"
        else:
            owners = "⚠ 目前没有任何步骤负责"
        lines.append(f"- [{row['id']}] {row['text']} —— {owners}")

    covered_by_me = [row for row in rows if current_index in row["step_indexes"]]
    if covered_by_me:
        lines += ["", "本步认领的要求（只把这几条做实，不要替别的步骤做）："]
        lines += [f"- [{row['id']}] {row['text']}" for row in covered_by_me]
    elif uncovered_requirements(problem):
        lines += ["", "本步没有认领任何要求；如果发现某条要求属于本步，直接告诉学生由本步承担。"]
    else:
        lines += ["", "本步没有认领题面要求，只推进本步的验收清单即可。"]

    lines += [
        "",
        "规则：",
        "- 每条要求都要落到可核对的结论上（公式 / 代码结果 / 已验证的图）；"
        "证据不足时写「证据不足」，不要凑数。",
        "- 不属于本步的要求，不要在本步展开；发现归属错了就如实说明。",
        "- 结尾在「验收状态」之外再写一行：「本步要求：[R2] ✓ 已覆盖｜[R3] ○ 还缺…」。",
        "- 若上面出现「没有任何步骤负责」的要求，先提醒学生，不要自己跳去补完。",
    ]
    return "\n".join(lines)

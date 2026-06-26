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


def stage_keys() -> list[str]:
    return [s["key"] for s in FRAMEWORK]


def get_stage(key: str) -> dict | None:
    return _BY_KEY.get(key)


def modality_of(key: str) -> str:
    """阶段 key → modality；未知 key 回退 prose。"""
    stage = _BY_KEY.get(key)
    return stage["modality"] if stage else MODALITY_PROSE

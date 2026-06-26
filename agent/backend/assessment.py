"""入门测评 + 分级学习路径：加载题库/路径、抽卷、判分定级、组装路径。

数据来源（由 scripts/build_assessment.py 离线生成）：
  - data/assessment/questions.json ：单选题题库
  - data/assessment/paths.json     ：L1–L5 五个等级各一条推荐学习路径

判分逻辑全在本地完成（单选题按难度加权），无需 LLM。等级按测评得分占比映射，
也支持学生自选等级。路径里的 concept_id 在组装时补标题/难度，供前端渲染与跳转。
"""
import json
import logging
import random

from .config import config
from .knowledge import knowledge_base

logger = logging.getLogger(__name__)

ASSESSMENT_DIR = config.DATA_DIR / "assessment"
QUESTIONS_PATH = ASSESSMENT_DIR / "questions.json"
PATHS_PATH = ASSESSMENT_DIR / "paths.json"

DIFF_WEIGHT = {"Beginner": 1, "Intermediate": 2, "Advanced": 3}

# 五个等级顺序与名称（与生成脚本 / 前端一致）
LEVEL_ORDER = ["L1", "L2", "L3", "L4", "L5"]
LEVEL_NAME = {"L1": "萌新", "L2": "入门", "L3": "进阶", "L4": "熟练", "L5": "高手"}
# 自选/兜底用的默认分档（路径文件里也带 score_min/max，以文件为准）
DEFAULT_BANDS = {
    "L1": (0.0, 0.20), "L2": (0.20, 0.40), "L3": (0.40, 0.60),
    "L4": (0.60, 0.80), "L5": (0.80, 1.01),
}

_questions: dict | None = None
_paths: dict | None = None


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_questions(force: bool = False) -> dict:
    global _questions
    if _questions is None or force:
        data = _load_json(QUESTIONS_PATH)
        _questions = data if isinstance(data, dict) else {"questions": []}
    return _questions


def load_paths(force: bool = False) -> dict:
    global _paths
    if _paths is None or force:
        data = _load_json(PATHS_PATH)
        _paths = data if isinstance(data, dict) else {"levels": []}
    return _paths


def available() -> bool:
    """题库是否就绪（题量 > 0）。"""
    return len(load_questions().get("questions", [])) > 0


def _q_weight(q: dict) -> float:
    return DIFF_WEIGHT.get(q.get("difficulty", "Intermediate"), 2)


def sample_quiz(n: int | None = None, shuffle_options: bool = True) -> dict:
    """抽一份测评卷（public 版，不含正确答案）。

    选项可打乱，但返回每题的 option_perm 以便前端提交后后端对应回原始下标——
    更简单的做法：打乱后把答案随之记录在服务端不现实（无状态），所以这里
    打乱选项时同时在题面内不暴露 answer，提交时前端回传「打乱后选中的下标」
    + 我们在 quiz 里带回 perm，submit 时按 perm 还原。
    """
    qs = list(load_questions().get("questions", []))
    random.shuffle(qs)
    if n:
        qs = qs[:n]
    out = []
    for q in qs:
        opts = list(q.get("options", []))
        perm = list(range(len(opts)))
        if shuffle_options:
            random.shuffle(perm)
        shown = [opts[i] for i in perm]
        out.append({
            "id": q["id"],
            "module_id": q.get("module_id", ""),
            "difficulty": q.get("difficulty", ""),
            "stem": q.get("stem", ""),
            "options": shown,
            "perm": perm,   # 打乱后下标 -> 原始下标映射，提交时回传
        })
    return {"count": len(out), "questions": out}


def _question_index() -> dict:
    return {q["id"]: q for q in load_questions().get("questions", [])}


def level_for_score(ratio: float) -> str:
    paths = load_paths().get("levels", [])
    for lv in paths:
        if lv.get("score_min", 0) <= ratio < lv.get("score_max", 1.01):
            return lv["level"]
    # 回退到默认分档
    for code, (lo, hi) in DEFAULT_BANDS.items():
        if lo <= ratio < hi:
            return code
    return "L5" if ratio >= 0.8 else "L1"


def grade(answers: list[dict]) -> dict:
    """判分定级。

    answers: [{id, choice, perm}]，choice 为前端展示顺序下的选中下标，
    perm 为抽卷时下发的下标映射（perm[choice] = 原始下标）。
    返回 {level, level_name, score, correct, total, per_module, wrong_concepts}。
    """
    qidx = _question_index()
    total_w = 0.0
    got_w = 0.0
    correct = 0
    total = 0
    mod_total: dict[str, float] = {}
    mod_got: dict[str, float] = {}
    wrong_concepts = []
    review = []   # 逐题批改明细，供结果页「看答案」

    for a in answers:
        q = qidx.get(a.get("id"))
        if not q:
            continue
        total += 1
        w = _q_weight(q)
        total_w += w
        mid = q.get("module_id", "?")
        mod_total[mid] = mod_total.get(mid, 0) + w

        choice = a.get("choice")
        perm = a.get("perm")
        orig = choice
        if isinstance(perm, list) and isinstance(choice, int) and 0 <= choice < len(perm):
            orig = perm[choice]   # 还原为原始选项下标
        is_correct = orig == q.get("answer")
        if is_correct:
            correct += 1
            got_w += w
            mod_got[mid] = mod_got.get(mid, 0) + w
        else:
            cid = q.get("concept_id")
            if cid:
                wrong_concepts.append(cid)

        nopts = len(q.get("options", []))
        chosen_idx = orig if (isinstance(orig, int) and 0 <= orig < nopts) else None
        # 逐题明细：题干 + 全部选项 + 正确下标 + 学生所选原始下标 + 解析 + 来源知识点
        review.append({
            "id": q.get("id"),
            "module_id": mid,
            "difficulty": q.get("difficulty", ""),
            "stem": q.get("stem", ""),
            "options": list(q.get("options", [])),
            "answer": q.get("answer"),
            "chosen": chosen_idx,
            "correct": is_correct,
            "explain": q.get("explain", ""),
            "concept_id": q.get("concept_id", ""),
            "concept_title": q.get("concept_title", ""),
        })

    ratio = (got_w / total_w) if total_w else 0.0
    per_module = {
        mid: round(mod_got.get(mid, 0) / mt, 3) if mt else 0.0
        for mid, mt in mod_total.items()
    }
    level = level_for_score(ratio)
    return {
        "level": level,
        "level_name": LEVEL_NAME.get(level, ""),
        "score": round(ratio, 3),
        "correct": correct,
        "total": total,
        "per_module": per_module,
        "wrong_concepts": wrong_concepts,
        "review": review,
    }


def _concept_meta(cid: str) -> dict | None:
    u = next((x for x in knowledge_base.units if x.chunk_id == cid), None)
    if u is None:
        return None
    return {"id": u.chunk_id, "title": u.title, "difficulty": u.difficulty,
            "module_id": u.module_id or ""}


def path_for(level: str) -> dict | None:
    """取某等级的学习路径，并为每个 concept_id 补标题/难度（过滤已不存在的）。"""
    level = (level or "").upper()
    paths = load_paths().get("levels", [])
    lv = next((l for l in paths if l.get("level") == level), None)
    if lv is None:
        return None
    milestones = []
    for m in lv.get("milestones", []):
        concepts = []
        for cid in m.get("concept_ids", []):
            meta = _concept_meta(cid)
            if meta:
                concepts.append(meta)
        milestones.append({
            "title": m.get("title", ""),
            "desc": m.get("desc", ""),
            "module_ids": m.get("module_ids", []),
            "concepts": concepts,
        })
    return {
        "level": lv["level"],
        "name": lv.get("name", LEVEL_NAME.get(level, "")),
        "tagline": lv.get("tagline", ""),
        "intro": lv.get("intro", ""),
        "milestones": milestones,
    }


def all_levels() -> list[dict]:
    """供前端「自选等级」展示：等级 + 名称 + 一句话定位。"""
    paths = load_paths().get("levels", [])
    by_code = {l.get("level"): l for l in paths}
    out = []
    for code in LEVEL_ORDER:
        lv = by_code.get(code, {})
        out.append({
            "level": code,
            "name": LEVEL_NAME.get(code, ""),
            "tagline": lv.get("tagline", ""),
        })
    return out

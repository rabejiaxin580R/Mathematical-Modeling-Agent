"""用户档案：轻量本地档案（无密码、无鉴权），每个档案存为一个 JSON 文件。

档案记录昵称、头像、模式1 学习进度（点亮的知识点）、模式3 真题成绩。
仅供信任的单机/局域网环境使用，拿到 profile id 即视为该用户，勿暴露公网。
"""
import json
import time
import uuid
from pathlib import Path

from .config import config


def _path(profile_id: str) -> Path:
    safe = "".join(c for c in profile_id if c.isalnum() or c in "-_")
    return config.PROFILES_DIR / f"{safe}.json"


def new_id() -> str:
    return "p_" + uuid.uuid4().hex[:12]


def _empty_assessment() -> dict:
    """测评/分级默认空结构。level 为空表示尚未定级（首次进入学习模式时引导）。"""
    return {
        "level": "",         # L1..L5；空 = 未定级
        "source": "",        # test=做测评得出 / self=自选 / 空
        "score": None,       # 测评得分占比（自选时为 None）
        "per_module": {},    # 各模块正确率（测评时写）
        "wrong_concepts": [],
        "skipped": False,    # 是否点过「先逛逛/跳过」，跳过后不再每次弹出
        "taken_at": 0,
    }


def load(profile_id: str) -> dict | None:
    if not profile_id:
        return None
    p = _path(profile_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(profile: dict):
    config.ensure_dirs()
    profile["updated_at"] = time.time()
    p = _path(profile["id"])
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


VALID_ROLES = ("", "coding", "writing", "modeling")


def _norm_role(role: str | None) -> str:
    r = (role or "").strip()
    return r if r in VALID_ROLES else ""


def create(nickname: str, avatar: str = "", role: str = "") -> dict:
    now = time.time()
    profile = {
        "id": new_id(),
        "nickname": (nickname or "").strip()[:20] or "建模新手",
        "avatar": (avatar or "").strip()[:32] or "fox",
        # 分工偏好：""=无偏好（按能力评级自适应）/ coding=编程手 / writing=写作手 / modeling=建模手
        "role": _norm_role(role),
        "created_at": now,
        "updated_at": now,
        "learn": {"completed": [], "visited": []},
        "practice": {"attempts": []},
        "assessment": _empty_assessment(),
    }
    save(profile)
    return profile


def list_all() -> list[dict]:
    config.ensure_dirs()
    items = []
    for p in config.PROFILES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "id": data.get("id"),
                "nickname": data.get("nickname", "未命名"),
                "avatar": data.get("avatar", "fox"),
                "updated_at": data.get("updated_at", 0),
            })
        except (json.JSONDecodeError, OSError):
            continue
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items


def _ensure_shape(profile: dict):
    """容错：补全旧档案可能缺失的字段。"""
    profile["role"] = _norm_role(profile.get("role"))
    learn = profile.setdefault("learn", {})
    learn.setdefault("completed", [])
    learn.setdefault("visited", [])
    practice = profile.setdefault("practice", {})
    practice.setdefault("attempts", [])
    asm = profile.setdefault("assessment", _empty_assessment())
    for k, v in _empty_assessment().items():
        asm.setdefault(k, v)


def mark_learned(profile_id: str, chunk_id: str, learned: bool) -> dict | None:
    """点亮/取消点亮一个知识点，返回更新后的档案。"""
    profile = load(profile_id)
    if profile is None or not chunk_id:
        return None
    _ensure_shape(profile)
    completed = profile["learn"]["completed"]
    visited = profile["learn"]["visited"]
    if learned:
        if chunk_id not in completed:
            completed.append(chunk_id)
    else:
        if chunk_id in completed:
            completed.remove(chunk_id)
    # 看过即记入 visited（去重）
    if chunk_id not in visited:
        visited.append(chunk_id)
    save(profile)
    return profile


def mark_visited(profile_id: str, chunk_id: str) -> dict | None:
    profile = load(profile_id)
    if profile is None or not chunk_id:
        return None
    _ensure_shape(profile)
    visited = profile["learn"]["visited"]
    if chunk_id not in visited:
        visited.append(chunk_id)
        save(profile)
    return profile


def record_attempt(profile_id: str, attempt: dict) -> dict | None:
    """记录/更新一道真题的成绩。同一 problem_id 覆盖为最新一次。"""
    profile = load(profile_id)
    if profile is None:
        return None
    _ensure_shape(profile)
    attempts = profile["practice"]["attempts"]
    pid = attempt.get("problem_id")
    attempt["graded_at"] = time.time()
    # upsert：同题覆盖
    for i, a in enumerate(attempts):
        if a.get("problem_id") == pid:
            attempts[i] = attempt
            break
    else:
        attempts.append(attempt)
    save(profile)
    return profile


def upsert_step_score(profile_id: str, problem_id: str, step_id: str,
                      score: float, max_score: float, total_max: float) -> dict | None:
    """记录单步得分，自动累加到该题的 attempt。返回更新后的档案。"""
    profile = load(profile_id)
    if profile is None:
        return None
    _ensure_shape(profile)
    attempts = profile["practice"]["attempts"]
    attempt = next((a for a in attempts if a.get("problem_id") == problem_id), None)
    if attempt is None:
        attempt = {"problem_id": problem_id, "step_scores": [], "total": 0, "max": total_max}
        attempts.append(attempt)
    attempt["max"] = total_max
    steps = attempt.setdefault("step_scores", [])
    for s in steps:
        if s.get("step_id") == step_id:
            s["score"], s["max"] = score, max_score
            break
    else:
        steps.append({"step_id": step_id, "score": score, "max": max_score})
    attempt["total"] = sum(s.get("score", 0) for s in steps)
    attempt["graded_at"] = time.time()
    save(profile)
    return profile


# 掌握度等级（过关判定）
MASTERY_LEVELS = ("待加强", "基本掌握", "很好")
PASS_LEVELS = ("基本掌握", "很好")


def upsert_step_mastery(profile_id: str, problem_id: str, step_id: str,
                        mastery: str, total_steps: int = 0) -> dict | None:
    """记录单步掌握度（过关/掌握度模式，替代硬打分）。返回更新后的档案。

    practice.attempts[] 每题：
      {problem_id, steps:[{step_id, mastery, passed, updated_at}],
       passed_count, total_steps, updated_at}
    """
    profile = load(profile_id)
    if profile is None:
        return None
    _ensure_shape(profile)
    if mastery not in MASTERY_LEVELS:
        mastery = "待加强"
    passed = mastery in PASS_LEVELS

    attempts = profile["practice"]["attempts"]
    attempt = next((a for a in attempts if a.get("problem_id") == problem_id), None)
    if attempt is None:
        attempt = {"problem_id": problem_id, "steps": []}
        attempts.append(attempt)
    steps = attempt.setdefault("steps", [])
    now = time.time()
    for s in steps:
        if s.get("step_id") == step_id:
            s["mastery"], s["passed"], s["updated_at"] = mastery, passed, now
            break
    else:
        steps.append({"step_id": step_id, "mastery": mastery, "passed": passed, "updated_at": now})
    attempt["passed_count"] = sum(1 for s in steps if s.get("passed"))
    if total_steps:
        attempt["total_steps"] = total_steps
    attempt["updated_at"] = now
    save(profile)
    return profile


def set_assessment(profile_id: str, level: str, source: str,
                   score: float | None = None, per_module: dict | None = None,
                   wrong_concepts: list | None = None) -> dict | None:
    """写入测评/自选定级结果，返回更新后的档案。"""
    profile = load(profile_id)
    if profile is None or not level:
        return None
    _ensure_shape(profile)
    profile["assessment"].update({
        "level": level,
        "source": source,
        "score": score,
        "per_module": per_module or {},
        "wrong_concepts": wrong_concepts or [],
        "skipped": False,
        "taken_at": time.time(),
    })
    save(profile)
    return profile


def mark_assessment_skipped(profile_id: str) -> dict | None:
    """学生选择「先逛逛/跳过」：标记后学习模式不再每次弹出引导。"""
    profile = load(profile_id)
    if profile is None:
        return None
    _ensure_shape(profile)
    profile["assessment"]["skipped"] = True
    save(profile)
    return profile


def set_role(profile_id: str, role: str) -> dict | None:
    """更新分工偏好（编程手/写作手/建模手/无偏好），返回更新后的档案。"""
    profile = load(profile_id)
    if profile is None:
        return None
    _ensure_shape(profile)
    profile["role"] = _norm_role(role)
    save(profile)
    return profile


# ── 自适应回答风格：把「能力评级 + 分工偏好」翻译成给助教的行文指令 ──

# 各能力等级 → 讲解深度侧重（对应 assessment.py 的 L1..L5 与「一句话总结/举例/公式/代码」等模块）
_LEVEL_STYLE = {
    "L1": "对方是**萌新**：多用「一句话总结」开头点题、多「举个例子」和生活化类比，"
          "少堆数学公式与术语；出现术语要顺带一句白话解释；代码给最小可跑版本并逐行注释。",
    "L2": "对方是**入门**：先给「一句话总结」，配直观例子，再引入必要的公式；"
          "术语第一次出现时简单解释；代码保持简洁并加注释。",
    "L3": "对方是**进阶**：正常给出思路、公式与代码，例子按需补充，不必事事从零解释。",
    "L4": "对方**较熟练**：可以直接上核心数学公式与完整代码，精简铺垫，多讲权衡与坑点。",
    "L5": "对方是**高手**：直接给严谨的数学公式、推导与工程化代码，聚焦深层原理、"
          "复杂度与优化，省去基础解释。",
}

# 分工偏好 → 回答侧重
_ROLE_STYLE = {
    "coding": "对方偏好**编程手**视角：优先给可运行的 Python 代码、实现细节、库用法与调试建议，"
              "公式够用即可，重点落在「怎么写出来、怎么跑通」。",
    "writing": "对方偏好**写作手**视角：优先讲论文/报告的结构、表述、图表规范与摘要写法，"
               "把方法讲清楚便于成文，代码点到为止。",
    "modeling": "对方偏好**建模手**视角：优先讲模型选择、假设、变量与公式推导、结果的合理性与敏感性，"
                "先把数学建模思路讲透，代码作为验证手段。",
}

_ROLE_LABEL = {"coding": "编程手", "writing": "写作手", "modeling": "建模手"}


def style_directive(level: str = "", role: str = "") -> str:
    """把能力评级 + 分工偏好拼成一段「回答风格」系统指令，供助教自适应作答。

    level：L1..L5（空=未定级，按 L3 中性处理）；——决定回答的**难度深度**
    role：coding/writing/modeling（空=无偏好，仅按评级自适应）；——决定回答的**内容侧重**
    两者都为空时返回空串（保持原有默认行为）。
    """
    level = level if level in _LEVEL_STYLE else ""
    role = _norm_role(role)
    if not level and not role:
        return ""
    parts = ["# 面向当前用户的回答侧重",
             "（**难度深度**由能力评级 L1..L5 决定，**内容侧重**由分工偏好决定，两者独立。）"]
    if level:
        parts.append("- " + _LEVEL_STYLE[level])
        # 按评级追加知识库使用方法
        if level == "L1":
            parts.append("- 检索知识库后，**优先引用**「一句话总结」和「举个例子」部分来讲，"
                         "公式只保留核心一两个并用白话解释。")
        elif level == "L2":
            parts.append("- 检索知识库后，先用「一句话总结」点题，再讲必要公式，配上例子。")
        elif level in ("L4", "L5"):
            parts.append("- 检索知识库后，**优先引用**数学公式、推导与代码实现，例子只作为辅助参考。")
    if role:
        parts.append("- " + _ROLE_STYLE[role])
    parts.append(
        "- 以上是**表达侧重**，不改变答案的正确性与完整性：该有的关键公式/代码/结论不能因迁就而省略，"
        "只调整详略、顺序与切入角度。"
    )
    return "\n".join(parts)

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


def create(nickname: str, avatar: str = "") -> dict:
    now = time.time()
    profile = {
        "id": new_id(),
        "nickname": (nickname or "").strip()[:20] or "建模新手",
        "avatar": (avatar or "").strip()[:32] or "fox",
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

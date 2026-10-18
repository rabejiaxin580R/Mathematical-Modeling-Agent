"""做题会话存档：把一道题的「题目快照 + 工作目录 + 进度 + 每步对话 id」存成一个 JSON。

与对话存档（storage.py）分开：这里存的是做题驾驶舱的整体状态，用于刷新/下次进来后
在立题页「我的做题存档」里点「继续」恢复——无需重新拆题，且每步对话接得上原上下文。
"""
import json
import time
import uuid
import os
import threading
import re
import math
from datetime import datetime
from copy import deepcopy

from .config import config
from . import storage

# The app runs one local worker. Serialize read/modify/write operations and replace
# files atomically so a page save cannot erase a concurrently confirmed result.
lock = threading.RLock()


def _path(session_id: str):
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return config.SOLVE_SESSIONS_DIR / f"{safe}.json"


def new_id() -> str:
    return "sv_" + uuid.uuid4().hex[:10]


def load(session_id: str) -> dict | None:
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("id") != session_id:
            return None
        return data
    except (json.JSONDecodeError, UnicodeError, OSError):
        return None


def save(session: dict) -> dict:
    config.ensure_dirs()
    session["updated_at"] = time.time()
    p = _path(session["id"])
    tmp = p.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)
    return session


def upsert(data: dict) -> dict:
    with lock:
        return _upsert(data)


def _upsert(data: dict) -> dict:
    """Only UI fields are writable here; confirmations/evidence stay server-owned."""
    sid = data.get("id")
    if sid and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sid):
        raise ValueError("无效存档标识")
    if data.get("run_id") and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", data["run_id"]):
        raise ValueError("无效运行标识")
    existing = load(sid) if sid else None
    if existing is None:
        if sid and _path(sid).exists():
            raise ValueError("历史存档无法读取，已保留原文件；请先恢复存档，再继续保存。")
        if sid and data.get("ui_version") is not None:
            raise ValueError("历史存档不存在，已阻止重新创建；当前输入仍保留。")
        existing = {
            "id": sid or new_id(),
            "created_at": time.time(),
        }
    elif data.get("ui_version") is not None and data["ui_version"] != existing.get("ui_version", 0):
        raise ValueError("存档已在另一页面修改，请刷新后继续；当前输入仍保留。")
    current_problem = existing.get("problem")
    incoming_problem = data.get("problem")
    if isinstance(current_problem, dict) and isinstance(incoming_problem, dict):
        current_id, incoming_id = current_problem.get("id"), incoming_problem.get("id")
        if current_id and incoming_id and current_id != incoming_id:
            raise ValueError("存档与题目不一致，已阻止覆盖；当前输入仍保留。")
    existing.update({
        "title": data.get("title", existing.get("title", "我的题目")),
        "problem": existing.get("problem") or data.get("problem"),
        "run_id": data.get("run_id", existing.get("run_id", "")),
        "files": data.get("files", existing.get("files", [])),
        "progress": data.get("progress", existing.get("progress", {"seen": [], "cur_step": -1})),
        "step_convs": {**(data.get("step_convs") or {}), **(existing.get("step_convs") or {})},
    })
    for key in ("initial_thought", "student_question", "statement", "workdir", "editor_state", "input_drafts", "is_example"):
        if key in data and (key not in ("initial_thought", "statement", "is_example") or key not in existing):
            existing[key] = deepcopy(data[key])
    existing["ui_version"] = existing.get("ui_version", 0) + 1
    if not existing.get("workdir") and existing.get("run_id"):
        folder = config.RUNS_DIR / existing["run_id"]
        folder.mkdir(parents=True, exist_ok=True)
        existing["workdir"] = str(folder)
    return save(existing)


def _timestamp(value, fallback=0) -> float:
    """Older archives used ISO text; the UI and sort both require epoch seconds."""
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            # Legacy local timestamps were written in the server's local time.
            stamp = parsed.timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            return float(fallback)
    return stamp if math.isfinite(stamp) else float(fallback)


def list_all() -> list[dict]:
    config.ensure_dirs()
    items = []
    for p in config.SOLVE_SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            prog = data.get("progress", {}) or {}
            steps = (data.get("problem", {}) or {}).get("steps", []) or []
            outcomes = [data.get("outcomes", {}).get(step["id"], {}) for step in steps]
            items.append({
                "id": data.get("id"),
                "title": data.get("title", "我的题目"),
                "updated_at": _timestamp(data.get("updated_at"), p.stat().st_mtime),
                "total_steps": len(steps),
                "seen_count": len(prog.get("seen", []) or []),
                "confirmed_count": sum(x.get("status") == "confirmed" for x in data.get("outcomes", {}).values()),
                "successful_evidence_count": sum(e.get("status") == "success" for e in data.get("evidence", [])),
                "revised_thought_count": sum(o.get("status") == "confirmed" and o.get("verdict") in ("partial", "unsupported") for o in outcomes),
                "needs_review_count": sum(o.get("status") == "needs_review" for o in outcomes),
                "project_complete": bool(steps) and all(o.get("status") == "confirmed" for o in outcomes),
                "current_step": prog.get("cur_step", -1),
                "current_title": next((s.get("title", "") for i, s in enumerate(steps) if i == prog.get("cur_step")), "尚未开始"),
            })
        except (json.JSONDecodeError, OSError, AttributeError, TypeError, KeyError):
            # An invalid legacy file must not hide every healthy project.
            continue
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items


def delete(session_id: str) -> bool:
    """删除存档，并一并删除其每步对话（落在 SOLVE_CONVERSATIONS_DIR）。"""
    data = load(session_id)
    if data:
        for conv_id in (data.get("step_convs", {}) or {}).values():
            if conv_id:
                storage.delete(conv_id, base=config.SOLVE_CONVERSATIONS_DIR)
    p = _path(session_id)
    if p.exists():
        p.unlink()
        return True
    return False

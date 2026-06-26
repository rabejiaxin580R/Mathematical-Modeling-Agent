"""做题会话存档：把一道题的「题目快照 + 工作目录 + 进度 + 每步对话 id」存成一个 JSON。

与对话存档（storage.py）分开：这里存的是做题驾驶舱的整体状态，用于刷新/下次进来后
在立题页「我的做题存档」里点「继续」恢复——无需重新拆题，且每步对话接得上原上下文。
"""
import json
import time
import uuid

from .config import config
from . import storage


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
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(session: dict) -> dict:
    config.ensure_dirs()
    session["updated_at"] = time.time()
    p = _path(session["id"])
    p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    return session


def upsert(data: dict) -> dict:
    """前端持全量状态，这里只负责落盘。无 id 则新建。"""
    sid = data.get("id")
    existing = load(sid) if sid else None
    if existing is None:
        existing = {
            "id": sid or new_id(),
            "created_at": time.time(),
        }
    existing.update({
        "title": data.get("title", existing.get("title", "我的题目")),
        "problem": data.get("problem", existing.get("problem")),
        "run_id": data.get("run_id", existing.get("run_id", "")),
        "files": data.get("files", existing.get("files", [])),
        "progress": data.get("progress", existing.get("progress", {"seen": [], "cur_step": -1})),
        "step_convs": data.get("step_convs", existing.get("step_convs", {})),
    })
    return save(existing)


def list_all() -> list[dict]:
    config.ensure_dirs()
    items = []
    for p in config.SOLVE_SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            prog = data.get("progress", {}) or {}
            steps = (data.get("problem", {}) or {}).get("steps", []) or []
            items.append({
                "id": data.get("id"),
                "title": data.get("title", "我的题目"),
                "updated_at": data.get("updated_at", 0),
                "total_steps": len(steps),
                "seen_count": len(prog.get("seen", []) or []),
            })
        except (json.JSONDecodeError, OSError):
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

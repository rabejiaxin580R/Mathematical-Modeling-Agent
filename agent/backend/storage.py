"""对话存档：每个会话存为一个 JSON 文件，便于学习者回溯。

默认存到 config.CONVERSATIONS_DIR（独立练习/通用）；做题会话（conv["agent"]=="solve"）
通过 base 参数或 save() 的自动路由存到 config.SOLVE_CONVERSATIONS_DIR，与独立练习物理隔离。
"""
import json
import time
import uuid
from pathlib import Path

from .config import config


def _base_dir(base: Path | None) -> Path:
    return base or config.CONVERSATIONS_DIR


def _path(conversation_id: str, base: Path | None = None) -> Path:
    safe = "".join(c for c in conversation_id if c.isalnum() or c in "-_")
    return _base_dir(base) / f"{safe}.json"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def load(conversation_id: str, base: Path | None = None) -> dict | None:
    p = _path(conversation_id, base)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(conversation: dict, base: Path | None = None):
    config.ensure_dirs()
    conversation["updated_at"] = time.time()
    # 自动路由：做题会话落到独立目录（即便调用方未显式传 base，也能正确隔离）
    if base is None and conversation.get("agent") == "solve":
        base = config.SOLVE_CONVERSATIONS_DIR
    p = _path(conversation["id"], base)
    p.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")


def create(base: Path | None = None) -> dict:
    conv = {
        "id": new_id(),
        "title": "新对话",
        "created_at": time.time(),
        "updated_at": time.time(),
        "messages": [],  # [{role, content, events?}]
    }
    save(conv, base)
    return conv


def list_all(base: Path | None = None) -> list[dict]:
    config.ensure_dirs()
    items = []
    for p in _base_dir(base).glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "id": data.get("id"),
                "title": data.get("title", "未命名"),
                "updated_at": data.get("updated_at", 0),
                "message_count": len(data.get("messages", [])),
            })
        except (json.JSONDecodeError, OSError):
            continue
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items


def delete(conversation_id: str, base: Path | None = None) -> bool:
    p = _path(conversation_id, base)
    if p.exists():
        p.unlink()
        return True
    return False

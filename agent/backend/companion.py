"""Source-backed project context and local profile preferences.

Only the companion database is writable here. Profile IDs provide local data
separation, not authentication; source research and student text stay untouched.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .config import config
from . import profiles, solve_sessions, storage

router = APIRouter()
SourceKind = Literal["initial_thought", "outcome", "evidence", "artifact", "message"]
Detail = Literal["balanced", "concise", "detailed"]
DEFAULT_PREFERENCES = {"detail": "balanced", "examples": False, "revision": 0}


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: SourceKind
    id: str = Field(min_length=1, max_length=180)
    step_id: str = Field(default="", max_length=100)


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: str = Field(min_length=1, max_length=100)
    step_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    source: Source | None = None
    operation_id: str = Field(min_length=1, max_length=160)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("记忆内容不能为空")
        return value


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=1)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value):
        return MemoryCreate.nonempty(value)


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detail: Detail
    examples: StrictBool
    expected_revision: int = Field(ge=0)


def _identity(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value or ""))


def _profile(pid):
    if not _identity(pid) or not isinstance(profiles.load(pid), dict):
        raise HTTPException(404, "本地档案不存在")


def _session(sid):
    if not _identity(sid):
        raise HTTPException(404, "项目不存在")
    session = solve_sessions.load(sid)
    if session is None:
        raise HTTPException(404, "项目不存在")
    return session


def _step(session, step_id):
    result = next((s for s in session.get("problem", {}).get("steps", []) if s.get("id") == step_id), None)
    if result is None:
        raise HTTPException(404, "项目步骤不存在")
    return result


@contextmanager
def _db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA_DIR / "companion.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("CREATE TABLE IF NOT EXISTS preferences (pid TEXT PRIMARY KEY, detail TEXT NOT NULL, examples INTEGER NOT NULL, revision INTEGER NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, pid TEXT NOT NULL, sid TEXT NOT NULL, step_id TEXT NOT NULL,
            text TEXT NOT NULL, source TEXT, revision INTEGER NOT NULL, updated_at REAL NOT NULL,
            operation_id TEXT NOT NULL, request_hash TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
            UNIQUE(pid, sid, operation_id))""")
        conn.execute("CREATE INDEX IF NOT EXISTS memories_owner ON memories(pid, sid, deleted)")
        conn.commit()
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _memory(row):
    return {"id": row["id"], "text": row["text"], "revision": row["revision"],
            "step_id": row["step_id"], "source": json.loads(row["source"]) if row["source"] else None,
            "updated_at": row["updated_at"]}


def _preferences(conn, pid):
    row = conn.execute("SELECT * FROM preferences WHERE pid=?", (pid,)).fetchone()
    return {"detail": row["detail"], "examples": bool(row["examples"]), "revision": row["revision"]} if row else dict(DEFAULT_PREFERENCES)


def source_record(session, kind, source_id):
    step_id, title, label, value = "", "", "", None
    if kind == "initial_thought" and source_id == session["id"]:
        title, label, value = "最初的想法", "项目原始想法", session.get("initial_thought", "")
    elif kind == "outcome":
        step = _step(session, source_id)
        value = session.get("outcomes", {}).get(source_id)
        step_id, title, label = source_id, step.get("title", source_id) + "的研究判断", "研究成果原记录"
    elif kind == "evidence":
        value = next((e for e in session.get("evidence", []) if e.get("id") == source_id), None)
        if value is not None:
            step_id, title, label = value.get("step_id", ""), value.get("purpose") or "验证记录", "验证原记录"
    elif kind == "artifact":
        artifact = next((a for a in session.get("workflow", {}).get("artifacts", []) if a.get("id") == source_id), None)
        if artifact is not None:
            value = artifact.get("content", "")
            step_id, title, label = artifact.get("step_id", ""), artifact.get("title") or "研究材料", "研究材料原文"
    elif kind == "message":
        for step, cid in session.get("step_convs", {}).items():
            conv = storage.load(cid, base=config.SOLVE_CONVERSATIONS_DIR)
            if not conv or conv.get("id") != cid or conv.get("solve_session_id") not in (None, "", session["id"]) or conv.get("step_id") not in (None, "", step):
                continue
            found = next((m for m in conv.get("messages", []) if m.get("id") == source_id), None)
            if found is not None:
                value, step_id = found.get("content", ""), step
                title = "学生原话" if found.get("role") == "user" else "对话原文"
                label = "系统开场原文" if found.get("origin") == "system_kickoff" else "对话消息原文"
                break
    if value is None:
        raise HTTPException(404, "来源不属于本项目或已不存在")
    if step_id:
        _step(session, step_id)
    return {"title": title, "content": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2),
            "source": {"kind": kind, "id": source_id, "step_id": step_id}, "source_label": label}


def _records(session, step_id):
    records, seen = [], set()
    def add(kind, identity, title, text, source_kind=None, needs_review=False):
        key = (kind, identity)
        if key in seen or not str(text).strip():
            return
        try:
            original = source_record(session, source_kind or kind, identity)
        except HTTPException:
            return
        seen.add(key)
        records.append({"id": kind + ":" + identity, "kind": kind, "title": title, "text": str(text)[:600],
                        "source": original["source"], "source_label": original["source_label"], "needs_review": needs_review})

    steps = session.get("problem", {}).get("steps", [])
    outcomes = session.get("outcomes", {})
    for step in steps:
        outcome = outcomes.get(step["id"], {})
        if outcome.get("status") == "needs_review":
            add("outcome", step["id"], "待复核：" + step.get("title", "本步成果"), outcome.get("decision") or "研究条件已变化，请复核本步判断。", needs_review=True)
    for evidence in reversed(session.get("evidence", [])):
        if evidence.get("step_id") == step_id and evidence.get("stale"):
            add("evidence", evidence["id"], "验证依据需要更新", evidence.get("purpose") or "这条验证记录对应的研究条件已经变化。", needs_review=True)
            break
    current = outcomes.get(step_id, {})
    if current.get("decision"):
        add("outcome", step_id, "本步判断" if current.get("status") == "confirmed" else "本步待审判断", current["decision"], needs_review=current.get("status") == "needs_review")
    confirmed = sorted(((key, value) for key, value in outcomes.items() if value.get("status") == "confirmed"),
                       key=lambda item: item[1].get("confirmed_at") or 0, reverse=True)
    if confirmed:
        key, outcome = confirmed[0]
        add("outcome", key, "最近确认的判断", outcome.get("decision", ""))
    add("initial_thought", session["id"], "最初的想法", session.get("initial_thought", ""))
    for artifact in reversed(session.get("workflow", {}).get("artifacts", [])):
        if artifact.get("step_id") != step_id or artifact.get("kind") != "note":
            continue
        note = artifact.get("metadata", {}).get("note", {})
        questions = note.get("questions") or []
        if questions:
            add("question", artifact["id"], "尚待回答的问题", "\n".join(str(q) for q in questions),
                source_kind="artifact", needs_review=artifact.get("status") == "needs_review")
        elif artifact.get("status") == "needs_review":
            add("artifact", artifact["id"], "笔记来源待核对", artifact.get("content", ""), needs_review=True)
    return records[:8]


@router.get("/api/solve/sessions/{sid}/companion-context")
def get_context(sid: str, pid: str = Query(min_length=1, max_length=100), step_id: str = Query(default="", max_length=100)):
    _profile(pid)
    with solve_sessions.lock, storage.lock:
        session = _session(sid)
        steps = session.get("problem", {}).get("steps", [])
        if not step_id:
            index = session.get("progress", {}).get("cur_step", 0)
            index = index if isinstance(index, int) and 0 <= index < len(steps) else 0
            step_id = steps[index]["id"] if steps else ""
        step = _step(session, step_id)
        records = _records(session, step_id)
    with _db() as conn:
        memories = [_memory(row) for row in conn.execute(
            "SELECT * FROM memories WHERE pid=? AND sid=? AND deleted=0 ORDER BY updated_at DESC, id", (pid, sid))]
        preferences = _preferences(conn, pid)
    return {"session_id": sid, "step_id": step_id, "step_title": step.get("title", ""),
            "revision": session.get("revision", 0), "records": records, "memories": memories, "preferences": preferences}


@router.get("/api/solve/sessions/{sid}/companion-source")
def get_source(sid: str, kind: SourceKind, source_id: str = Query(min_length=1, max_length=180)):
    with solve_sessions.lock, storage.lock:
        return source_record(_session(sid), kind, source_id)


@router.post("/api/solve/sessions/{sid}/companion-memories", status_code=201)
def create_memory(sid: str, request: MemoryCreate):
    _profile(request.pid)
    with solve_sessions.lock, storage.lock:
        session = _session(sid)
        _step(session, request.step_id)
        source = None
        if request.source:
            source = source_record(session, request.source.kind, request.source.id)["source"]
            if request.source.step_id != source["step_id"]:
                raise HTTPException(422, "来源步骤与原记录不一致")
    content = {"step_id": request.step_id, "text": request.text, "source": source}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT * FROM memories WHERE pid=? AND sid=? AND operation_id=?",
                                (request.pid, sid, request.operation_id)).fetchone()
        if previous:
            if previous["request_hash"] != digest:
                raise HTTPException(409, "同一操作标识不能用于不同内容")
            if previous["deleted"]:
                raise HTTPException(410, "这条记忆已删除，不会重复创建")
            return _memory(previous)
        count = conn.execute("SELECT COUNT(*) FROM memories WHERE pid=? AND sid=? AND deleted=0", (request.pid, sid)).fetchone()[0]
        if count >= 100:
            raise HTTPException(422, "本项目已保存 100 条记忆，请先整理已有内容")
        identity = "cm_" + uuid.uuid4().hex
        conn.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 0)",
                     (identity, request.pid, sid, request.step_id, request.text,
                      json.dumps(source, ensure_ascii=False) if source else None, time.time(), request.operation_id, digest))
        return _memory(conn.execute("SELECT * FROM memories WHERE id=?", (identity,)).fetchone())


def _owned_memory(conn, sid, mid, pid):
    row = conn.execute("SELECT * FROM memories WHERE id=? AND sid=? AND pid=? AND deleted=0", (mid, sid, pid)).fetchone()
    if row is None:
        raise HTTPException(404, "记忆不存在")
    return row


@router.patch("/api/solve/sessions/{sid}/companion-memories/{mid}")
def update_memory(sid: str, mid: str, request: MemoryUpdate):
    _profile(request.pid)
    session = _session(sid)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _owned_memory(conn, sid, mid, request.pid)
        _step(session, row["step_id"])
        if row["revision"] != request.expected_revision:
            raise HTTPException(409, {"message": "记忆已被修改，请读取最新内容；当前输入仍保留。", "current": _memory(row)})
        conn.execute("UPDATE memories SET text=?, revision=revision+1, updated_at=? WHERE id=?", (request.text, time.time(), mid))
        return _memory(conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone())


@router.delete("/api/solve/sessions/{sid}/companion-memories/{mid}")
def delete_memory(sid: str, mid: str, pid: str = Query(min_length=1, max_length=100), expected_revision: int = Query(ge=1)):
    _profile(pid)
    session = _session(sid)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _owned_memory(conn, sid, mid, pid)
        _step(session, row["step_id"])
        if row["revision"] != expected_revision:
            raise HTTPException(409, {"message": "记忆已被修改，请读取最新内容后再删除。", "current": _memory(row)})
        conn.execute("UPDATE memories SET deleted=1, text='', source=NULL, revision=revision+1, updated_at=? WHERE id=?", (time.time(), mid))
    return {"deleted": True, "id": mid}


@router.get("/api/profiles/{pid}/companion-preferences")
def get_preferences(pid: str):
    _profile(pid)
    with _db() as conn:
        return _preferences(conn, pid)


@router.patch("/api/profiles/{pid}/companion-preferences")
def update_preferences(pid: str, request: PreferencesUpdate):
    _profile(pid)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _preferences(conn, pid)
        if current["revision"] != request.expected_revision:
            raise HTTPException(409, {"message": "表达偏好已更新，请重新读取。", "current": current})
        result = {"detail": request.detail, "examples": request.examples, "revision": current["revision"] + 1}
        conn.execute("INSERT OR REPLACE INTO preferences VALUES (?, ?, ?, ?)", (pid, result["detail"], int(result["examples"]), result["revision"]))
        return result


def style_directive(pid):
    if not pid or not _identity(pid) or not isinstance(profiles.load(pid), dict):
        return ""
    try:
        with _db() as conn:
            preference = _preferences(conn, pid)
    except (sqlite3.Error, OSError):
        return ""
    if preference["detail"] == "balanced" and not preference["examples"]:
        return ""
    detail = {"balanced": "按问题需要安排解释详略。", "concise": "表达尽量简洁，先给关键结论与必要依据。",
              "detailed": "适当展开推理过程、关键假设与限制，保留清晰的层次。"}[preference["detail"]]
    examples = "必要时提供一个明确标为示例的小例子，不能把示例数据当作本项目证据。" if preference["examples"] else "示例按问题需要提供。"
    return "学生的表达偏好：" + detail + examples + "当前消息中的明确表达要求优先于这些默认偏好。这些偏好只影响表达，不改变辅导/助手模式、研究事实、学生确认权或完整性要求。"

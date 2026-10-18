"""Durable chat workers: HTTP readers only subscribe to the event journal.

The desktop server runs one process. SQLite records acknowledgements and events;
restarting the process never reruns a model call automatically.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
import json
import logging
import os
import sqlite3
import threading
import time
import uuid

from fastapi import HTTPException
from .config import config
from . import storage

logger = logging.getLogger(__name__)
MAX_WORKERS = max(1, int(os.getenv("CHAT_TASK_WORKERS", "4")))
MAX_QUEUED = max(0, int(os.getenv("CHAT_TASK_QUEUE", "16")))
MAX_EVENT_BYTES = 32 * 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_lock = threading.RLock()
_runs = {}
_initialized = set()
_pending = deque()
_running = 0


def _root():
    return str(config.DATA_DIR.resolve())


def _key(conversation_id, message_id):
    return _root(), conversation_id, message_id


@contextmanager
def _db(root=None):
    root = root or _root()
    os.makedirs(root, exist_ok=True)
    db = sqlite3.connect(os.path.join(root, "chat_tasks.sqlite3"), timeout=10)
    try:
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=NORMAL")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def recover():
    """Mark abandoned work interrupted. Recovery never contacts the model."""
    root = _root()
    if root in _initialized:
        return
    with storage.lock, _lock:
        if root in _initialized:
            return
        with _db(root) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, conversation_id TEXT, message_id TEXT, kind TEXT, created REAL, data TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_message ON tasks(kind, message_id)")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_recent ON tasks(created DESC)")
            db.execute("CREATE TABLE IF NOT EXISTS events (task_id TEXT, seq INTEGER, data TEXT, PRIMARY KEY(task_id, seq))")
            for task_id, raw in db.execute("SELECT id, data FROM tasks").fetchall():
                data = json.loads(raw)
                if data.get("status") not in TERMINAL:
                    journal = [json.loads(row[0]) for row in db.execute(
                        "SELECT data FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
                    _restore_partial(data, journal)
                    data.update(status="interrupted", updated_at=time.time(),
                                error="服务已重启，本轮生成已中断；已保存内容仍可查看，请手动发送新消息继续。")
                    db.execute("UPDATE tasks SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), task_id))
        _initialized.add(root)


def _restore_partial(task, events):
    """Recover a crashed turn into normal history without replaying any tools."""
    if task["kind"] not in {"chat", "solve"}:
        return  # Practice retains its entire history in the task's request/journal.
    base = config.SOLVE_CONVERSATIONS_DIR if task["kind"] == "solve" else None
    conv = storage.load(task["conversation_id"], base=base)
    if not conv:
        return  # A deleted conversation is never resurrected.
    user = next((message for message in conv["messages"]
                 if message.get("client_message_id") == task["client_message_id"]), None)
    if not user:
        return
    if user.get("reply_status") == "completed":
        return  # The final conversation save won the race with the process exit.
    if any(message.get("recovered_from_task") == task["id"] for message in conv["messages"]):
        return
    if user.get("reply_status") == "interrupted" and any(
            message.get("reply_to") == user.get("id") for message in conv["messages"]):
        return  # The generator's finally block already saved this partial reply.
    text, display_events, calls, results = "", [], [], []
    for event in events:
        kind = event.get("type")
        if kind == "token":
            text += event.get("text", "")
        elif kind == "done" and event.get("content"):
            text = event["content"]
        elif kind in {"search", "tool_call", "tool_result"}:
            display_events.append(event)
            if kind == "tool_call" and event.get("call_id"):
                calls.append({"id": event["call_id"], "type": "function", "function": {
                    "name": event["name"], "arguments": json.dumps(event.get("arguments") or {}, ensure_ascii=False)}})
            elif kind == "tool_result" and event.get("call_id"):
                results.append({"role": "tool", "tool_call_id": event["call_id"], "content": event.get("content", "")})
    paired = {call["id"] for call in calls} & {result["tool_call_id"] for result in results}
    if text or display_events:
        reply = {"role": "assistant", "origin": "assistant", "content": text,
                 "events": display_events, "reply_to": user.get("id"), "recovered_from_task": task["id"]}
        if paired:
            reply["tool_calls"] = [call for call in calls if call["id"] in paired]
        conv["messages"].append(reply)
        conv["messages"].extend(result for result in results if result["tool_call_id"] in paired)
    user["reply_status"] = "interrupted"
    storage.save(conv)


def _public(run):
    with run["condition"]:
        return deepcopy(run["task"])


def _save(run):
    task = run["task"]
    with _db(run["root"]) as db:
        db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                   (task["id"], task["conversation_id"], task["client_message_id"], task["kind"],
                    task["created_at"], json.dumps(task, ensure_ascii=False)))


def _append(run, event, raw_frame=None):
    raw = json.dumps(event, ensure_ascii=False)
    size = len(raw.encode("utf-8"))
    if run["bytes"] + size > MAX_EVENT_BYTES:
        raise RuntimeError("本轮输出超过保存上限，请拆分为更短的问题。")
    with run["condition"]:
        seq = run["task"]["cursor"] + 1
        # Persist first: every delivered cursor remains recoverable after restart.
        with _db(run["root"]) as db:
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (run["task"]["id"], seq, raw))
        run["events"].append(raw_frame if raw_frame is not None else "data: " + raw + "\n\n")
        run["task"]["cursor"] = seq
        run["task"]["updated_at"] = time.time()
        run["bytes"] += size
        run["condition"].notify_all()


def check_capacity():
    with _lock:
        if _running >= MAX_WORKERS and sum(not r["done"] for r in _pending) >= MAX_QUEUED:
            raise HTTPException(429, "后台对话队列已满，请稍后再发送。", headers={"Retry-After": "3"})


def has_active(conversation_id):
    with _lock:
        return any(k[:2] == (_root(), conversation_id) and not v["done"] for k, v in _runs.items())


def existing(conversation_id, message_id):
    with _lock:
        return _runs.get(_key(conversation_id, message_id))


def find(message_id, kind, conversation_id=None):
    recover()
    with _db() as db:
        query = "SELECT id FROM tasks WHERE message_id=? AND kind=?"
        args = [message_id, kind]
        if conversation_id:
            query += " AND conversation_id=?"
            args.append(conversation_id)
        row = db.execute(query + " ORDER BY created DESC LIMIT 1", args).fetchone()
    return get(row[0]) if row else None


def get(task_id, after=None, limit=1000):
    recover()
    with _db() as db:
        row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        task = json.loads(row[0])
        cursor = db.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
        task["cursor"] = cursor
        if after is not None:
            rows = db.execute("SELECT seq, data FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (task_id, max(0, after), max(1, min(limit, 2000)))).fetchall()
            task["events"] = [{"id": seq, "event": json.loads(raw)} for seq, raw in rows]
            task["next_cursor"] = rows[-1][0] if rows else min(max(0, after), cursor)
            task["has_more"] = task["next_cursor"] < cursor
    return task


def list_tasks(conversation_id=None, active_only=False, message_id=None, limit=100, include_request=True):
    recover()
    limit = max(1, min(limit, 200))
    with _db() as db:
        filters, args = [], []
        if conversation_id:
            filters.append("conversation_id=?")
            args.append(conversation_id)
        if message_id:
            filters.append("message_id=?")
            args.append(message_id)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = db.execute("SELECT data FROM tasks" + where + " ORDER BY created DESC", args)
        result = []
        for row in rows:
            task = json.loads(row[0])
            if active_only and task["status"] in TERMINAL:
                continue
            if not include_request:
                task.pop("request", None)
            result.append(task)
            if len(result) >= limit:
                break
    return result


def invalidate(conversation_id, reason, keep_message_ids=()):
    """Retain journals for audit, but never replay turns removed from history."""
    recover()
    retained = set(keep_message_ids)
    removed = []
    with _lock, _db() as db:
        rows = db.execute("SELECT id, data FROM tasks WHERE conversation_id=? AND kind='chat'",
                          (conversation_id,)).fetchall()
        for task_id, raw in rows:
            task = json.loads(raw)
            if task["client_message_id"] in retained:
                continue
            task.update(invalidated=True, invalidated_reason=reason, invalidated_at=time.time())
            db.execute("UPDATE tasks SET data=? WHERE id=?", (json.dumps(task, ensure_ascii=False), task_id))
            run = _runs.get(_key(conversation_id, task["client_message_id"]))
            if run:
                with run["condition"]:
                    run["task"].update(task)
            removed.append(task["client_message_id"])
    return removed


def cancel(conversation_id, message_id):
    """Only an explicit POST cancels; closing a reader never invokes this."""
    with _lock:
        run = _runs.get(_key(conversation_id, message_id))
        if not run or run["done"]:
            return False
        run["cancel"].set()
        if run["task"]["status"] == "queued":
            run.pop("factory", None)
            _append(run, {"type": "cancelled"})
            run["done"] = True
            run["task"].update(status="cancelled", updated_at=time.time())
            with run["condition"]:
                run["condition"].notify_all()
        else:
            run["task"].update(status="cancelling", updated_at=time.time())
        _save(run)
        return True


def is_cancel_requested(conversation_id, message_id):
    run = existing(conversation_id, message_id)
    return bool(run and run["cancel"].is_set())


def discard_completed(conversation_id, message_id):
    """Compatibility: the old solve POST permits an explicit same-ID retry."""
    with _lock:
        key = _key(conversation_id, message_id)
        run = _runs.get(key)
        if run and run["done"]:
            del _runs[key]


def start(conversation_id, message_id, factory, *, kind="solve", request=None, title="", url=""):
    recover()
    key = _key(conversation_id, message_id)
    with _lock:
        if key in _runs:
            return _runs[key]
        check_capacity()
        for old in list(_runs):
            if _runs[old]["done"] and time.time() - _runs[old]["created"] > 600:
                del _runs[old]
        now = time.time()
        task = {"id": "chat_" + uuid.uuid4().hex, "kind": kind, "conversation_id": conversation_id,
                "client_message_id": message_id, "title": title or "AI 对话", "request": request or {}, "url": url,
                "status": "queued", "cursor": 0, "created_at": now, "updated_at": now}
        run = {"task": task, "root": key[0], "events": [], "bytes": 0, "done": False,
               "condition": threading.Condition(), "cancel": threading.Event(), "created": now,
               "factory": factory}
        _save(run)
        _runs[key] = run
        if _running < MAX_WORKERS:
            _launch(run)
        else:
            _pending.append(run)
        return run


def _launch(run):
    global _running
    _running += 1
    run["task"].update(status="running", updated_at=time.time())
    _save(run)
    thread = threading.Thread(target=_pump, args=(run,), daemon=True, name=run["task"]["id"])
    run["thread"] = thread
    thread.start()


def _pump(run):
    global _running
    source, success, failed = None, False, False
    try:
        source = run.pop("factory")()
        while not run["cancel"].is_set():
            try:
                chunk = next(source)
            except StopIteration:
                break
            if run["cancel"].is_set():
                break
            if "data:" not in str(chunk) and str(chunk).strip() and not str(chunk).startswith(":"):
                # Retain the original in-process transport's opaque frame compatibility.
                _append(run, {"type": "transport", "text": str(chunk)}, raw_frame=chunk)
                continue
            for frame in str(chunk).replace("\r\n", "\n").split("\n\n"):
                lines = [line[5:].lstrip() for line in frame.split("\n") if line.startswith("data:")]
                if not lines:
                    continue
                event = json.loads("\n".join(lines))
                if event.get("type") == "done":
                    success = not event.get("interrupted", False)
                if event.get("type") == "error":
                    failed = True
                    run["task"]["error"] = str(event.get("message") or "生成失败")
                _append(run, event)
    except Exception:
        logger.exception("后台对话生成失败")
        failed, success = True, False
        run["task"]["error"] = "对话生成失败，已保存的内容仍可查看。"
        try:
            _append(run, {"type": "error", "message": run["task"]["error"]})
        except Exception:
            logger.exception("后台对话错误事件保存失败")
    finally:
        try:
            # Close in the owning thread so partial-save finally blocks and ContextVars run.
            if source is not None and hasattr(source, "close"):
                source.close()
        except Exception:
            failed, success = True, False
            logger.exception("后台对话存档失败")
        with _lock:
            try:
                if run["cancel"].is_set():
                    status = "cancelled"
                    _append(run, {"type": "cancelled"})
                else:
                    status = "completed" if success else "failed" if failed else "interrupted"
                with run["condition"]:
                    run["task"].update(status=status, updated_at=time.time())
                    _save(run)
                    run["done"] = True
                    run["condition"].notify_all()
            finally:
                _running -= 1
                while _pending and _running < MAX_WORKERS:
                    following = _pending.popleft()
                    if not following["done"]:
                        _launch(following)


def stream(run):
    """Original data-only protocol, retained for existing unit callers."""
    index = 0
    while True:
        with run["condition"]:
            if index >= len(run["events"]) and not run["done"]:
                run["condition"].wait(timeout=10)
            events = run["events"][index:]
            index += len(events)
            done = run["done"]
        yield from events
        if done:
            return
        if not events:
            yield ": keepalive\n\n"


async def stream_async(task_id, after=0, *, include_ids=True):
    """Async subscription avoids pinning one Starlette worker per SSE connection."""
    cursor, heartbeat = max(0, after), time.monotonic()
    while True:
        task = await asyncio.to_thread(get, task_id, cursor)
        if task is None or task.get("invalidated"):
            return
        for record in task["events"]:
            cursor = record["id"]
            prefix = f"id: {cursor}\n" if include_ids else ""
            yield prefix + "data: " + json.dumps(record["event"], ensure_ascii=False) + "\n\n"
        if task["status"] in TERMINAL and not task["has_more"]:
            return
        if task["has_more"]:
            continue
        if time.monotonic() - heartbeat >= 10:
            yield ": keepalive\n\n"
            heartbeat = time.monotonic()
        await asyncio.sleep(0.15)


def wait_for_all(timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            threads = [v.get("thread") for v in _runs.values() if not v["done"]]
        if not threads:
            return
        for thread in threads:
            if thread:
                thread.join(max(0, deadline - time.monotonic()))

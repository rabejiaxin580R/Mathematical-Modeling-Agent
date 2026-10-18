"""Student-controlled excerpts and source-aware adoption of paper suggestions."""
from copy import deepcopy
import hashlib
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import solve_sessions, storage, workflow, solve_flow
from .config import config

router = APIRouter(prefix="/api/solve/sessions/{sid}/research")


class Excerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str
    message_id: str
    quote: str = Field(min_length=1, max_length=12000)
    idea: str = Field(min_length=1, max_length=4000)
    question: str = Field(default="", max_length=4000)
    operation_id: str = Field(min_length=1, max_length=160)


class Adoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    expected_target_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=1000000)
    operation_id: str = Field(min_length=1, max_length=160)


def _messages(data, step_id=None):
    result = []
    for step, cid in data.get("step_convs", {}).items():
        if step_id and step != step_id:
            continue
        conv = storage.load(cid, base=config.SOLVE_CONVERSATIONS_DIR)
        if not conv or conv.get("solve_session_id") not in (None, "", data["id"]) or conv.get("step_id") not in (None, "", step):
            continue
        for index, message in enumerate(conv.get("messages", [])):
            if message.get("role") not in {"user", "assistant"} or message.get("origin") == "system_kickoff" or message.get("reply_status") == "running":
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            identity = message.get("id") or "legacy:" + cid + ":" + str(index) + ":" + hashlib.sha256(content.encode()).hexdigest()[:16]
            attribution = "ai" if message["role"] == "assistant" else "student" if message.get("origin") in {"student", "user"} else "unverified"
            result.append({"id": identity, "content": content, "role": message["role"], "attribution": attribution, "step_id": step})
    return result


@router.get("/messages")
def messages(sid: str, step_id: str):
    with solve_sessions.lock, storage.lock:
        data = workflow._session(sid)
        solve_flow.step_index(data, step_id)
        return {"messages": _messages(data, step_id)[-40:]}


@router.post("/notes", status_code=201)
def excerpt(sid: str, request: Excerpt):
    if not request.idea.strip() or not request.quote.strip():
        raise HTTPException(422, "请保留一段原文，并写下你的理解。")
    digest = workflow._digest(request.model_dump(exclude={"operation_id"}))
    with solve_sessions.lock, storage.lock:
        data = workflow._session(sid)
        solve_flow.step_index(data, request.step_id)
        wf = workflow._ensure(data)
        previous = next((a for a in wf["artifacts"] if a.get("metadata", {}).get("excerpt_operation") == request.operation_id), None)
        if previous:
            if previous["metadata"].get("excerpt_digest") != digest:
                raise HTTPException(409, "这次摘录已保存，请查看笔记后继续修改。")
            return deepcopy(previous)
        message = next((m for m in _messages(data, request.step_id) if m["id"] == request.message_id), None)
        if not message or request.quote not in message["content"]:
            raise HTTPException(422, "摘录必须来自所选消息的原文。你的理解可以写在下方。")
        note = {"source_message_id": message["id"], "source_message_ids": [message["id"]],
                "quote": request.quote, "idea": request.idea.strip(), "attribution": message["attribution"],
                "source": message["attribution"], "questions": [request.question.strip()] if request.question.strip() else [],
                "student_original": request.quote if message["attribution"] == "student" else "", "reason": "", "assumptions": []}
        artifact = workflow._put_artifact(data, "note", request.step_id, request.idea.strip()[:80], workflow._note_markdown(note),
            [message["id"]], metadata={"note": note, "excerpt_operation": request.operation_id, "excerpt_digest": digest},
            author="student", status="needs_review" if message["attribution"] == "unverified" else "draft")
        workflow._invalidate(data, artifact)
        solve_sessions.save(data)
        return deepcopy(artifact)


def _safe_citations(items):
    result = []
    for citation in items:
        value = deepcopy(citation)
        for key in ("url", "pdf_url"):
            url = value.get(key, "") or ""
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                value[key] = ""
        result.append(value)
    return result


@router.get("/artifacts/{aid}/sources")
def sources(sid: str, aid: str):
    with solve_sessions.lock, storage.lock:
        data = workflow._session(sid)
        artifact = workflow._artifact(data, aid)
        messages_by_id = {m["id"]: m for m in _messages(data)}
        source_messages, missing = [], []
        for identity in artifact.get("source_message_ids", []):
            if identity in messages_by_id:
                source_messages.append(messages_by_id[identity])
            else:
                missing.append("来源消息 " + identity + " 暂不可核对")
        evidence = []
        for identity in artifact.get("evidence_ids", []):
            record = next((e for e in data.get("evidence", []) if e["id"] == identity), None)
            if record:
                evidence.append(deepcopy(record))
            else:
                missing.append("验证记录 " + identity + " 暂不可核对")
        notes = []
        metadata = artifact.get("metadata", {})
        for ref in metadata.get("note_refs", []):
            original = next((a for a in data.get("workflow", {}).get("artifacts", []) if a["id"] == ref["id"]), None)
            version = original if original and original["revision"] == ref["revision"] else next((v for v in (original or {}).get("versions", []) if v["revision"] == ref["revision"]), None)
            if version:
                notes.append({"id": ref["id"], "revision": ref["revision"], "title": (original or {}).get("title", "笔记"), "content": version["content"]})
            else:
                missing.append("笔记 " + ref["id"] + " 的对应版本暂不可核对")
        return {"artifact_id": aid, "revision": artifact["revision"], "messages": source_messages, "evidence": evidence,
                "notes": notes, "citations": _safe_citations(metadata.get("citations", [])), "missing": missing,
                "note_provenance_available": "note_refs" in metadata, "warnings": metadata.get("warnings", [])}


@router.post("/artifacts/{aid}/adopt")
def adopt(sid: str, aid: str, request: Adoption):
    if not request.content.strip():
        raise HTTPException(422, "请保留要写入的正文。")
    digest = workflow._digest(request.model_dump(exclude={"operation_id"}))
    with solve_sessions.lock:
        data = workflow._session(sid)
        suggestion = workflow._artifact(data, aid)
        for entry in suggestion.get("adoptions", []):
            if entry["operation_id"] == request.operation_id:
                if entry["digest"] != digest:
                    raise HTTPException(409, "这次采纳已经保存，请查看最新版本。")
                return {"artifact": deepcopy(workflow._artifact(data, entry["target_id"])), "adopted_revision": entry["revision"]}
        if suggestion.get("kind") != "paper" or not suggestion.get("suggested_for"):
            raise HTTPException(422, "这份材料不是论文修改建议。")
        target = workflow._artifact(data, suggestion["suggested_for"])
        if target.get("kind") != "paper" or target["step_id"] != suggestion["step_id"]:
            raise HTTPException(422, "建议与论文不属于同一步骤。")
        if suggestion["revision"] != request.expected_revision or target["revision"] != request.expected_target_revision:
            raise HTTPException(409, "论文已有新版本，你的合并文字仍保留。请重新对照后保存。")
        metadata = deepcopy(target.get("metadata", {}))
        for key in ("citations", "note_refs"):
            combined = metadata.get(key, []) + suggestion.get("metadata", {}).get(key, [])
            metadata[key] = list({workflow._digest(item): item for item in combined}.values())
        metadata["accepted_suggestion"] = {"id": aid, "revision": suggestion["revision"]}
        artifact = workflow._put_artifact(data, "paper", target["step_id"], target["title"], request.content,
            list(dict.fromkeys(target.get("source_message_ids", []) + suggestion.get("source_message_ids", []))),
            list(dict.fromkeys(target.get("evidence_ids", []) + suggestion.get("evidence_ids", []))), metadata,
            replace_id=target["id"], author="student")
        workflow._invalidate(data, artifact)
        suggestion.setdefault("adoptions", []).append({"operation_id": request.operation_id, "digest": digest, "target_id": target["id"], "revision": artifact["revision"]})
        suggestion["status"] = "adopted"
        workflow._event(data, "suggestion_adopted", artifact_id=target["id"], suggestion_id=aid, revision=artifact["revision"], step_id=target["step_id"])
        solve_sessions.save(data)
        return {"artifact": deepcopy(artifact), "adopted_revision": artifact["revision"]}

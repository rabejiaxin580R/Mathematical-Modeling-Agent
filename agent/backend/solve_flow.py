"""Student-owned decisions, immutable evidence and paper source snapshots.

Extends the existing solve session; no second project or workflow entity.
"""
import base64
import hashlib
import json
import time
import uuid
from copy import deepcopy
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import framework, solve_sessions as sessions, storage, paper_db
from .config import config

router = APIRouter()
OUTCOME_FIELDS = ("decision", "reason", "basis", "limits", "verdict", "not_applicable", "evidence_ids")
VERDICTS = {"pending", "supported", "partial", "unsupported", "insufficient"}


def attachment_run_id(sid, step_id):
    return 'attachments_' + hashlib.sha256(json.dumps([sid, step_id]).encode()).hexdigest()[:24]


def active_attachments(data, step_id):
    return [item for item in data.get('step_files', {}).get(step_id, []) if not item.get('removed_at')]


@router.post("/api/solve/sessions/{sid}/steps/{step_id}/attachments")
async def upload_discussion_attachment(sid: str, step_id: str, file: UploadFile = File(...)):
    data = get_session(sid)
    step_index(data, step_id)
    name = (file.filename or '').replace('\\', '/').split('/')[-1]
    safe = ''.join(c for c in name if c.isalnum() or c in '._- ').strip().rstrip('.')[:160]
    ext = Path(safe).suffix.lower()
    if ext not in config.UPLOAD_ALLOWED_EXTS:
        raise HTTPException(400, f"不支持的文件类型：{ext or '无扩展名'}。允许：{', '.join(sorted(config.UPLOAD_ALLOWED_EXTS))}")
    raw = await file.read(config.UPLOAD_MAX_BYTES + 1)
    if len(raw) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(400, f"文件过大（>{config.UPLOAD_MAX_BYTES // (1024*1024)}MB）")
    run_id = attachment_run_id(sid, step_id)
    storage_name = f"{Path(safe).stem}_{uuid.uuid4().hex[:12]}{ext}"
    root = (config.RUNS_DIR / run_id).resolve()
    if not root.is_relative_to(config.RUNS_DIR.resolve()):
        raise HTTPException(409, "当前步骤工作目录无效")
    root.mkdir(parents=True, exist_ok=True)
    with sessions.lock:
        current = get_session(sid)
        step_index(current, step_id)
        with (root / storage_name).open('xb') as output:
            output.write(raw)
        files = current.setdefault("step_files", {}).setdefault(step_id, [])
        item = {"id": "sf_" + uuid.uuid4().hex[:12], "filename": safe,
                "storage_name": storage_name, "size": len(raw), "created_at": time.time()}
        files.append(item)
        wf = current.setdefault('workflow', {})
        revisions = wf.setdefault('input_revisions', {})
        revisions[step_id] = revisions.get(step_id, 0) + 1
        sessions.save(current)
    return {"id": item["id"], "filename": safe, "size": len(raw)}


@router.get("/api/solve/sessions/{sid}/steps/{step_id}/attachments")
def list_discussion_attachments(sid: str, step_id: str):
    data = get_session(sid)
    step_index(data, step_id)
    return [{key: item.get(key) for key in ("id", "filename", "size", "created_at")}
            for item in active_attachments(data, step_id)]


@router.delete("/api/solve/sessions/{sid}/steps/{step_id}/attachments/{attachment_id}")
def remove_discussion_attachment(sid: str, step_id: str, attachment_id: str):
    with sessions.lock:
        data = get_session(sid)
        step_index(data, step_id)
        files = data.get("step_files", {}).get(step_id, [])
        item = next((f for f in files if f.get("id") == attachment_id), None)
        if not item:
            raise HTTPException(404, "附件不存在")
        if item.get('removed_at'):
            return {"ok": True}
        # Historical discussion and evidence retain their original bytes.
        item['removed_at'] = time.time()
        revisions = data.setdefault('workflow', {}).setdefault('input_revisions', {})
        revisions[step_id] = revisions.get(step_id, 0) + 1
        sessions.save(data)
        return {"ok": True}


def get_session(sid):
    data = sessions.load(sid)
    if not data:
        raise HTTPException(404, "做题存档不存在")
    return data


def step_index(data, step_id):
    for i, step in enumerate(data.get("problem", {}).get("steps", [])):
        if step.get("id") == step_id:
            return i
    raise HTTPException(404, "建模步骤不存在")


def update_outcome(sid, step_id, expected_revision, outcome, action="save"):
    with sessions.lock:
        data = get_session(sid)
        idx = step_index(data, step_id)
        if data.get("revision", 0) != expected_revision:
            raise HTTPException(409, "成果已更新，请重新读取后再确认。当前草稿仍保留。")
        outcomes = data.setdefault("outcomes", {})
        previous = outcomes.get(step_id, {})
        if action == "reopen":
            clean = {k: deepcopy(previous.get(k)) for k in OUTCOME_FIELDS}
        else:
            clean = {k: deepcopy(outcome.get(k, [] if k == "evidence_ids" else False if k == "not_applicable" else "")) for k in OUTCOME_FIELDS}
        clean["verdict"] = clean.get("verdict") or "pending"
        if clean["verdict"] not in VERDICTS:
            raise HTTPException(422, "请选择有效的结果判断")
        available = {x["id"] for x in data.get("evidence", [])}
        if any(e not in available for e in clean.get("evidence_ids", [])):
            raise HTTPException(422, "引用的验证记录不存在")
        changed = any(clean.get(k) != previous.get(k) for k in OUTCOME_FIELDS)
        if action == "confirm":
            if not (clean.get("decision") or "").strip() or not (clean.get("reason") or "").strip():
                raise HTTPException(422, "请填写本步决定和理由，再确认成果")
            for step in data["problem"]["steps"][:idx]:
                if outcomes.get(step["id"], {}).get("status") != "confirmed":
                    raise HTTPException(409, "请先确认前面的建模步骤")
            if not clean.get("not_applicable") and not ((clean.get("basis") or "").strip() or clean.get("evidence_ids")):
                raise HTTPException(422, "请补充推导、知识出处或验证记录；证据不足时也请说明限制")
        if changed or action == "reopen":
            for step in data["problem"]["steps"][idx:]:
                old = outcomes.get(step["id"])
                if old and old.get("status") == "confirmed":
                    old.setdefault("history", []).append({k: deepcopy(v) for k, v in old.items() if k != "history"})
                    old["status"] = "needs_review"
        status = "confirmed" if action == "confirm" else ("needs_review" if previous.get("history") or action == "reopen" else "draft")
        if not changed and action == "save":
            status = previous.get("status", "draft")
        clean.update(version=previous.get("version", 0) + (1 if changed else 0), status=status,
                     history=previous.get("history", []), confirmed_at=time.time() if status == "confirmed" else None)
        outcomes[step_id] = clean
        data["revision"] = data.get("revision", 0) + 1
        sessions.save(data)
        return data


def snapshot_inputs(sid, workdir, filenames=None):
    """Copy input bytes before execution. Missing/oversize files are explicit."""
    result = []
    if not workdir:
        return result
    root = Path(workdir).resolve()
    if not root.is_dir():
        return result
    files = [root / name for name in filenames] if filenames is not None else root.rglob("*")
    allowed = config.UPLOAD_ALLOWED_EXTS
    for p in files:
        p = p.resolve()
        if not p.is_relative_to(root) or not p.is_file() or p.suffix.lower() not in allowed:
            continue
        if any(part in {".venv", "venv", ".git", "node_modules", "__pycache__"} for part in p.relative_to(root).parts):
            continue
        name = p.relative_to(root).as_posix()
        if p.stat().st_size > config.UPLOAD_MAX_BYTES:
            result.append({"name": name, "unavailable": "文件超出快照大小上限，请手动提供研究所需数据"})
            continue
        raw = p.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        folder = config.SOLVE_SESSIONS_DIR / sid / "assets"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (digest + p.suffix.lower())
        if not target.exists():
            target.write_bytes(raw)
        result.append({"name": name, "sha256": digest, "asset": target.name})
    return result


def add_evidence(sid, step_id, payload, inputs=None):
    with sessions.lock:
        data = get_session(sid)
        step_index(data, step_id)
        item = deepcopy(payload)
        item.update(id="ev_" + uuid.uuid4().hex[:12], step_id=step_id, created_at=time.time(),
                    outcome_version=item.get("outcome_version", data.get("outcomes", {}).get(step_id, {}).get("version", 0)), inputs=inputs or [])
        images = []
        for img in item.pop("images", []) or []:
            raw = base64.b64decode(img["data_base64"], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            folder = config.SOLVE_SESSIONS_DIR / sid / "assets"
            folder.mkdir(parents=True, exist_ok=True)
            name = digest + ".png"
            (folder / name).write_bytes(raw)
            images.append({"name": img.get("name", "结果图"), "asset": name})
        item["images"] = images
        data.setdefault("evidence", []).append(item)
        sessions.save(data)
        return item


def partial_status(data: dict) -> dict:
    """已确认 / 未确认步骤的统计，供「随时整理成论文」的提示与写作告警使用。"""
    steps = (data.get("problem") or {}).get("steps", []) or []
    outcomes = data.get("outcomes", {}) or {}
    missing = [s for s in steps if outcomes.get(s.get("id"), {}).get("status") != "confirmed"]
    return {"partial": bool(missing), "confirmed": len(steps) - len(missing),
            "missing": len(missing), "total": len(steps),
            "missing_steps": [{"id": s.get("id"),
                               "title": framework.step_label(data.get("problem") or {}, s)}
                              for s in missing]}


def material_snapshot(data, allow_partial=False):
    """冻结用于写作的研究材料。

    allow_partial=True 支持「随时整理成论文」的捷径：只把**学生已确认**的成果
    冻进论文，未确认的步骤记为待补充，绝不当作结论。默认仍要求全部确认。
    """
    steps = data.get("problem", {}).get("steps", []) or []
    outcomes = data.get("outcomes", {}) or {}
    if not steps:
        raise HTTPException(409, "还没有可整理的建模步骤。")
    missing = [s for s in steps if outcomes.get(s["id"], {}).get("status") != "confirmed"]
    if missing and not allow_partial:
        raise HTTPException(409, "请先确认所有适用步骤，再整理成论文")
    refs = {eid for outcome in outcomes.values() if outcome.get("status") == "confirmed"
            for eid in outcome.get("evidence_ids", [])}
    for record in data.get("evidence", []):
        if record["id"] not in refs:
            continue
        for asset in record.get("inputs", []) + record.get("images", []):
            if asset.get("unavailable") or not (config.SOLVE_SESSIONS_DIR / data['id'] / 'assets' / asset.get('asset', '')).is_file():
                raise HTTPException(409, f"验证材料缺失：{asset.get('name', '附件')}。请返回建模补充材料并重新确认。")
    return {"id": data["id"], "revision": data.get("revision", 0), "title": data.get("title"),
            "problem": deepcopy(data["problem"]), "initial_thought": data.get("initial_thought", ""),
            "is_example": data.get("is_example", False), "frozen": True,
            "partial": bool(missing),
            "missing_steps": [{"id": s.get("id"),
                               "title": framework.step_label(data.get("problem") or {}, s)}
                              for s in missing],
            "confirmed_count": len(steps) - len(missing), "total_count": len(steps),
            "outcomes": {k: {f: deepcopy(v) for f, v in o.items() if f != "history"}
                         for k, o in outcomes.items() if o.get("status") == "confirmed"},
            "evidence": [deepcopy(e) for e in data.get("evidence", []) if e["id"] in refs]}


def material_text(data):
    parts = ["学生原始想法（可能已修正）：" + data.get("initial_thought", ""),
             "以下已确认成果是写作依据。保留不支持、局限和模拟性质。不得虚构数据、改变模型或结论；缺项明确说明。"]
    problem = data.get("problem") or {}
    rows = framework.coverage_map(problem)
    if rows:
        parts.append("本题题面要求与负责步骤（论文必须逐条覆盖，不能漏）：\n" + "\n".join(
            f"- [{row['id']}] {row['text']} —— "
            + ("、".join(row["step_labels"]) if row["covered"] else "⚠ 没有任何步骤给出结论")
            for row in rows))
    for step in data.get("problem", {}).get("steps", []):
        outcome = data.get("outcomes", {}).get(step["id"], {})
        if outcome.get("status") == "confirmed":
            parts.append(framework.step_label(problem, step) + "\n"
                         + json.dumps({k: v for k, v in outcome.items() if k != "history"},
                                      ensure_ascii=False))
    for ev in data.get("evidence", []):
        parts.append("验证依据：" + json.dumps({k: v for k, v in ev.items() if k not in ("images", "inputs")}, ensure_ascii=False))
    if data.get("is_example"):
        parts.append("本项目为示例项目，所有给定数据是模拟数据，论文必须明确标注。")
    missing = data.get("missing_steps") or []
    if missing:
        parts.append(
            "以下步骤学生尚未确认、没有留下自己的结论。学生选择了直接让 AI 整理，请根据题目与已有材料"
            "**直接补全撰写**这些章节；不要声称它们是学生确认过的结论，缺数据的地方明确标注为待补充，"
            "不要编造具体数值：\n"
            + "\n".join("- " + (item.get("title") or item.get("id") or "未命名步骤") for item in missing))
    return "\n\n".join(parts)


def create_paper(sid, allow_partial=False):
    with sessions.lock:
        data = get_session(sid)
        snapshot = material_snapshot(data, allow_partial=allow_partial)
        canonical = deepcopy(snapshot)
        canonical.pop("revision", None)
        for o in canonical["outcomes"].values():
            o.pop("confirmed_at", None)
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        conn = paper_db.get_conn()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS solve_sources (session_id TEXT PRIMARY KEY REFERENCES paper_sessions(session_id), source_id TEXT NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL, UNIQUE(source_id,digest))")
            row = conn.execute("SELECT session_id,snapshot FROM solve_sources WHERE source_id=? AND digest=?", (sid, digest)).fetchone()
            if row:
                return {"paper_session_id": row[0], "source_revision": json.loads(row[1])["revision"]}
            topic = data.get("title") or "我的建模研究"
            if len(topic) < 5:
                topic += " · 建模研究"
            pid = paper_db.create_paper_session(conn, topic=topic[:100],
                problem_md=data.get("statement") or data["problem"].get("background") or topic,
                source_solve_id=sid, contest="HiMCM")
            conn.execute("INSERT INTO solve_sources VALUES (?,?,?,?)",
                         (pid, sid, digest, json.dumps(snapshot, ensure_ascii=False)))
            conn.commit()
            data.setdefault("papers", []).append({"id": pid, "revision": snapshot["revision"], "created_at": time.time()})
            sessions.save(data)
            return {"paper_session_id": pid, "source_revision": snapshot["revision"]}
        finally:
            conn.close()


def paper_source(conn, pid):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='solve_sources'").fetchone():
        return None
    row = conn.execute("SELECT snapshot FROM solve_sources WHERE session_id=?", (pid,)).fetchone()
    return json.loads(row[0]) if row else None


def evidence_figures(source):
    """Embed archived images, so both Markdown and Word use the frozen evidence."""
    parts = []
    for ev in source.get('evidence', []):
        for img in ev.get('images', []):
            path = config.SOLVE_SESSIONS_DIR / source['id'] / 'assets' / img['asset']
            if not path.is_file():
                raise ValueError('已确认图表缺失，请返回建模补充验证材料。')
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            label = f"Archived evidence {ev['id']} ({ev.get('source', '')})"
            parts.append(f"![{label}](data:image/png;base64,{encoded})")
    return '\n\n'.join(parts)


class OutcomeRequest(BaseModel):
    expected_revision: int
    action: str = "save"
    decision: str = Field(default="", max_length=20000)
    reason: str = Field(default="", max_length=20000)
    basis: str = Field(default="", max_length=30000)
    limits: str = Field(default="", max_length=10000)
    verdict: str = "pending"
    not_applicable: bool = False
    evidence_ids: list[str] = []


@router.post("/api/solve/sessions/{sid}/steps/{step_id}/outcome")
def outcome_endpoint(sid: str, step_id: str, req: OutcomeRequest):
    if req.action not in {"save", "confirm", "reopen"}:
        raise HTTPException(422, "无效操作")
    return update_outcome(sid, step_id, req.expected_revision, req.model_dump(), req.action)


@router.post("/api/solve/sessions/{sid}/steps/{step_id}/summarize")
def summarize_endpoint(sid: str, step_id: str):
    from .paper_gen import _call_writer
    data = get_session(sid)
    step_index(data, step_id)
    conv = storage.load(data.get("step_convs", {}).get(step_id, ""), base=config.SOLVE_CONVERSATIONS_DIR) or {}
    messages = [{"role": m["role"], "content": m.get("content", "")} for m in conv.get("messages", []) if m["role"] in ("user", "assistant")]
    if not messages:
        raise HTTPException(422, "还没有本步讨论，可以先讨论或直接填写成果")
    raw = _call_writer("用简体中文从实际讨论整理草稿，不补造结果，不代学生确认。只返回 JSON：decision（本步决定）、reason（理由）、basis（已有依据）、limits（限制）。没有依据时明确写证据不足。", json.dumps(messages, ensure_ascii=False))
    try:
        result = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        return {"draft": {k: str(result.get(k, "")) for k in ("decision", "reason", "basis", "limits")}, "revision": data.get("revision", 0)}
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(502, "整理未完成，请重试或直接填写；原讨论已保留")


class EvidenceRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=2000)
    source: str
    stdout: str = Field(min_length=1, max_length=100000)


PLAN_SIZE = len(framework.FRAMEWORK)
# 新增步骤的临时标识前缀：客户端新建情景组时用它生成 id，服务端按它识别新步骤。
NEW_STEP_PREFIX = "n_"


class PlanStepRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    stage_key: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=200)
    prompt: str = Field(default="", max_length=4000)
    covers: list[str] = []
    task_id: str = Field(default="", max_length=80)


class PlanTaskRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=120)


class PlanRequest(BaseModel):
    expected_revision: int
    steps: list[PlanStepRequest]
    tasks: list[PlanTaskRequest] = []


@router.get("/api/solve/framework")
def solve_framework():
    """「编辑路线」可选模块清单（9 个固定阶段，不含评分细则与参考答案）。"""
    return {"size": PLAN_SIZE, "stages": framework.public_stages()}


@router.put("/api/solve/sessions/{sid}/plan")
def plan_endpoint(sid: str, req: PlanRequest):
    """学生调整建模路线：排序 / 换模块 / 改内容 / 增删情景组的步骤。

    路线按「情景组」组织：**主线那一组必须保留全部 9 个模块**，其它情景组只用这一组
    真正需要的模块；组内模块不重复，跨组允许重复（情景A 的模型建立与主线的模型建立
    是两步不同的工作）。

    路线变化后系统同步适配：成果顺序、论文取材顺序、每步验收清单、题面要求的归属都按
    新路线走；内容变化（换模块或改标题/引导语/认领的要求）会使该步及其后续已确认成果
    转为「需重新确认」，换模块还会解绑旧讨论（原对话文件保留，不删除）。
    """
    return update_plan(sid, req.expected_revision, req.steps, req.tasks)


def update_plan(sid, expected_revision, requested, task_titles=None):
    with sessions.lock:
        data = get_session(sid)
        problem = data.get("problem") or {}
        steps = problem.get("steps") or []
        if not steps:
            raise HTTPException(409, "当前项目还没有可调整的建模路线。")
        if not requested:
            raise HTTPException(422, "建模路线不能为空。")
        if data.get("revision", 0) != expected_revision:
            raise HTTPException(409, "路线已更新，请重新读取后再保存。当前调整仍保留在页面上。")

        valid = set(framework.stage_keys())
        existing_by_id = {s.get("id"): s for s in steps}
        groups = framework.task_groups(problem)
        main_task_id = groups[0]["id"] if groups else "T1"

        ids, seen_ids = [], set()
        for item in requested:
            if item.id in seen_ids:
                raise HTTPException(422, "步骤标识重复，请刷新后重试。")
            seen_ids.add(item.id)
            if item.stage_key not in valid:
                raise HTTPException(422, "路线里包含未知模块，请刷新后重试。")
            if item.id not in existing_by_id and not item.id.startswith(NEW_STEP_PREFIX):
                raise HTTPException(422, "步骤标识与当前路线不一致，请刷新后重试。")
            ids.append(item.id)

        known_tasks = {task["id"]: dict(task) for task in framework.tasks_of(problem)}
        known_tasks.setdefault(main_task_id, {"id": main_task_id, "title": "", "goal": "",
                                             "requirement_ids": []})
        for entry in (task_titles or []):
            known_tasks.setdefault(entry.id, {"id": entry.id, "title": "", "goal": "",
                                             "requirement_ids": []})
            known_tasks[entry.id]["title"] = (entry.title or "").strip()[:120]

        # 分组 + 组内唯一性校验：主线必须保留全部 9 个模块。
        by_task: dict[str, list] = {}
        requested_task: dict[str, str] = {}
        for item in requested:
            old = existing_by_id.get(item.id)
            tid = (item.task_id or framework.step_task_id(old or {}) or main_task_id).strip()
            if tid not in known_tasks:
                tid = main_task_id
            requested_task[item.id] = tid
            by_task.setdefault(tid, []).append(item)
        for tid, items in by_task.items():
            keys = [entry.stage_key for entry in items]
            if len(set(keys)) != len(keys):
                label = known_tasks.get(tid, {}).get("title") or "主线"
                raise HTTPException(422, f"「{label}」这一组里同一个模块只能出现一次，请先替换重复的模块。")
            if tid == main_task_id and set(keys) != valid:
                missing = [framework.get_stage(key)["name"] for key in framework.stage_keys()
                           if key not in set(keys)]
                raise HTTPException(422, "主线必须保留全部 9 个模块，缺少：" + "、".join(missing))
        known_tasks = {tid: meta for tid, meta in known_tasks.items() if tid in by_task}

        requirements = framework.requirements_of(problem)
        requirement_ids = {row["id"] for row in requirements} if requirements else set()

        outcomes = data.setdefault("outcomes", {})
        convs = data.get("step_convs", {}) or {}
        changed, module_swapped = set(), set()
        new_steps = []
        for index, item in enumerate(requested):
            old = existing_by_id.get(item.id)
            stage = framework.get_stage(item.stage_key) or {}
            module_changed = old is None or item.stage_key != old.get("stage_key")
            if old is None:
                step = {**framework.default_step(item.stage_key, item.id, index),
                        "content_source": "student_plan"}
                for key in ("prompt", "title"):
                    value = (getattr(item, key) or "").strip()
                    if value:
                        step[key] = value
            elif module_changed:
                step = {**framework.default_step(item.stage_key, item.id, index),
                        "content_source": "student_plan"}
                for key in ("prompt", "title"):
                    value = (getattr(item, key) or "").strip()
                    if value:
                        step[key] = value
            else:
                step = dict(old)
                title = (item.title or "").strip()
                prompt = (item.prompt or "").strip()
                if title and title != (step.get("title") or ""):
                    step["title"] = title
                if prompt and prompt != (step.get("prompt") or ""):
                    step["prompt"] = prompt
            step["id"] = item.id
            step["task_id"] = requested_task[item.id]
            step["stage_key"] = item.stage_key
            step["modality"] = framework.modality_of(item.stage_key)
            step["deliverable"] = stage.get("deliverable", step.get("deliverable", ""))
            step["guide_style"] = stage.get("guide_style", step.get("guide_style", ""))
            covers = [rid for rid in dict.fromkeys(item.covers or [])
                      if not requirement_ids or rid in requirement_ids]
            previous_covers = framework.step_covers(old or {})
            covers_changed = covers != previous_covers
            step["covers"] = covers if (covers or covers_changed) else previous_covers
            step["covers_assumed"] = False if covers_changed else bool((old or {}).get("covers_assumed"))
            if (module_changed or step.get("title") != (old or {}).get("title")
                    or step.get("prompt") != (old or {}).get("prompt") or covers_changed):
                changed.add(item.id)
            if module_changed and old is not None:
                module_swapped.add(item.id)
            new_steps.append(step)

        removed = [step_id for step_id in existing_by_id if step_id not in seen_ids]
        order_changed = [s.get("id") for s in steps] != ids
        problem["steps"] = new_steps
        problem["tasks"] = [known_tasks[tid] for tid in
                            dict.fromkeys(step["task_id"] for step in new_steps)]

        for step_id in removed:
            outcomes.pop(step_id, None)
            convs.pop(step_id, None)
            data.get("step_files", {}).pop(step_id, None)
            data["evidence"] = [ev for ev in data.get("evidence", [])
                                if ev.get("step_id") != step_id]

        # 内容变化的步骤：旧结论作废为「需重新确认」，换模块时解绑旧讨论。
        for index, step in enumerate(new_steps):
            step_id = step["id"]
            if step_id in module_swapped:
                convs.pop(step_id, None)
            if step_id not in changed:
                continue
            previous = outcomes.get(step_id)
            if previous and previous.get("status") in {"confirmed", "draft"}:
                if previous.get("status") == "confirmed" or previous.get("history"):
                    previous.setdefault("history", []).append(
                        {k: deepcopy(v) for k, v in previous.items() if k != "history"})
                previous["status"] = "needs_review"
            for later in new_steps[index + 1:]:
                downstream = outcomes.get(later["id"])
                if downstream and downstream.get("status") == "confirmed":
                    downstream.setdefault("history", []).append(
                        {k: deepcopy(v) for k, v in downstream.items() if k != "history"})
                    downstream["status"] = "needs_review"

        # 换模块后重新开始讨论：旧会话文件保留，仅解除引用。
        data["step_convs"] = convs

        # 保持「当前步骤」指向同一步内容，而不是同一个序号。
        progress = data.setdefault("progress", {})
        old_index = progress.get("cur_step", 0)
        if isinstance(old_index, int) and 0 <= old_index < len(steps):
            current_id = steps[old_index].get("id")
            progress["cur_step"] = next((i for i, s in enumerate(new_steps) if s["id"] == current_id), 0)
        if order_changed:
            progress["seen"] = []

        data["revision"] = data.get("revision", 0) + 1
        sessions.save(data)
        return {"session": data, "order": ids, "changed": sorted(changed),
                "module_swapped": sorted(module_swapped), "order_changed": order_changed,
                "removed": sorted(removed),
                "uncovered": [row["id"] for row in framework.uncovered_requirements(problem)]}


@router.post("/api/solve/sessions/{sid}/steps/{step_id}/evidence")
def evidence_endpoint(sid: str, step_id: str, req: EvidenceRequest):
    if req.source not in {"user", "theory", "simulation"}:
        raise HTTPException(422, "请选择材料来源")
    return add_evidence(sid, step_id, {**req.model_dump(), "status": "provided"})


@router.get("/api/solve/sessions/{sid}/assets/{filename}")
def evidence_asset(sid: str, filename: str):
    data = get_session(sid)
    allowed = {a["asset"] for e in data.get("evidence", []) for a in e.get("images", []) + e.get("inputs", []) if "asset" in a}
    if filename not in allowed or Path(filename).name != filename:
        raise HTTPException(404, "材料不存在")
    return FileResponse(config.SOLVE_SESSIONS_DIR / sid / "assets" / filename)

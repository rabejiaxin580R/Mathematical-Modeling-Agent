"""FastAPI 应用：聊天 SSE 流式接口 + 会话管理 + 静态前端 + 设置 API。

启动：python -m backend.main   或   uvicorn backend.main:app
"""
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import config
from .knowledge import knowledge_base
from .agent import agent
from .ide import TerminalSession
from . import storage
from . import solve_sessions
from . import export_docx
from . import fileops
from . import checkpoints
from . import profiles
from . import graph
from . import assessment
from . import problems
from . import grader
from . import framework
from . import executor
from . import dyn_problems

# 前端目录始终在程序根目录下（不能用 DATA_DIR.parent：打包后 DATA_DIR 指向用户可写目录）
FRONTEND_DIR = config.ROOT_DIR / "frontend"


# ── 日志配置 ──
def _setup_logging():
    config.ensure_dirs()
    log_file = config.DATA_DIR / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    # 降低第三方库日志级别
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    """清洗上传文件名，仅保留基本字符，避免路径穿越。"""
    name = (name or "").replace("\\", "/").split("/")[-1]
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip()
    return safe or "file"


def _workspace_files(conv_id: str) -> list[str]:
    """列出某会话工作目录下用户上传的文件（排除运行产物）。"""
    workdir = config.RUNS_DIR / conv_id
    if not workdir.exists():
        return []
    skip = {"script.py"}
    names = []
    for p in sorted(workdir.iterdir()):
        if not p.is_file() or p.name in skip:
            continue
        # 排除运行产物：matplotlib 自动保存的 figure_*.png（见 executor._PREAMBLE）
        if p.name.startswith("figure_") and p.suffix.lower() == ".png":
            continue
        names.append(p.name)
    return names


def _practice_run_id(pid: str, problem_id: str, step_id: str) -> str:
    """真题练习每个步骤的独立工作目录 id（上传文件 + 代码运行共享）。"""
    def clean(s: str) -> str:
        return "".join(c for c in (s or "") if c.isalnum() or c in "-_")
    return f"practice_{clean(pid)[:12]}_{clean(problem_id)}_{clean(step_id)}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    for p in config.validate():
        logger.warning(p)
    knowledge_base.load()
    try:
        graph.build_graph()
    except Exception:
        logger.exception("知识图谱构建失败（不影响其它功能）")
    logger.info("模型：%s @ %s", config.get_llm_model(), config.get_llm_base_url())
    logger.info("访问 http://%s:%s", config.HOST, config.PORT)
    yield


app = FastAPI(title="数学建模助教 Agent", lifespan=lifespan)


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    regenerate: bool = False


class SettingsRequest(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


# ── 用户档案 ──
class ProfileCreateRequest(BaseModel):
    nickname: str = ""
    avatar: str = ""


class ProfileUpdateRequest(BaseModel):
    nickname: str = ""
    avatar: str = ""


class LearnRequest(BaseModel):
    chunk_id: str
    learned: bool = True


class AssessmentSubmitRequest(BaseModel):
    pid: str
    answers: list[dict] = []   # [{id, choice, perm}]


class AssessmentSelfSelectRequest(BaseModel):
    pid: str
    level: str


class AssessmentSkipRequest(BaseModel):
    pid: str


class GradeRequest(BaseModel):
    pid: str
    step_id: str
    submission: str = ""


class StepChatRequest(BaseModel):
    pid: str = ""
    step_id: str
    messages: list[dict] = []   # 本步多轮历史 [{role, content}]，由前端持有
    submission: str = ""
    grade: dict | None = None
    query: str = ""             # 可选：用于实时检索论文全文的关键词
    run_id: str = ""            # 本步工作目录（上传/运行共享）；空则后端按 pid+step 生成
    workspace_files: list[str] = []   # 本步已上传的文件名


class PracticeRunRequest(BaseModel):
    run_id: str
    code: str = ""


class StepAssessRequest(BaseModel):
    pid: str = ""
    step_id: str
    messages: list[dict] = []   # 本步师生对话，由前端持有


# ── 中间件：记录请求耗时 ──
@app.middleware("http")
async def log_requests(request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    if request.url.path.startswith("/api/"):
        logger.info("%s %s → %d (%.0fms)", request.method, request.url.path, response.status_code, elapsed)
    return response


@app.get("/api/health")
def health():
    return {
        "ok": not config.validate(),
        "problems": config.validate(),
        "model": config.get_llm_model(),
        "knowledge_units": len(knowledge_base.units),
        "categories": knowledge_base.categories(),
    }


@app.get("/api/onboarding")
def onboarding():
    """首次启动向导所需信息：是否已配置密钥 + 内置网关入口 + 自助配置预设。

    前端据此决定是否弹出「领额度 / 填自己的 Key」二选一引导。
    """
    api_key = config.get_llm_api_key()
    has_key = bool(api_key) and api_key != "sk-your-api-key-here"
    return {
        "has_api_key": has_key,
        "gateway": {
            # 三者齐备前端才展示「用我们提供的额度」这条路
            "enabled": bool(config.GATEWAY_SIGNUP_URL and config.GATEWAY_BASE_URL),
            "signup_url": config.GATEWAY_SIGNUP_URL,
            "base_url": config.GATEWAY_BASE_URL,
            "model": config.GATEWAY_MODEL,
        },
    }


@app.get("/api/conversations")
def list_conversations():
    return storage.list_all()


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: str):
    conv = storage.load(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    return conv


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    return {"deleted": storage.delete(conv_id)}


class RollbackRequest(BaseModel):
    index: int  # 目标用户消息在 conv["messages"] 中的下标


@app.post("/api/conversations/{conv_id}/rollback")
def rollback_conversation(conv_id: str, req: RollbackRequest):
    """回到某条用户消息之前：截断该处之后的对话，并撤销其后 AI 对文件的写/删改动。"""
    conv = storage.load(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    msgs = conv.get("messages", [])
    idx = req.index
    if idx < 0 or idx >= len(msgs):
        raise HTTPException(400, "下标越界")
    if msgs[idx].get("role") != "user":
        raise HTTPException(400, "只能回溯到用户消息")

    # 收集 idx 及之后所有回合的文件撤销记录（assistant 消息上的 file_undo）
    records = []
    for m in msgs[idx:]:
        for rec in m.get("file_undo", []) or []:
            records.append(rec)
    reverted = checkpoints.restore(conv_id, records)

    rolled_message = msgs[idx].get("content", "")
    conv["messages"] = msgs[:idx]
    storage.save(conv)
    logger.info("回溯会话 %s 到 #%d，还原 %d 个文件", conv_id, idx, reverted)
    return {"ok": True, "reverted_files": reverted, "message": rolled_message, "conversation": conv}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), conversation_id: str | None = Form(None)):
    conv = storage.load(conversation_id) if conversation_id else None
    if conv is None:
        conv = storage.create()

    safe = _safe_name(file.filename)
    ext = ("." + safe.rsplit(".", 1)[-1].lower()) if "." in safe else ""
    if ext not in config.UPLOAD_ALLOWED_EXTS:
        raise HTTPException(400, f"不支持的文件类型：{ext or '无扩展名'}。允许：{', '.join(sorted(config.UPLOAD_ALLOWED_EXTS))}")

    data = await file.read()
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(400, f"文件过大（>{config.UPLOAD_MAX_BYTES // (1024*1024)}MB）。")

    config.ensure_dirs()
    workdir = config.RUNS_DIR / conv["id"]
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / safe).write_bytes(data)

    logger.info("文件上传: %s → %s (%d bytes)", safe, conv["id"], len(data))
    return {"conversation_id": conv["id"], "filename": safe, "size": len(data)}


@app.post("/api/chat")
def chat(req: ChatRequest):
    # 取得或新建会话
    conv = storage.load(req.conversation_id) if req.conversation_id else None
    if conv is None:
        conv = storage.create()
        logger.info("新建会话: %s", conv["id"])

    if req.regenerate:
        # 重新生成：丢弃末尾的 assistant 消息及其后的 tool 消息
        while conv["messages"] and conv["messages"][-1]["role"] in ("assistant", "tool"):
            conv["messages"].pop()
    else:
        if not conv["messages"]:
            conv["title"] = req.message[:20] or "新对话"
        conv["messages"].append({"role": "user", "content": req.message})

    # 通用聊天与做题共用同一套流式 + 落盘逻辑，仅基础提示词不同
    return StreamingResponse(_run_chat_stream(conv), media_type="text/event-stream")


def _run_chat_stream(conv: dict, base_prompt: str | None = None):
    """聊天流式响应的共享实现：跑 agent 工具循环、SSE 推送、落盘（含文件快照/工具配对）。

    /api/chat（通用助教）与 /api/solve/chat（做题共创伙伴）都调用它，
    仅 base_prompt 不同（None=通用 SYSTEM_PROMPT；agent.solve_prompt=做题人格）。
    """
    history = conv["messages"]
    run_id = conv["id"]
    workspace_files = _workspace_files(run_id)
    system_extra = conv.get("system_extra", "")

    def event_stream():
        yield _sse({"type": "meta", "conversation_id": conv["id"], "title": conv["title"]})

        collected_events = []
        tool_calls_for_save = []  # OpenAI 格式 tool_calls
        tool_messages_for_save = []  # 独立的 tool 角色消息
        undo_records = []  # 本回合 AI 对文件的写/删，供回溯还原
        final_text = ""
        try:
            for ev in agent.stream_reply(history, run_id, workspace_files,
                                         system_extra=system_extra, base_prompt=base_prompt):
                if ev["type"] == "done":
                    final_text = ev["content"]
                elif ev["type"] == "token":
                    final_text += ev["text"]
                else:
                    if ev["type"] == "tool_call":
                        collected_events.append(ev)
                        # 时序：此刻文件尚未被改动（agent 先 yield tool_call 再执行工具），
                        # 趁机对写/删目标做快照，供「回到这一步」还原。
                        if ev.get("name") in ("write_file", "delete_file"):
                            rec = checkpoints.snapshot(conv["id"], (ev.get("arguments") or {}).get("path", ""))
                            if rec:
                                undo_records.append(rec)
                        tool_calls_for_save.append({
                            "id": ev.get("call_id", f"call_{len(tool_calls_for_save)}"),
                            "type": "function",
                            "function": {
                                "name": ev["name"],
                                "arguments": json.dumps(ev["arguments"], ensure_ascii=False),
                            },
                        })
                    elif ev["type"] == "tool_result":
                        collected_events.append(ev)
                        cid = ev.get("call_id", f"call_{len(tool_messages_for_save)}")
                        tool_messages_for_save.append({
                            "role": "tool",
                            "tool_call_id": cid,
                            "content": ev.get("content", ""),
                        })
                    elif ev["type"] == "search":
                        collected_events.append(ev)
                yield _sse(ev)
        except Exception:
            logger.exception("SSE 流异常")
            yield _sse({"type": "error", "message": "服务端内部错误"})
        finally:
            if final_text or collected_events:
                # 仅保留「assistant.tool_calls 与 tool 消息一一配对」的部分：
                # 用户中途停止可能导致 tool_call 已产出但 tool_result 未产出，
                # 若直接保存会形成非法历史（tool_calls 无匹配 tool 消息，或反之），
                # 使该会话之后所有请求被大模型 API 拒绝。
                call_ids = {tc["id"] for tc in tool_calls_for_save}
                answered_ids = {m["tool_call_id"] for m in tool_messages_for_save}
                paired = call_ids & answered_ids
                valid_tool_calls = [tc for tc in tool_calls_for_save if tc["id"] in paired]
                valid_tool_msgs = [m for m in tool_messages_for_save if m["tool_call_id"] in paired]
                msg = {
                    "role": "assistant",
                    "content": final_text,
                    "events": collected_events,
                }
                if valid_tool_calls:
                    msg["tool_calls"] = valid_tool_calls
                if undo_records:
                    msg["file_undo"] = undo_records
                conv["messages"].append(msg)
                # 保存配对的 tool 消息（供后续对话恢复工具调用上下文）
                conv["messages"].extend(valid_tool_msgs)
                storage.save(conv)
        yield _sse({"type": "saved", "conversation_id": conv["id"]})

    return event_stream()


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ── 设置 API ──
@app.get("/api/settings")
def get_settings():
    return config.get_settings()


@app.post("/api/settings")
def update_settings(req: SettingsRequest):
    result = config.update_settings(
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
    )
    agent.reconfigure()
    logger.info("设置已更新: model=%s, base_url=%s", result["model"], result["base_url"])
    return result


# ── 用户档案 API ──
@app.post("/api/profiles")
def create_profile(req: ProfileCreateRequest):
    profile = profiles.create(req.nickname, req.avatar)
    logger.info("新建档案: %s (%s)", profile["nickname"], profile["id"])
    return profile


@app.get("/api/profiles/{pid}")
def get_profile(pid: str):
    profile = profiles.load(pid)
    if not profile:
        raise HTTPException(404, "档案不存在")
    profiles._ensure_shape(profile)
    return profile


@app.patch("/api/profiles/{pid}")
def update_profile(pid: str, req: ProfileUpdateRequest):
    profile = profiles.load(pid)
    if not profile:
        raise HTTPException(404, "档案不存在")
    if req.nickname:
        profile["nickname"] = req.nickname.strip()[:20]
    if req.avatar:
        profile["avatar"] = req.avatar.strip()[:32]
    profiles.save(profile)
    return profile


@app.post("/api/profiles/{pid}/learn")
def profile_learn(pid: str, req: LearnRequest):
    profile = profiles.mark_learned(pid, req.chunk_id, req.learned)
    if profile is None:
        raise HTTPException(404, "档案不存在或参数无效")
    return profile["learn"]


# ── 模式1：知识图谱 API ──
@app.get("/api/graph")
def get_graph():
    return graph.build_graph()


@app.get("/api/map")
def get_map():
    return graph.build_map()


@app.get("/api/graph/node/{chunk_id}")
def get_graph_node(chunk_id: str):
    detail = graph.node_detail(chunk_id)
    if detail is None:
        raise HTTPException(404, "知识点不存在")
    return detail


# ── 入门测评 + 分级学习路径 API ──
@app.get("/api/assessment/status")
def assessment_status():
    """题库是否就绪 + 各等级（含一句话定位，供自选）。"""
    return {"available": assessment.available(), "levels": assessment.all_levels()}


@app.get("/api/assessment/quiz")
def assessment_quiz(n: int = 0):
    """抽一份测评卷（public，无正确答案）。n<=0 取全部题。"""
    if not assessment.available():
        raise HTTPException(404, "测评题库尚未生成")
    return assessment.sample_quiz(n or None)


@app.post("/api/assessment/submit")
def assessment_submit(req: AssessmentSubmitRequest):
    """提交测评：本地判分定级，写入档案，返回等级 + 推荐学习路径。"""
    if not req.answers:
        raise HTTPException(400, "没有作答记录")
    result = assessment.grade(req.answers)
    profile = profiles.set_assessment(
        req.pid, level=result["level"], source="test",
        score=result["score"], per_module=result["per_module"],
        wrong_concepts=result["wrong_concepts"],
    )
    if profile is None:
        raise HTTPException(404, "档案不存在")
    result["path"] = assessment.path_for(result["level"])
    return result


@app.post("/api/assessment/self-select")
def assessment_self_select(req: AssessmentSelfSelectRequest):
    """学生跳过测评、自选等级：写入档案，返回该等级学习路径。"""
    level = (req.level or "").upper()
    if level not in assessment.LEVEL_ORDER:
        raise HTTPException(400, "等级不合法")
    profile = profiles.set_assessment(req.pid, level=level, source="self")
    if profile is None:
        raise HTTPException(404, "档案不存在")
    return {"level": level, "level_name": assessment.LEVEL_NAME.get(level, ""),
            "source": "self", "path": assessment.path_for(level)}


@app.post("/api/assessment/skip")
def assessment_skip(req: AssessmentSkipRequest):
    """标记「先逛逛/跳过」，学习模式不再每次弹出引导。"""
    profile = profiles.mark_assessment_skipped(req.pid)
    if profile is None:
        raise HTTPException(404, "档案不存在")
    return {"ok": True}


@app.get("/api/assessment/path/{level}")
def assessment_path(level: str):
    path = assessment.path_for(level)
    if path is None:
        raise HTTPException(404, "该等级路径不存在")
    return path


@app.get("/assessment")
def assessment_page():
    """学习模式·入门测评页（答题 + 定级 + 推荐路径）。"""
    return FileResponse(FRONTEND_DIR / "assessment.html")


# ── 模式3：真题练习 API ──
@app.get("/api/problems")
def list_problems():
    return problems.list_all()


@app.get("/api/problems/framework")
def get_framework():
    """建模框架（阶段 + modality），前端按 modality 渲染所需。"""
    return {"stages": framework.FRAMEWORK}


@app.get("/api/problems/{problem_id}")
def get_problem(problem_id: str):
    """单题 public 版（剔除 criteria 细则 / 参考答案 / 论文要点）。"""
    data = problems.load_public(problem_id)
    if data is None:
        raise HTTPException(404, "题目不存在")
    return data


@app.get("/api/problems/{problem_id}/data/{filename}")
def download_problem_data(problem_id: str, filename: str):
    """下载真题附带的数据文件（路径安全，限定在该题资产目录内）。"""
    path = problems.resolve_data_file(problem_id, filename)
    if path is None:
        raise HTTPException(404, "数据文件不存在")
    return FileResponse(str(path), filename=path.name)


@app.get("/api/problems/{problem_id}/papers/{paper_id}")
def search_problem_paper(problem_id: str, paper_id: str, step: str = "", q: str = ""):
    """实时检索某题优秀论文全文片段（供追问对照）。q 为空则按该步任务检索。"""
    query = q
    if not query and step:
        st = problems.get_step(problem_id, step)
        if st:
            query = (st.get("title", "") + " " + st.get("prompt", ""))[:200]
    excerpts = problems.search_papers(problem_id, query or paper_id)
    # 只保留指定论文（若给了 paper_id）
    if paper_id and paper_id != "_all":
        excerpts = [e for e in excerpts if e.get("paper_id") == paper_id]
    return {"problem_id": problem_id, "paper_id": paper_id, "excerpts": excerpts}


@app.post("/api/problems/{problem_id}/grade")
def grade_problem(problem_id: str, req: GradeRequest):
    full = problems.load_full(problem_id)
    if full is None:
        raise HTTPException(404, "题目或步骤不存在")
    if full.get("dynamic"):
        raise HTTPException(400, "动态题是一步步一起做的模式，不做评分")
    step = problems.get_step(problem_id, req.step_id)
    if step is None:
        raise HTTPException(404, "题目或步骤不存在")

    result = grader.grade(step, req.submission)
    result["step_id"] = req.step_id
    # 批改后揭示参考标尺 + 优秀论文这一步怎么做
    result["reference_outline"] = step.get("reference_outline", "")
    result["paper_points"] = step.get("paper_points", []) or []

    # 记入档案（评分出错则不记分）
    if not result.get("error"):
        profiles.upsert_step_score(
            req.pid, problem_id, req.step_id,
            score=result["total"], max_score=result["max"],
            total_max=problems.total_max(problem_id),
        )
    return result


@app.post("/api/problems/{problem_id}/assess")
def assess_step(problem_id: str, req: StepAssessRequest):
    """对话模式：基于本阶段师生对话评估掌握度（过关/掌握度，替代硬打分）。

    返回 {mastery, passed, evidence, comment, suggestions, reference_outline, paper_points}。
    评估完成后记入档案（按过关推进进度）。
    """
    full = problems.load_full(problem_id)
    if full is None:
        raise HTTPException(404, "题目或步骤不存在")
    if full.get("dynamic"):
        raise HTTPException(400, "动态题是一步步一起做的模式，不做掌握度评估")
    step = problems.get_step(problem_id, req.step_id)
    if step is None:
        raise HTTPException(404, "题目或步骤不存在")

    result = grader.assess_stage(step, req.messages)
    result["step_id"] = req.step_id
    # 评估后揭示参考标尺 + 优秀论文这一步怎么做
    result["reference_outline"] = step.get("reference_outline", "")
    result["paper_points"] = step.get("paper_points", []) or []

    if not result.get("error"):
        total_steps = len(problems.load_public(problem_id).get("steps", []))
        profiles.upsert_step_mastery(
            req.pid, problem_id, req.step_id,
            mastery=result["mastery"], total_steps=total_steps,
        )
    return result


@app.post("/api/problems/{problem_id}/step-chat")
def step_chat(problem_id: str, req: StepChatRequest):
    """「和 AI 讨论这一步」：围绕单步上下文的流式答疑（SSE，不落盘）。"""
    full = problems.load_full(problem_id)
    step = problems.get_step(problem_id, req.step_id)
    if full is None or step is None:
        raise HTTPException(404, "题目或步骤不存在")

    # 按需检索论文全文片段（学生追问偏深时提供原文参照）
    excerpts = []
    if req.query:
        excerpts = problems.search_papers(problem_id, req.query)

    run_id = req.run_id or _practice_run_id(req.pid, problem_id, req.step_id)
    workspace_files = req.workspace_files or _workspace_files(run_id)

    stage = framework.get_stage(step.get("stage_key", "")) or {}
    step_context = {
        "problem_title": full.get("title", ""),
        "background": full.get("background", ""),
        "stage_name": stage.get("name", step.get("title", "")),
        "step_title": step.get("title", ""),
        "modality": step.get("modality", ""),
        "prompt": step.get("prompt", ""),
        "guide_style": step.get("guide_style", ""),
        "submission": req.submission,
        "grade": req.grade,
        "paper_points": step.get("paper_points", []) or [],
        "paper_excerpts": excerpts,
        "workspace_files": workspace_files,
    }

    def event_stream():
        try:
            for ev in agent.stream_step_chat(req.messages, step_context, run_id=run_id):
                yield _sse(ev)
        except Exception:
            logger.exception("本步讨论 SSE 流异常")
            yield _sse({"type": "error", "message": "服务端内部错误"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/practice/upload")
async def practice_upload(
    file: UploadFile = File(...),
    pid: str = Form(""),
    problem_id: str = Form(...),
    step_id: str = Form(...),
):
    """真题练习某一步上传文件：落盘到该步独立工作目录，供学生/AI 在本步使用。

    不走 conversations（与 /api/upload 区分）；返回 {run_id, filename, size}。
    """
    safe = _safe_name(file.filename)
    ext = ("." + safe.rsplit(".", 1)[-1].lower()) if "." in safe else ""
    if ext not in config.UPLOAD_ALLOWED_EXTS:
        raise HTTPException(400, f"不支持的文件类型：{ext or '无扩展名'}。允许：{', '.join(sorted(config.UPLOAD_ALLOWED_EXTS))}")
    data = await file.read()
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(400, f"文件过大（>{config.UPLOAD_MAX_BYTES // (1024*1024)}MB）。")

    config.ensure_dirs()
    run_id = _practice_run_id(pid, problem_id, step_id)
    workdir = config.RUNS_DIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / safe).write_bytes(data)
    logger.info("真题上传: %s → %s (%d bytes)", safe, run_id, len(data))
    return {"run_id": run_id, "filename": safe, "size": len(data)}


@app.post("/api/practice/run")
def practice_run(req: PracticeRunRequest):
    """真题练习内联运行 Python：在该步工作目录执行，返回 stdout/stderr/图片。"""
    if not req.run_id:
        raise HTTPException(400, "缺少 run_id")
    result = executor.run_python(req.code, req.run_id)
    return result.to_dict()


# ── 工作台「做题」模式：上传/粘贴题面 → AI 动态拆步 ──
class AnalyzeProblemRequest(BaseModel):
    statement: str = ""
    run_id: str = ""
    files: list[str] = []


@app.post("/api/build/problems/analyze")
def analyze_problem(req: AnalyzeProblemRequest):
    """把用户题面用 LLM 按建模框架拆成分步题目，存为动态题并返回 public 版（供 stepflow 渲染）。"""
    if not (req.statement.strip() or req.files):
        raise HTTPException(400, "请粘贴题面或上传题目文件")
    try:
        problem = dyn_problems.generate(req.statement, req.run_id or None, req.files or None)
    except Exception as e:
        logger.exception("做题模式生成失败")
        raise HTTPException(500, f"题目拆解失败：{e}")
    public = problems.load_public(problem["id"])
    return public or problem


class SolveEnterRequest(BaseModel):
    problem_id: str
    step_id: str
    conversation_id: str = ""


_MLABEL = {"key-points": "要点拆解", "formula": "公式建模", "code": "编程求解", "prose": "分析论述"}


@app.post("/api/solve/enter")
def solve_enter(req: SolveEnterRequest):
    """做题·进入某一步：为这道题准备一个独立的「做题 agent」会话（隐藏上下文），返回会话 id 与开场白。

    把题目背景 + 本步任务 + 共创要求写进会话的 system_extra（对用户不可见）；
    会话打上 agent="solve" 标记，后续 /api/solve/chat 用做题人格（SOLVE_SYSTEM_PROMPT）续聊。
    用户在做题驾驶舱里只看到自己点开这一步后的简短开场，而 AI 已清楚这道题、知道有哪些文件。
    """
    full = problems.load_full(req.problem_id)
    step = problems.get_step(req.problem_id, req.step_id)
    if full is None or step is None:
        raise HTTPException(404, "题目或步骤不存在")

    conv = storage.load(req.conversation_id, base=config.SOLVE_CONVERSATIONS_DIR) if req.conversation_id else None
    if conv is None:
        conv = storage.create(base=config.SOLVE_CONVERSATIONS_DIR)
    conv["agent"] = "solve"

    steps = full.get("steps", []) or []
    idx = next((i for i, s in enumerate(steps) if (s.get("id") or f"s{i+1}") == req.step_id), 0)
    mlabel = _MLABEL.get(step.get("modality", ""), step.get("modality", ""))

    extra = "\n".join([
        "# 当前任务：陪学生做他自己带来的一道题（做题模式）",
        "全程用简体中文回答。",
        "",
        "## 题目背景",
        (full.get("background") or "").strip(),
        "",
        f"## 现在这一步：第 {idx + 1} 步 · {step.get('title', '')}（{mlabel}）",
        "本步要一起想清楚的重点：",
        (step.get("prompt") or "").strip(),
        (f"本步交付物：{step.get('deliverable')}" if step.get("deliverable") else ""),
        "",
        "## 怎么配合（重要）",
        "- 你已经清楚这道题、也有相关知识，但**这一步由学生来推进**：用提问和思路引导他、帮他纠错、补他缺的知识。",
        "- **不要直接把这一步的完整答案 / 公式 / 代码丢给他**；先帮他把本步要解决的问题理清楚，再抛出第一个问题让他来答。",
        "- 这道题没有现成标准答案，你不确定的地方就如实说，和他一起推。",
        "- 只聚焦当前这一步，不要跳到后面的阶段，也不要替他把整道题做完。",
        "- 若当前工作目录里没有相关文件，就直接和他讨论，**不要反复去找不存在的文件**。",
    ])
    conv["system_extra"] = extra
    if not conv.get("messages"):
        conv["title"] = ((full.get("title") or "做题")[:14]) + f"·第{idx + 1}步"
    storage.save(conv)

    kickoff = f"我们开始【第 {idx + 1} 步 · {step.get('title', '')}】，先帮我把这一步要解决的问题理清楚，然后抛第一个问题让我来答。"
    return {"conversation_id": conv["id"], "kickoff": kickoff}


@app.post("/api/solve/chat")
def solve_chat(req: ChatRequest):
    """做题·共创对话：用「建模共创伙伴」人格（SOLVE_SYSTEM_PROMPT）的流式聊天（SSE）。

    与 /api/chat 共用 _run_chat_stream（同一套工具循环 + 落盘），仅基础提示词不同。
    会话上的 system_extra（题目背景/本步任务/共创要求）由 /api/solve/enter 写入。
    """
    conv = storage.load(req.conversation_id, base=config.SOLVE_CONVERSATIONS_DIR) if req.conversation_id else None
    if conv is None:
        conv = storage.create(base=config.SOLVE_CONVERSATIONS_DIR)
        conv["agent"] = "solve"
        logger.info("新建做题会话: %s", conv["id"])

    if req.regenerate:
        while conv["messages"] and conv["messages"][-1]["role"] in ("assistant", "tool"):
            conv["messages"].pop()
    else:
        if not conv["messages"]:
            conv["title"] = req.message[:20] or "做题"
        conv["messages"].append({"role": "user", "content": req.message})

    return StreamingResponse(_run_chat_stream(conv, base_prompt=agent.solve_prompt),
                             media_type="text/event-stream")


# ── 做题会话存档：题目快照 + 进度 + 每步对话 id，用于刷新/下次进来恢复 ──
class SolveSessionRequest(BaseModel):
    id: str = ""
    title: str = "我的题目"
    problem: dict | None = None
    run_id: str = ""
    files: list[str] = []
    progress: dict | None = None
    step_convs: dict | None = None


@app.post("/api/solve/sessions")
def upsert_solve_session(req: SolveSessionRequest):
    """新建/更新一份做题存档（前端持全量状态，后端只负责落盘）。"""
    data = req.model_dump()
    if not data.get("id"):
        data.pop("id", None)
    saved = solve_sessions.upsert(data)
    return saved


@app.get("/api/solve/sessions")
def list_solve_sessions():
    return solve_sessions.list_all()


@app.get("/api/solve/sessions/{session_id}")
def get_solve_session(session_id: str):
    data = solve_sessions.load(session_id)
    if not data:
        raise HTTPException(404, "做题存档不存在")
    return data


@app.delete("/api/solve/sessions/{session_id}")
def delete_solve_session(session_id: str):
    return {"deleted": solve_sessions.delete(session_id)}


# ── 文档导出：把编辑器里的 Markdown/LaTeX 内容导出为 Word(.docx) ──
class ExportDocxRequest(BaseModel):
    content: str = ""
    filename: str = "document"
    format: str = "markdown"  # markdown | latex


@app.post("/api/export/docx")
def export_docx_endpoint(req: ExportDocxRequest):
    if not req.content.strip():
        raise HTTPException(400, "内容为空，无法导出")
    try:
        data = export_docx.to_docx(req.content, req.format)
    except Exception as e:
        logger.exception("导出 Word 失败")
        raise HTTPException(500, f"导出失败：{e}")
    import urllib.parse
    name = (req.filename or "document").rsplit(".", 1)[0] + ".docx"
    quoted = urllib.parse.quote(name)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


class RemoveUploadRequest(BaseModel):
    conversation_id: str = ""
    filename: str = ""


@app.post("/api/upload/remove")
def remove_upload(req: RemoveUploadRequest):
    """删除某会话工作目录里用户上传的一个文件。"""
    if not req.conversation_id or not req.filename:
        raise HTTPException(400, "缺少会话或文件名")
    safe = _safe_name(req.filename)
    target = config.RUNS_DIR / req.conversation_id / safe
    if target.exists() and target.is_file():
        try:
            target.unlink()
        except OSError as e:
            raise HTTPException(500, f"删除失败：{e}")
        return {"deleted": True, "filename": safe}
    return {"deleted": False, "filename": safe}


# ── 极简 IDE ──
class SaveRequest(BaseModel):
    path: str
    content: str = ""
@app.get("/ide")
def ide_page():
    return FileResponse(FRONTEND_DIR / "ide.html")


@app.post("/api/ide/save")
def ide_save(req: SaveRequest):
    """保存编辑器内容到磁盘（复用 fileops.write_file）。"""
    result = fileops.write_file(req.path, req.content, overwrite=True)
    return result["display"]


@app.get("/api/ide/tree")
def ide_tree(path: str = "."):
    """列出某目录下的文件与子目录（复用 fileops.list_dir，供文件浏览器懒加载）。

    返回 {type:"file_op", action:"list", ok, path, items:[{name,is_dir,size}], count, truncated}。
    path 留空或为相对路径时按服务进程 cwd 解析。
    """
    return fileops.list_dir(path)["display"]


@app.post("/api/ide/mkdir")
def ide_mkdir(req: SaveRequest):
    """创建目录（含缺失的父目录），供前端首次使用弹窗调用。"""
    result = fileops.make_dir(req.path)
    return result["display"]


@app.get("/api/ide/read")
def ide_read(path: str):
    """读取任意路径文件内容（复用 fileops.read_file），供「打开到编辑器」「插入内容到对话」。

    成功返回 {ok:true, path, content, chars}；失败返回 {ok:false, path, error}。
    富格式（PDF/Word/PPT/Excel/HTML）经 MarkItDown 提取为文本。
    content 已剥离 read_file 面向模型的中文前缀，返回纯净正文。
    """
    result = fileops.read_file(path)
    d = result["display"]
    if not d.get("ok"):
        return {"ok": False, "path": d.get("path", path), "error": d.get("error", "读取失败")}
    rp = d.get("path", path)
    content = result["content"]
    prefix = f"文件「{rp}」的内容如下：\n\n"
    if content.startswith(prefix):
        content = content[len(prefix):]
    elif content == f"「{rp}」内容为空。":
        content = ""
    return {"ok": True, "path": rp, "content": content, "chars": d.get("chars", 0)}


@app.websocket("/api/ide/terminal")
async def ide_terminal(ws: WebSocket):
    """交互式终端：持久子进程 + stdin。

    收：{type:"run_code",code,cwd} / {type:"run_cmd",command,cwd}
        / {type:"stdin",data} / {type:"interrupt"}
    发：{type:"stdout",data} / {type:"image",name,data_base64}
        / {type:"exit",code,cwd} / {type:"sys",data}
    """
    await ws.accept()
    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue = asyncio.Queue()
    sid = uuid.uuid4().hex[:12]

    # 读取线程在子线程里回调 emit；用 call_soon_threadsafe 把事件投递到事件循环
    def emit(event: dict):
        loop.call_soon_threadsafe(out_queue.put_nowait, event)

    session = TerminalSession(emit, sid)

    async def pump():
        try:
            while True:
                event = await out_queue.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass

    pump_task = asyncio.create_task(pump())
    # 告知前端初始工作目录
    emit({"type": "exit", "code": 0, "cwd": str(session.cwd)})
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "run_code":
                session.run_code(msg.get("code", ""), msg.get("cwd"))
            elif t == "run_cmd":
                session.run_command(msg.get("command", ""), msg.get("cwd"))
            elif t == "stdin":
                session.send_stdin(msg.get("data", ""))
            elif t == "interrupt":
                session.interrupt()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("IDE WebSocket 异常")
    finally:
        pump_task.cancel()
        session.cleanup()


# ── 静态前端 ──
@app.get("/")
def index():
    """登录落地页（输入昵称/选头像创建本地档案）。"""
    return FileResponse(FRONTEND_DIR / "landing.html")


@app.get("/home")
def home_page():
    """模式选择主页：学习模式 / 做建模 / 真题练习。"""
    return FileResponse(FRONTEND_DIR / "home.html")


@app.get("/learn")
def learn_page():
    """模式1：知识图谱学习模式。"""
    return FileResponse(FRONTEND_DIR / "learn.html")


@app.get("/build")
def build_page():
    """模式2：做建模中转页（hub）——独立练习 / 做题 两张卡片。"""
    return FileResponse(FRONTEND_DIR / "build.html")


@app.get("/build/free")
def build_free_page():
    """独立练习：三栏自由练习工作台（编辑器+终端+自由对话）。"""
    return FileResponse(FRONTEND_DIR / "workspace.html")


@app.get("/solve")
def solve_page():
    """做题：独立的做题 agent（上传题目→AI 拆步→一步步共创）。"""
    return FileResponse(FRONTEND_DIR / "solve.html")


@app.get("/practice")
def practice_page():
    """模式3：真题练习。"""
    return FileResponse(FRONTEND_DIR / "practice.html")


@app.get("/chat")
def chat_page():
    """旧版纯聊天页（退路，保留）。"""
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def main():
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
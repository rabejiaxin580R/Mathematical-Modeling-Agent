"""网关 FastAPI 应用：OpenAI 兼容代理 + 网站后端 + 页面路由。

启动：python -m gateway.app   或   uvicorn gateway.app:app --host 0.0.0.0 --port 9000

两类端点：
  1. /v1/chat/completions、/v1/models —— OpenAI 兼容，用 API key（sk-xxx）鉴权 + 计费。
     本地 app 把网关地址当作 LLM_BASE_URL（填到 .../v1），把 API key 当作 LLM_API_KEY。
  2. /api/*、页面 —— 网站：注册/登录/余额/用量/API key 管理/卡密兑换/管理员。
"""
import logging

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, billing, db, proxy, mail, graph, user_settings
from . import literature
from .config import config, Config, save_runtime_settings
from .knowledge import knowledge_base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


app = FastAPI(title="LLM API 网关")


@app.on_event("startup")
def _startup():
    config.ensure_dirs()
    db.init_db()
    # 预加载知识库
    try:
        knowledge_base.load()
    except Exception:
        logger.exception("知识库加载失败")
    for p in config.validate():
        logger.warning(p)
    logger.info("上游：%s（白名单模型：%s）", config.UPSTREAM_BASE_URL,
                "、".join(sorted(config.ALLOWED_MODELS)) or "不限")
    logger.info("网关监听 http://%s:%s", config.HOST, config.PORT)


# ════════════════════════ OpenAI 兼容代理 ════════════════════════
def _api_key_user(authorization: str = Header(default="")) -> dict:
    """从 Authorization: Bearer sk-xxx 解析 API key，返回 {user_id, key_id}；失败 401。"""
    raw = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    ctx = auth.resolve_api_key(raw)
    if ctx is None:
        raise HTTPException(401, "无效的 API key")
    return ctx


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict, ctx: dict = Depends(_api_key_user)):
    """OpenAI 兼容聊天补全。校验余额 + 模型白名单 → 转发上游 → 数 token 扣费。"""
    user_id = ctx["user_id"]
    key_id = ctx["key_id"]

    if not billing.has_credit(user_id):
        raise HTTPException(402, "余额不足，请充值后再用")

    model = payload.get("model", "")
    if config.ALLOWED_MODELS and model not in config.ALLOWED_MODELS:
        raise HTTPException(400, f"模型 {model} 不在允许列表内")

    def on_usage(m: str, p: int, c: int):
        try:
            billing.charge(user_id, m, p, c, api_key_id=key_id)
        except Exception:
            logger.exception("扣费失败 user=%s", user_id)

    if payload.get("stream"):
        return StreamingResponse(proxy.stream_chat(payload, on_usage),
                                 media_type="text/event-stream")
    status, data = await proxy.complete_chat(payload, on_usage)
    return JSONResponse(data, status_code=status)


@app.get("/v1/models")
def list_models(ctx: dict = Depends(_api_key_user)):
    """OpenAI 兼容模型列表（仅返回白名单内的模型）。"""
    models = sorted(config.ALLOWED_MODELS) or list(config.MODEL_PRICES.keys())
    return {"object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "gateway"} for m in models]}


class LiteratureSearchRequest(BaseModel):
    query: str
    sources: list[str] = ["arxiv", "semantic_scholar"]
    max_per_source: int = 5


@app.post("/v1/literature/search")
def literature_search(req: LiteratureSearchRequest, ctx: dict = Depends(_api_key_user)):
    """文献检索（鉴权但不计费）：服务端搜 arXiv / Semantic Scholar，结果缓存 7 天。

    本地 app 用它替代直连，从而无需自己配代理或 API key。
    """
    papers = literature.search_literature(
        req.query, sources=req.sources, max_per_source=req.max_per_source
    )
    return {"papers": papers}


# ════════════════════════ 网站 API ════════════════════════
class RegisterRequest(BaseModel):
    phone: str
    password: str
    nickname: str = ""


class LoginRequest(BaseModel):
    phone: str
    password: str


class KeyCreateRequest(BaseModel):
    name: str = ""


class VerifyCodeRequest(BaseModel):
    email: str
    code: str


class RedeemRequest(BaseModel):
    code: str


@app.post("/api/register/request-code")
def api_register_request_code(req: RegisterRequest, request: Request):
    """注册第 1 步：请求邮箱验证码。"""
    client_ip = auth.get_client_ip(request)
    return auth.register_step1_request_code(req.phone, req.password, req.nickname,
                                             client_ip=client_ip)


@app.post("/api/register/verify")
def api_register_verify(req: VerifyCodeRequest):
    """注册第 2 步：校验验证码，完成注册。"""
    return auth.register_step2_verify(req.email, req.code)


@app.post("/api/login")
def api_login(req: LoginRequest):
    return auth.login(req.phone, req.password)


@app.get("/api/me")
def api_me(user: dict = Depends(auth.get_current_user)):
    return user


@app.get("/api/balance")
def api_balance(user: dict = Depends(auth.get_current_user)):
    bal = billing.get_balance(user["id"])
    return {"user_id": user["id"], **(bal or {"balance_cents": 0, "free_tokens_left": 0})}


@app.get("/api/usage")
def api_usage(user: dict = Depends(auth.get_current_user), limit: int = 50):
    return {"items": billing.usage_history(user["id"], limit)}


@app.get("/api/models")
def api_models(user: dict = Depends(auth.get_current_user)):
    """网站用：列出可调用模型。用登录 token 鉴权（区别于 /v1/models 用 API key）。"""
    models = sorted(config.ALLOWED_MODELS) or list(config.MODEL_PRICES.keys())
    return {"data": [{"id": m} for m in models]}


# ── API key 管理 ──
@app.get("/api/keys")
def api_list_keys(user: dict = Depends(auth.get_current_user)):
    return {"items": auth.list_api_keys(user["id"])}


@app.post("/api/keys")
def api_create_key(req: KeyCreateRequest, user: dict = Depends(auth.get_current_user)):
    """生成新 API key。返回的 key 为完整明文，仅此一次。"""
    return auth.create_api_key(user["id"], req.name)


@app.delete("/api/keys/{key_id}")
def api_revoke_key(key_id: str, user: dict = Depends(auth.get_current_user)):
    if not auth.revoke_api_key(user["id"], key_id):
        raise HTTPException(404, "key 不存在")
    return {"ok": True}


# ── 卡密兑换 ──
@app.post("/api/redeem")
def api_redeem(req: RedeemRequest, user: dict = Depends(auth.get_current_user)):
    return billing.redeem_card(user["id"], req.code)


# ════════════════════════ 管理员 API ════════════════════════
class AdminRechargeRequest(BaseModel):
    user_id: str = ""
    phone: str = ""
    amount_cents: int


class AdminCardsRequest(BaseModel):
    amount_cents: int
    count: int = 1


@app.post("/api/admin/recharge")
def admin_recharge(req: AdminRechargeRequest, _: bool = Depends(auth.require_admin)):
    """管理员手动充值。请求头需带 X-Admin-Token。"""
    uid = req.user_id
    if not uid and req.phone:
        row = db.get_conn().execute("SELECT id FROM users WHERE phone=?",
                                    (req.phone.strip(),)).fetchone()
        uid = row["id"] if row else ""
    if not uid:
        raise HTTPException(404, "未找到用户")
    try:
        result = billing.recharge(uid, req.amount_cents, method="admin")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"user_id": uid, **result}


@app.post("/api/admin/cards")
def admin_cards(req: AdminCardsRequest, _: bool = Depends(auth.require_admin)):
    """批量生成卡密。请求头需带 X-Admin-Token。返回明文卡密列表（仅此一次）。"""
    try:
        codes = billing.gen_cards(req.amount_cents, req.count)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"amount_cents": req.amount_cents, "count": len(codes), "codes": codes}


@app.get("/api/admin/test-mail")
def admin_test_mail(to: str = "", _: bool = Depends(auth.require_admin)):
    """检测 SMTP 配置是否正确。请求头需带 X-Admin-Token。
    可选 ?to=you@example.com 发送测试邮件到指定地址。"""
    err = mail.test_config(to.strip() if to else "")
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "message": f"SMTP 配置正常{'，测试邮件已发送' if to else ''}"}


_NO_CACHE = {"Cache-Control": "no-store, must-revalidate"}

# ════════════════════════ 设置 ════════════════════════
class SettingsRequest(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


@app.get("/api/settings")
def api_get_settings():
    """返回当前上游配置（API Key 脱敏）。"""
    key = config.UPSTREAM_API_KEY
    masked = ""
    if len(key) >= 12:
        masked = key[:5] + "****" + key[-4:]
    elif key:
        masked = key[:3] + "****"
    _PLACEHOLDER = ("your-real", "your-key", "your-deepseek", "change-me", "your-api", "example")
    _has = bool(key and len(key) >= 15 and not any(p in key.lower() for p in _PLACEHOLDER))
    return {
        "api_key": masked,
        "base_url": config.UPSTREAM_BASE_URL,
        "model": (sorted(config.ALLOWED_MODELS)[0] if config.ALLOWED_MODELS else ""),
        "has_key": _has,
    }


@app.post("/api/settings")
def api_save_settings(req: SettingsRequest,
                      user: dict = Depends(auth.get_current_user)):
    """保存上游 API 配置（需登录）。"""
    updates = {}
    key = req.api_key.strip()
    # 如果 key 不是脱敏占位（不含 ****），则更新
    if key and "****" not in key and len(key) >= 15:
        updates["api_key"] = key
    if req.base_url.strip():
        updates["base_url"] = req.base_url.strip()
    if req.model.strip():
        updates["model"] = req.model.strip()
    if updates:
        save_runtime_settings(updates)
        Config.reload()
    return {"ok": True, "has_key": bool(config.UPSTREAM_API_KEY)}


@app.post("/api/settings/auto")
def api_settings_auto(user: dict = Depends(auth.get_current_user)):
    """一键配置：自动生成用户 API Key + 检测上游 Key 是否可用（需登录）。"""
    _PLACEHOLDER = ("your-real", "your-key", "your-deepseek", "change-me", "your-api", "example")
    upstream_key = config.UPSTREAM_API_KEY

    # 1. 检查上游 Key 是否已配好（非占位符）
    if (not upstream_key or len(upstream_key) < 15 or
            any(p in upstream_key.lower() for p in _PLACEHOLDER)):
        raise HTTPException(400, "管理员尚未配置上游 API Key。请在 .env 中设置 UPSTREAM_API_KEY 后重试。")

    # 2. 自动生成一个用户 API Key（sk-xxx）
    new_key = auth.create_api_key(user["id"], "一键配置自动生成")

    # 3. 上游已配置 → 返回成功
    return {
        "ok": True,
        "has_key": True,
        "api_key": new_key.get("key", ""),
        "api_key_prefix": new_key.get("key_prefix", ""),
        "base_url": config.UPSTREAM_BASE_URL,
        "model": sorted(config.ALLOWED_MODELS)[0] if config.ALLOWED_MODELS else "",
    }


# ── 用户级上游来源（AI 问答用平台额度 or 自己的 Key）──
class UserUpstreamRequest(BaseModel):
    source: str = ""          # platform | own（仅切换来源时传）
    api_key: str = ""         # own 模式：自己的上游 Key
    base_url: str = ""
    model: str = ""


@app.get("/api/user/upstream")
def api_get_user_upstream(user: dict = Depends(auth.get_current_user)):
    """返回当前用户的 AI 问答 API 来源配置（key 脱敏）。"""
    return user_settings.get_public(user["id"])


@app.post("/api/user/upstream")
def api_set_user_upstream(req: UserUpstreamRequest,
                          user: dict = Depends(auth.get_current_user)):
    """切换来源 / 保存自己的上游 Key。
    - 只传 source：切换来源（切 own 需已有有效 key）；
    - 传了 api_key：保存自配上游，并默认切到 own。
    """
    try:
        if req.api_key.strip():
            return user_settings.save_own(user["id"], req.api_key, req.base_url,
                                          req.model, switch=(req.source != "platform"))
        if req.source:
            return user_settings.set_source(user["id"], req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return user_settings.get_public(user["id"])


# ════════════════════════ 知识库 ════════════════════════
@app.get("/knowledge")
def page_knowledge():
    """知识库浏览：课程地图 + 学习卡片（无需登录）。"""
    return FileResponse(config.STATIC_DIR / "knowledge.html", headers=_NO_CACHE)


@app.get("/knowledge-hall")
def page_knowledge_hall():
    """AI 知识大厅：AI 问答 + 知识地图（无需登录即可浏览，AI 问答需登录）。"""
    return FileResponse(config.STATIC_DIR / "knowledge_hall.html", headers=_NO_CACHE)


@app.get("/api/map")
def api_map():
    """课程地图：模块 → 子类 → 概念的嵌套结构（无需登录）。"""
    return graph.build_map()


@app.get("/api/graph/node/{chunk_id}")
def api_graph_node(chunk_id: str):
    """单个概念完整详情（无需登录）。"""
    detail = graph.node_detail(chunk_id)
    if detail is None:
        raise HTTPException(404, "概念不存在")
    return detail


@app.get("/api/knowledge/search")
def api_knowledge_search(q: str = "", top_k: int = 20):
    """全文搜索概念（无需登录）。"""
    results = knowledge_base.search(q, top_k)
    return {
        "query": q,
        "results": [
            {"concept_id": u.chunk_id, "title": u.title,
             "one_liner": (u.explain or {}).get("one_liner", ""),
             "difficulty": u.difficulty, "score": round(s, 4)}
            for u, s in results
        ]
    }


class KnowledgeChatRequest(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/api/knowledge/chat")
async def api_knowledge_chat(req: KnowledgeChatRequest,
                               user: dict = Depends(auth.get_current_user)):
    """AI 知识问答（需登录，消耗 Token 额度）。
    检索知识库 → 注入上下文 → 调用 DeepSeek → 流式返回 + 引用。
    """
    user_id = user["id"]

    # 决定 API 来源：own（用自己的 Key，不扣 Token）| platform（扣平台额度）
    upstream = user_settings.effective_upstream(user_id)
    if upstream["billable"] and not billing.has_credit(user_id):
        raise HTTPException(402, "免费额度已用完，可切换到「自己的 API Key」或充值后再用")

    from .knowledge import format_chat_context

    # 1. 检索相关概念
    top_concepts = knowledge_base.search(req.message, top_k=config.KNOWLEDGE_TOP_K)
    context_text = format_chat_context([u for u, _ in top_concepts])

    # 2. 构建 system prompt
    system_prompt = (
        "你是数学建模领域的AI助教。请基于下面提供的知识库内容回答用户问题。\n"
        "要求：\n"
        "- 回答要有出处，引用知识点时使用 [citation:concept_id] 格式标注\n"
        "- 如果知识库内容不足以回答，请诚实说明\n"
        "- 使用中文回答，保持专业但通俗易懂的风格\n"
        "- 数学公式使用 LaTeX 格式（行内 $...$，块级 $$...$$）\n\n"
        "=== 知识库参考内容 ===\n" + context_text
    )

    # 3. 构建消息列表
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(req.history)
    messages.append({"role": "user", "content": req.message})

    # 4. 构建请求载荷（own 模式用用户自配模型，platform 用全局模型）
    fallback_model = config.KNOWLEDGE_MODEL or (
        sorted(config.ALLOWED_MODELS)[0] if config.ALLOWED_MODELS else "deepseek-chat")
    model = upstream["model"] or fallback_model
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 2048,
    }

    # 5. 调用代理基础设施（platform 扣费；own 用自己的上游，不扣平台 Token）
    def on_usage(m: str, p: int, c: int):
        if not upstream["billable"]:
            return
        try:
            billing.charge(user_id, m, p, c)
        except Exception:
            logger.exception("知识问答扣费失败 user=%s", user_id)

    return StreamingResponse(
        proxy.stream_chat(payload, on_usage,
                          api_key=upstream["api_key"], base_url=upstream["base_url"]),
        media_type="text/event-stream")


# ════════════════════════ 页面路由 ════════════════════════
@app.get("/")
def page_index():
    """首页 = 产品展示 + 下载页（无需登录）。"""
    return FileResponse(config.STATIC_DIR / "download.html", headers=_NO_CACHE)


@app.get("/login")
def page_login():
    """登录/注册页（领免费 Token 入口）。"""
    return FileResponse(config.STATIC_DIR / "login.html", headers=_NO_CACHE)


@app.get("/dashboard")
def page_dashboard():
    """用户控制台（需登录）。"""
    return FileResponse(config.STATIC_DIR / "dashboard.html", headers=_NO_CACHE)


@app.get("/download")
def page_download():
    """下载页别名（与首页相同）。"""
    return FileResponse(config.STATIC_DIR / "download.html", headers=_NO_CACHE)


if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

# /dl/ —— 安装包下载直链
from .config import ROOT_DIR as _ROOT_DIR
_DL_DIR = _ROOT_DIR.parent / "download"
if _DL_DIR.exists():
    app.mount("/dl", StaticFiles(directory=str(_DL_DIR)), name="download")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)

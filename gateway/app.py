"""网关 FastAPI 应用：OpenAI 兼容代理 + 网站后端 + 页面路由。

启动：python -m gateway.app   或   uvicorn gateway.app:app --host 0.0.0.0 --port 9000

两类端点：
  1. /v1/chat/completions、/v1/models —— OpenAI 兼容，用 API key（sk-xxx）鉴权 + 计费。
     本地 app 把网关地址当作 LLM_BASE_URL（填到 .../v1），把 API key 当作 LLM_API_KEY。
  2. /api/*、页面 —— 网站：注册/登录/余额/用量/API key 管理/卡密兑换/管理员。
"""
import logging

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, billing, db, proxy
from .config import config

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


class RedeemRequest(BaseModel):
    code: str


@app.post("/api/register")
def api_register(req: RegisterRequest):
    return auth.register(req.phone, req.password, req.nickname)


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


_NO_CACHE = {"Cache-Control": "no-store, must-revalidate"}

# ════════════════════════ 页面路由 ════════════════════════
@app.get("/")
def page_index():
    return FileResponse(config.STATIC_DIR / "login.html", headers=_NO_CACHE)


@app.get("/dashboard")
def page_dashboard():
    return FileResponse(config.STATIC_DIR / "dashboard.html", headers=_NO_CACHE)


if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)

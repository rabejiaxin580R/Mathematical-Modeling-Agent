"""鉴权：网站登录（密码 + 自签 token）+ API key 生成/校验。

两套凭证分开，互不混用：
  1. 网站登录：phone + 密码 → HMAC 自签 token（user_id|过期），存前端 localStorage，
     经 Authorization: Bearer <token> 携带，用于网站 API（余额/用量/管理 key）。
  2. API key：用户在网站生成的 sk-xxx，粘进本地 app，用于调 /v1/chat/completions。
     库里只存 sha256(完整 key)，明文只在生成时返回一次。

密码哈希：pbkdf2_hmac(sha256, 120000 轮)。零第三方依赖（标准库 hmac/hashlib）。

安全须知（公网部署）：AUTH_SECRET/ADMIN_TOKEN 改强随机值、走 HTTPS、登录接口加限流。
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid

from fastapi import Header, HTTPException

from . import db
from .config import config

logger = logging.getLogger(__name__)

_PBKDF2_ROUNDS = 120_000


# ── 密码哈希 ──
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── 登录 token 签发 / 校验（HMAC 自签） ──
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    sig = hmac.new(config.AUTH_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(sig.digest())


def issue_token(user_id: str) -> str:
    payload = {"uid": user_id, "exp": time.time() + config.AUTH_TOKEN_TTL}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_token(token: str) -> str | None:
    """校验登录 token，返回 user_id；无效/过期返回 None。"""
    try:
        payload_b64, sig = token.split(".")
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return None
    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload.get("uid")


# ── 注册 / 登录 ──
def _normalize_phone(phone: str) -> str:
    return (phone or "").strip()


def register(phone: str, password: str, nickname: str = "") -> dict:
    """注册新用户：写 users 表，赠送免费额度。返回 {user, token}。"""
    phone = _normalize_phone(phone)
    if not phone or len(password) < 6:
        raise HTTPException(400, "手机号/邮箱不能为空，密码至少 6 位")

    conn = db.get_conn()
    with db.write_lock():
        if conn.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone():
            raise HTTPException(409, "该账号已注册")
        uid = "u_" + uuid.uuid4().hex[:12]
        now = time.time()
        conn.execute(
            "INSERT INTO users (id, phone, password_hash, nickname, balance_cents, "
            "free_tokens_left, is_admin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, phone, hash_password(password), (nickname or "").strip()[:20] or "建模用户",
             0, config.FREE_TOKENS_ON_SIGNUP, 0, now, now),
        )
        conn.commit()
    logger.info("注册新用户 %s (%s)", uid, phone)
    return {"user": public_user(uid), "token": issue_token(uid)}


def login(phone: str, password: str) -> dict:
    phone = _normalize_phone(phone)
    row = db.get_conn().execute(
        "SELECT id, password_hash FROM users WHERE phone=?", (phone,)
    ).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        raise HTTPException(401, "账号或密码错误")
    return {"user": public_user(row["id"]), "token": issue_token(row["id"])}


def public_user(user_id: str) -> dict | None:
    """对外用户信息（不含密码哈希）。"""
    row = db.get_conn().execute(
        "SELECT id, phone, nickname, balance_cents, free_tokens_left, is_admin "
        "FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["is_admin"] = bool(d["is_admin"])
    return d


# ── API key 生成 / 校验 ──
def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_api_key(user_id: str, name: str = "") -> dict:
    """为用户生成一个新的 API key。返回 {id, name, key, key_prefix}；
    key 是完整明文 sk-xxx，仅此一次返回，之后无法再取。"""
    raw = "sk-" + secrets.token_urlsafe(32)
    prefix = raw[:12]
    kid = "k_" + uuid.uuid4().hex[:12]
    now = time.time()
    conn = db.get_conn()
    with db.write_lock():
        conn.execute(
            "INSERT INTO api_keys (id, user_id, name, key_prefix, key_hash, "
            "revoked, created_at, last_used_at) VALUES (?,?,?,?,?,?,?,?)",
            (kid, user_id, (name or "").strip()[:40], prefix, _hash_key(raw), 0, now, 0),
        )
        conn.commit()
    logger.info("生成 API key %s user=%s", kid, user_id)
    return {"id": kid, "name": name, "key": raw, "key_prefix": prefix}


def list_api_keys(user_id: str) -> list[dict]:
    rows = db.get_conn().execute(
        "SELECT id, name, key_prefix, revoked, created_at, last_used_at "
        "FROM api_keys WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["revoked"] = bool(d["revoked"])
        out.append(d)
    return out


def revoke_api_key(user_id: str, key_id: str) -> bool:
    conn = db.get_conn()
    with db.write_lock():
        cur = conn.execute(
            "UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (key_id, user_id)
        )
        conn.commit()
    return cur.rowcount > 0


def resolve_api_key(raw: str) -> dict | None:
    """校验 API key（来自 /v1 请求的 Authorization: Bearer sk-xxx）。
    返回 {user_id, key_id}；无效/吊销返回 None。顺带更新 last_used_at。"""
    if not raw:
        return None
    row = db.get_conn().execute(
        "SELECT id, user_id, revoked FROM api_keys WHERE key_hash=?", (_hash_key(raw),)
    ).fetchone()
    if row is None or row["revoked"]:
        return None
    try:
        with db.write_lock():
            conn = db.get_conn()
            conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), row["id"]))
            conn.commit()
    except Exception:
        pass  # last_used_at 更新失败不影响鉴权
    return {"user_id": row["user_id"], "key_id": row["id"]}


# ── FastAPI 依赖 ──
def get_current_user(authorization: str = Header(default="")) -> dict:
    """网站 API 用：校验 Bearer 登录 token，返回用户 dict；失败抛 401。"""
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    uid = verify_token(token) if token else None
    if not uid:
        raise HTTPException(401, "未登录或登录已过期")
    user = public_user(uid)
    if user is None:
        raise HTTPException(401, "用户不存在")
    return user


def require_admin(x_admin_token: str = Header(default="")) -> bool:
    """管理员接口守卫：请求头 X-Admin-Token 必须等于配置的 ADMIN_TOKEN。"""
    if not config.ADMIN_TOKEN or not hmac.compare_digest(x_admin_token, config.ADMIN_TOKEN):
        raise HTTPException(403, "需要管理员权限")
    return True

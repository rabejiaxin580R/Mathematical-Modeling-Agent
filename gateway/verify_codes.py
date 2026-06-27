"""邮箱验证码管理：生成、发送、校验。

验证码 6 位数字，10 分钟过期。频率限制：
  - 同邮箱每小时最多 3 次
  - 同 IP 每小时最多 5 次
  - 每验证码最多错误 5 次
"""
import logging
import random
import time

from fastapi import HTTPException

from . import db

logger = logging.getLogger(__name__)

_CODE_TTL = 600          # 验证码有效期 10 分钟
_MAX_ATTEMPTS = 5        # 每验证码最多错误次数
_COOLDOWN_EMAIL = 3      # 同邮箱每小时最多请求数
_COOLDOWN_IP = 5         # 同 IP 每小时最多请求数
_COOLDOWN_WINDOW = 3600  # 频率限制窗口 1 小时


def _cleanup_expired():
    """删除所有过期验证码。每次请求新验证码时顺带执行。"""
    conn = db.get_conn()
    with db.write_lock():
        conn.execute("DELETE FROM email_verifications WHERE expires_at < ?", (time.time(),))
        conn.commit()


def request_code(email: str, password_hash: str, nickname: str, ip: str) -> None:
    """为指定邮箱生成验证码并发送邮件。异常直接抛出由调用方 handle。

    在 DB 中暂存验证码 + 密码哈希 + 昵称（验证通过后才写入 users 表）。
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "请输入有效的邮箱地址")

    _cleanup_expired()
    now = time.time()
    conn = db.get_conn()

    # 频率限制：同邮箱
    email_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM email_verifications "
        "WHERE email=? AND created_at > ?", (email, now - _COOLDOWN_WINDOW),
    ).fetchone()["cnt"]
    if email_count >= _COOLDOWN_EMAIL:
        raise HTTPException(429, "该邮箱请求验证码太频繁，请 1 小时后再试")

    # 频率限制：同 IP
    if ip and ip != "unknown":
        ip_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM email_verifications "
            "WHERE ip_address=? AND created_at > ?", (ip, now - _COOLDOWN_WINDOW),
        ).fetchone()["cnt"]
        if ip_count >= _COOLDOWN_IP:
            raise HTTPException(429, "请求验证码太频繁，请 1 小时后再试")

    # 生成 6 位数字验证码
    code = "".join(str(random.randint(0, 9)) for _ in range(6))

    with db.write_lock():
        conn.execute(
            "INSERT INTO email_verifications (email, code, password_hash, nickname, "
            "expires_at, attempts, ip_address, created_at) VALUES (?,?,?,?,?,0,?,?)",
            (email, code, password_hash, ("{}\"".format(nickname)).strip()[:20] or "建模用户",
             now + _CODE_TTL, (ip or "")[:45], now),
        )
        conn.commit()

    # 发邮件（放在最后，避免邮件发送失败时 DB 已有记录）
    from .mail import send_code
    send_code(email, code)
    logger.info("验证码已发送 %s (ip=%s)", email, ip)


def verify_and_register(email: str, code: str) -> dict:
    """校验验证码，通过后创建用户账号。返回 {user, token}。"""
    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not email or len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "请输入正确的邮箱和 6 位验证码")

    now = time.time()
    conn = db.get_conn()

    with db.write_lock():
        row = conn.execute(
            "SELECT email, code, password_hash, nickname, expires_at, attempts "
            "FROM email_verifications WHERE email=? ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()

        if row is None:
            raise HTTPException(404, "未找到验证码，请先请求发送验证码")

        if row["attempts"] >= _MAX_ATTEMPTS:
            conn.execute("DELETE FROM email_verifications WHERE email=?", (email,))
            conn.commit()
            raise HTTPException(429, "验证码尝试次数过多，请重新请求")

        if row["expires_at"] < now:
            conn.execute("DELETE FROM email_verifications WHERE email=?", (email,))
            conn.commit()
            raise HTTPException(410, "验证码已过期，请重新请求")

        if row["code"] != code:
            conn.execute(
                "UPDATE email_verifications SET attempts = attempts + 1 WHERE email=?",
                (email,),
            )
            conn.commit()
            remaining = _MAX_ATTEMPTS - row["attempts"] - 1
            raise HTTPException(400, f"验证码错误，还剩 {remaining} 次尝试")

        # 验证通过 → 删验证码记录 → 创建用户
        conn.execute("DELETE FROM email_verifications WHERE email=?", (email,))
        conn.commit()

    from . import auth
    return auth.create_user(email, row["password_hash"], row["nickname"])

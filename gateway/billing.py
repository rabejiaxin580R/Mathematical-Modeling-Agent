"""计费：token → 金额折算，余额扣费 / 充值 / 卡密兑换（SQLite 事务，原子）。

金额单位：分（整数）。计费口径：
  成本(元) = prompt_tokens/1e6 * 输入价 + completion_tokens/1e6 * 输出价
  售价(分) = ceil(成本 * BILLING_MARKUP * 100)
扣费优先消耗免费 token 额度，不足部分再扣余额（分）。
"""
import logging
import math
import secrets
import time

from fastapi import HTTPException

from . import db
from .config import config

logger = logging.getLogger(__name__)


def cost_cents(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """按模型价格表把 token 折算成「分」（已乘计费倍率，向上取整不小于 0）。"""
    in_price, out_price = config.prices(model)
    yuan = (prompt_tokens / 1e6) * in_price + (completion_tokens / 1e6) * out_price
    cents = yuan * config.BILLING_MARKUP * 100
    return max(0, math.ceil(cents))


def get_balance(user_id: str) -> dict | None:
    row = db.get_conn().execute(
        "SELECT balance_cents, free_tokens_left FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    return {"balance_cents": row["balance_cents"], "free_tokens_left": row["free_tokens_left"]}


def has_credit(user_id: str) -> bool:
    """调用前的准入检查：仍有免费额度或余额 > 0 即放行。"""
    bal = get_balance(user_id)
    if bal is None:
        return False
    return bal["free_tokens_left"] > 0 or bal["balance_cents"] > 0


def charge(user_id: str, model: str, prompt_tokens: int, completion_tokens: int,
           api_key_id: str = "") -> dict:
    """一次调用结束后扣费（事务）：先耗免费 token，剩余按价折算扣余额。

    余额可能被扣成负数（本次调用已发生、token 已消耗，不能拒付）——下次
    has_credit 会因余额<=0 而拦截。「先用后扣」的固有取舍，可接受。
    """
    total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)
    conn = db.get_conn()
    with db.write_lock():
        row = conn.execute(
            "SELECT balance_cents, free_tokens_left FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"用户不存在：{user_id}")

        free_left = row["free_tokens_left"]
        free_used = min(free_left, total_tokens)
        billable_tokens = total_tokens - free_used

        # 免费额度按比例覆盖输入/输出，避免免费部分仍被收费
        if total_tokens > 0 and billable_tokens > 0:
            ratio = billable_tokens / total_tokens
            bill_prompt = round(prompt_tokens * ratio)
            bill_completion = round(completion_tokens * ratio)
        else:
            bill_prompt = bill_completion = 0

        cost = cost_cents(model, bill_prompt, bill_completion)
        new_balance = row["balance_cents"] - cost
        new_free = free_left - free_used
        now = time.time()

        conn.execute(
            "UPDATE users SET balance_cents=?, free_tokens_left=?, updated_at=? WHERE id=?",
            (new_balance, new_free, now, user_id),
        )
        conn.execute(
            "INSERT INTO usage_log (user_id, api_key_id, model, prompt_tokens, "
            "completion_tokens, cost_cents, free_tokens_used, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user_id, api_key_id, model, prompt_tokens, completion_tokens, cost, free_used, now),
        )
        conn.commit()

    logger.info("扣费 user=%s model=%s tok=%d/%d cost=%d分 free_used=%d 余额=%d分",
                user_id, model, prompt_tokens, completion_tokens, cost, free_used, new_balance)
    return {"cost_cents": cost, "free_tokens_used": free_used,
            "balance_cents": new_balance, "free_tokens_left": new_free}


def recharge(user_id: str, amount_cents: int, method: str = "admin",
             out_trade_no: str = "") -> dict:
    """给用户充值（事务）：加余额 + 记一条 paid 的 recharge_log。"""
    if amount_cents <= 0:
        raise ValueError("充值金额必须为正")
    conn = db.get_conn()
    with db.write_lock():
        row = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError(f"用户不存在：{user_id}")
        now = time.time()
        new_balance = row["balance_cents"] + amount_cents
        conn.execute("UPDATE users SET balance_cents=?, updated_at=? WHERE id=?",
                     (new_balance, now, user_id))
        conn.execute(
            "INSERT INTO recharge_log (user_id, amount_cents, method, status, "
            "out_trade_no, created_at, paid_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, amount_cents, method, "paid", out_trade_no, now, now),
        )
        conn.commit()
    logger.info("充值 user=%s +%d分 method=%s 余额=%d分", user_id, amount_cents, method, new_balance)
    return {"balance_cents": new_balance, "amount_cents": amount_cents}


def usage_history(user_id: str, limit: int = 50) -> list[dict]:
    rows = db.get_conn().execute(
        "SELECT model, prompt_tokens, completion_tokens, cost_cents, "
        "free_tokens_used, created_at FROM usage_log WHERE user_id=? "
        "ORDER BY created_at DESC LIMIT ?", (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── 卡密：管理员生成、用户兑换 ──
def gen_cards(amount_cents: int, count: int) -> list[str]:
    """批量生成卡密（管理员）。返回明文卡密列表（仅此一次完整展示）。"""
    if amount_cents <= 0 or count <= 0 or count > 1000:
        raise ValueError("面值必须为正，数量 1~1000")
    codes = []
    now = time.time()
    conn = db.get_conn()
    with db.write_lock():
        for _ in range(count):
            code = secrets.token_urlsafe(12)
            conn.execute(
                "INSERT INTO cards (code, amount_cents, redeemed_by, created_at, redeemed_at) "
                "VALUES (?,?,?,?,?)", (code, amount_cents, "", now, 0),
            )
            codes.append(code)
        conn.commit()
    logger.info("生成卡密 %d 张，面值 %d 分", count, amount_cents)
    return codes


def redeem_card(user_id: str, code: str) -> dict:
    """用户兑换卡密（事务）：校验未用 → 标记已用 → 加余额。返回 {amount_cents, balance_cents}。"""
    code = (code or "").strip()
    if not code:
        raise HTTPException(400, "卡密不能为空")
    conn = db.get_conn()
    with db.write_lock():
        row = conn.execute("SELECT amount_cents, redeemed_by FROM cards WHERE code=?",
                           (code,)).fetchone()
        if row is None:
            raise HTTPException(404, "卡密无效")
        if row["redeemed_by"]:
            raise HTTPException(409, "卡密已被使用")
        now = time.time()
        conn.execute("UPDATE cards SET redeemed_by=?, redeemed_at=? WHERE code=?",
                     (user_id, now, code))
        # 复用 recharge 逻辑加余额（同一事务内手写，避免嵌套锁）
        urow = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user_id,)).fetchone()
        if urow is None:
            raise HTTPException(404, "用户不存在")
        new_balance = urow["balance_cents"] + row["amount_cents"]
        conn.execute("UPDATE users SET balance_cents=?, updated_at=? WHERE id=?",
                     (new_balance, now, user_id))
        conn.execute(
            "INSERT INTO recharge_log (user_id, amount_cents, method, status, "
            "out_trade_no, created_at, paid_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, row["amount_cents"], "card", "paid", code, now, now),
        )
        conn.commit()
    logger.info("兑换卡密 user=%s +%d分 余额=%d分", user_id, row["amount_cents"], new_balance)
    return {"amount_cents": row["amount_cents"], "balance_cents": new_balance}

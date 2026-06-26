"""SQLite 数据层：账号、API key、余额、用量、充值记录、卡密。

为什么用 SQLite：余额扣费/充值/卡密兑换涉及金额，需要事务保证原子性，
并发写 JSON 会丢数据。SQLite 是 Python 自带的单文件数据库，零额外依赖。

库文件 data/gateway.db。金额一律以「分」（整数）存储，杜绝浮点误差；
免费额度以「token 数」存储。
"""
import sqlite3
import threading

from .config import config

# 进程内串行化写操作（SQLite 单写者）。保护「读-改-写」式扣费/充值/兑换。
_write_lock = threading.RLock()
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """每线程一个连接（sqlite3 连接非线程安全）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(str(config.db_path()), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def write_lock() -> threading.RLock:
    """供 billing/auth 在「读-改-写」临界区使用。"""
    return _write_lock


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                TEXT PRIMARY KEY,               -- u_xxx
    phone             TEXT UNIQUE NOT NULL,           -- 登录账号（手机号/邮箱）
    password_hash     TEXT NOT NULL,                  -- pbkdf2：算法$迭代$盐$哈希
    nickname          TEXT DEFAULT '',
    balance_cents     INTEGER NOT NULL DEFAULT 0,     -- 余额（分）
    free_tokens_left  INTEGER NOT NULL DEFAULT 0,     -- 剩余免费 token 额度
    is_admin          INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

-- 用户调 API 用的密钥。明文 sk-xxx 只在生成时返回一次，库里只存哈希。
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,                    -- k_xxx
    user_id      TEXT NOT NULL,
    name         TEXT DEFAULT '',                     -- 用户起的备注名
    key_prefix   TEXT NOT NULL,                       -- 前 12 位明文，供列表展示（sk-xxxx…）
    key_hash     TEXT NOT NULL UNIQUE,                -- sha256(完整 key)
    revoked      INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    last_used_at REAL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
    api_key_id        TEXT DEFAULT '',
    model             TEXT DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents        INTEGER NOT NULL DEFAULT 0,     -- 实扣余额（分）；走免费额度时为 0
    free_tokens_used  INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS recharge_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    method       TEXT DEFAULT '',                     -- admin / card / wechat / alipay
    status       TEXT NOT NULL DEFAULT 'paid',
    out_trade_no TEXT DEFAULT '',                     -- 商户订单号（接在线支付后用）
    created_at   REAL NOT NULL,
    paid_at      REAL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 卡密：管理员批量生成，用户兑换 → 加余额。接在线支付前的主要充值方式。
CREATE TABLE IF NOT EXISTS cards (
    code         TEXT PRIMARY KEY,                    -- 卡密明文（一次性、足够随机）
    amount_cents INTEGER NOT NULL,                    -- 面值（分）
    redeemed_by  TEXT DEFAULT '',                     -- 兑换用户 id；空=未用
    created_at   REAL NOT NULL,
    redeemed_at  REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_recharge_user ON recharge_log(user_id, created_at);
"""


def init_db():
    """建表（幂等）。应用启动时调用。"""
    conn = get_conn()
    with _write_lock:
        conn.executescript(_SCHEMA)
        conn.commit()

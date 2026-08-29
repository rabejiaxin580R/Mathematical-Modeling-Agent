"""论文生成流水线的 SQLite 状态层。

设计原则
--------
- 状态权威在后端：不信任前端传来的全量状态（solve_sessions.py 的旧模式），
  每一步的 content_md / status 都由后端写入、由后端读出后再喂给下一步 LLM。
- LLM 写 DML：后端把 DDL 作为上下文发给 LLM，让它产出 INSERT/UPDATE；
  execute_dml() 在事务里跑，任何 CHECK/UNIQUE 违反都整体回滚，
  错误消息原文返回给 LLM 让它自己改正——不需要手写 JSON 解析校验。
- 一个全局库：DATA_DIR/paper_sessions.db，session_id 作分区键；
  方便跨会话查询（如「未完成的会话列表」），也方便统一备份。

表结构摘要（见 PAPER_DDL 字符串）
-----------------------------------
  paper_sessions      会话锚（一行 = 一篇正在写的论文）
  section_state       每节的生成状态与 content_md（backend 权威）
  symbols             符号表（notation 阶段落盘，后续章节引用）
  model_cards         使用的数学模型（思路矫正也会写这里）
  results_ledger      关键数值结果台账
  fig_table_registry  图/表注册（caption 与 Note. 统一管理）
"""

import sqlite3
import uuid
from pathlib import Path


# ── DDL ─────────────────────────────────────────────────────────────────────

PAPER_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ① 会话锚：一行 = 一篇正在生成的论文
CREATE TABLE IF NOT EXISTS paper_sessions (
    session_id  TEXT PRIMARY KEY
                    CHECK(session_id GLOB 'ps_????????????????????????????????'),
    topic       TEXT NOT NULL CHECK(length(topic) >= 5),
    problem_md  TEXT NOT NULL,
    contest     TEXT NOT NULL DEFAULT 'HiMCM'
                    CHECK(contest IN ('HiMCM', 'MCM', 'ICM')),
    source_solve_id TEXT,             -- 来源做题存档 id
    team_id     TEXT,
    created_at  TEXT NOT NULL CHECK(created_at GLOB '????-??-??T??:??:??*'),
    updated_at  TEXT NOT NULL CHECK(updated_at GLOB '????-??-??T??:??:??*'),
    status      TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'paused', 'complete', 'abandoned'))
);

-- ② 每节的生成状态（backend 权威，替代前端持全量状态的旧模式）
-- section_key 枚举覆盖 framework.py 的九阶段 + abstract + references
CREATE TABLE IF NOT EXISTS section_state (
    session_id  TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    section_key TEXT NOT NULL
                    CHECK(section_key IN (
                        'abstract', 'restate', 'assume', 'notation', 'build',
                        'solve', 'analyze', 'sensitivity', 'evaluate', 'extend',
                        'references'
                    )),
    status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'generating', 'draft', 'approved', 'skipped')),
    content_md  TEXT,                  -- 当前草稿（approved 后固化）
    attempt_no  INTEGER NOT NULL DEFAULT 0 CHECK(attempt_no >= 0),
    updated_at  TEXT NOT NULL CHECK(updated_at GLOB '????-??-??T??:??:??*'),
    PRIMARY KEY (session_id, section_key)
);

-- ③ 符号表：notation 阶段落盘，后续章节按 symbol_tex 引用
CREATE TABLE IF NOT EXISTS symbols (
    session_id  TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    symbol_tex  TEXT NOT NULL,         -- LaTeX 表示，如 \\bar{x}、\\lambda
    meaning     TEXT NOT NULL CHECK(length(meaning) >= 2),
    unit        TEXT,                  -- 量纲；纯数无量纲则 NULL
    introduced  TEXT                   -- 首次出现的 section_key
                    CHECK(introduced IS NULL OR introduced IN (
                        'abstract', 'restate', 'assume', 'notation', 'build',
                        'solve', 'analyze', 'sensitivity', 'evaluate', 'extend',
                        'references'
                    )),
    PRIMARY KEY (session_id, symbol_tex)
);

-- ④ 模型卡片：一模型一行；思路矫正的替换建议也写这里（rationale 注明来源）
-- model_id 格式 M1 M2 M10 …（至少两字符：M + 一位以上数字）
CREATE TABLE IF NOT EXISTS model_cards (
    session_id  TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    model_id    TEXT NOT NULL CHECK(model_id GLOB 'M[0-9]*' AND length(model_id) >= 2),
    name        TEXT NOT NULL,
    concept_id  TEXT,                  -- 知识库 concept_id，如 C.5.3
    rationale   TEXT,                  -- 选用理由（思路矫正会补充「替换自 MX」）
    assumptions TEXT,                  -- 模型专有假设
    limitations TEXT,                  -- 局限性（用于模型评价节）
    citation    TEXT,                  -- APA author-date，如 (Smith, 2020)
    PRIMARY KEY (session_id, model_id)
);

-- ⑤ 结果台账：关键数值结果，供结果分析节和灵敏度节引用
-- result_id 格式 R1 R2 R10 …
CREATE TABLE IF NOT EXISTS results_ledger (
    session_id  TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    result_id   TEXT NOT NULL CHECK(result_id GLOB 'R[0-9]*' AND length(result_id) >= 2),
    label       TEXT NOT NULL,         -- 描述，如 "optimal total cost"
    value_text  TEXT NOT NULL,         -- 存文本，兼容带单位 / 区间的值
    unit        TEXT,
    source_eq   TEXT,                  -- 推导来源，如 "(3)" 或 "Algorithm 1"
    section_key TEXT
                    CHECK(section_key IS NULL OR section_key IN (
                        'abstract', 'restate', 'assume', 'notation', 'build',
                        'solve', 'analyze', 'sensitivity', 'evaluate', 'extend',
                        'references'
                    )),
    PRIMARY KEY (session_id, result_id)
);

-- ⑦ 永久拒绝的矫正建议（用户拒绝后跨会话不再提示）
-- fingerprint = sha256[:16] of "{stage}:{kb_ref}:{issue_type}"
CREATE TABLE IF NOT EXISTS rejected_corrections (
    fingerprint  TEXT PRIMARY KEY CHECK(length(fingerprint) = 16),
    stage        TEXT,
    kb_ref       TEXT,
    issue_type   TEXT,
    rejected_at  TEXT NOT NULL CHECK(rejected_at GLOB '????-??-??T??:??:??*')
);

-- ⑥ 图/表注册：与 export_docx 的 Caption:/Note: 约定对接
-- item_no 在 (session_id, item_type) 内单调递增；UNIQUE 防重复注册
CREATE TABLE IF NOT EXISTS fig_table_registry (
    session_id  TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL CHECK(item_type IN ('figure', 'table')),
    item_no     INTEGER NOT NULL CHECK(item_no >= 1),
    caption     TEXT NOT NULL CHECK(length(caption) >= 3),
    note_text   TEXT,                  -- APA Note. 内容；无则 NULL
    section_key TEXT,
    PRIMARY KEY (session_id, item_type, item_no),
    UNIQUE (session_id, item_type, item_no)
);

-- ⑦ 数据集管理：用户上传或系统生成的测试数据（通用数据管理架构）
-- 存储schema、统计摘要、采样数据，而不是完整数据，避免输入上下文爆炸
CREATE TABLE IF NOT EXISTS session_datasets (
    session_id    TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    dataset_id    TEXT NOT NULL CHECK(dataset_id GLOB 'DS[0-9]*' AND length(dataset_id) >= 3),
    dataset_name  TEXT NOT NULL,
    source_type   TEXT NOT NULL CHECK(source_type IN ('user_upload', 'generated', 'scraped')),
    total_rows    INTEGER NOT NULL CHECK(total_rows > 0),
    created_at    TEXT NOT NULL CHECK(created_at GLOB '????-??-??T??:??:??*'),
    schema_json   TEXT NOT NULL,      -- [{"name":"age","type":"int","unit":"years"},...]
    stats_json    TEXT,                -- {"age":{"min":18,"max":65,"mean":42.3},...}
    sample_rows   TEXT,                -- JSON数组：采样的5-10行
    section_key   TEXT,
    PRIMARY KEY (session_id, dataset_id)
);

-- ⑧ 数据集完整行存储（可选，用于大数据集 >100 行）
CREATE TABLE IF NOT EXISTS dataset_rows (
    session_id    TEXT NOT NULL,
    dataset_id    TEXT NOT NULL,
    row_id        INTEGER NOT NULL CHECK(row_id >= 1),
    row_data      TEXT NOT NULL,      -- JSON字符串：一行数据
    PRIMARY KEY (session_id, dataset_id, row_id),
    FOREIGN KEY (session_id, dataset_id)
        REFERENCES session_datasets(session_id, dataset_id) ON DELETE CASCADE
);
"""

# 给 LLM 看的紧凑摘要（不含注释，只留约束关键词）—— 用于两阶段检索的 index 层
PAPER_DDL_INDEX = (
    "paper_sessions(session_id PK GLOB 'ps_*', topic, problem_md, contest IN HiMCM/MCM/ICM, "
    "source_solve_id, "
    "team_id, created_at ISO8601, updated_at ISO8601, status IN active/paused/complete/abandoned) | "
    "section_state(session_id FK, section_key IN abstract/restate/assume/notation/build/"
    "solve/analyze/sensitivity/evaluate/extend/references, status IN pending/generating/draft/"
    "approved/skipped, content_md, attempt_no>=0, updated_at ISO8601) PK(session_id,section_key) | "
    "symbols(session_id FK, symbol_tex PK, meaning>=2chars, unit nullable, introduced nullable section_key) | "
    "model_cards(session_id FK, model_id GLOB 'M[0-9]*'>=2chars PK, name, concept_id nullable, "
    "rationale, assumptions, limitations, citation APA) | "
    "results_ledger(session_id FK, result_id GLOB 'R[0-9]*'>=2chars PK, label, value_text, unit, "
    "source_eq, section_key nullable) | "
    "fig_table_registry(session_id FK, item_type IN figure/table, item_no>=1, caption>=3chars, "
    "note_text nullable, section_key nullable) PK(session_id,item_type,item_no) | "
    "session_datasets(session_id FK, dataset_id GLOB 'DS[0-9]*'>=3chars PK, dataset_name, "
    "source_type IN user_upload/generated/scraped, total_rows>0, schema_json, stats_json nullable, "
    "sample_rows nullable, created_at ISO8601, section_key nullable) | "
    "dataset_rows(session_id+dataset_id FK, row_id>=1, row_data JSON) PK(session_id,dataset_id,row_id) | "
    "rejected_corrections(fingerprint PK sha256[:16], stage, kb_ref, issue_type, rejected_at ISO8601)"
)


# ── 连接与初始化 ─────────────────────────────────────────────────────────────

def get_db_path() -> Path:
    """返回全局论文数据库的路径（惰性导入 config，避免循环）。"""
    from .config import config
    config.ensure_dirs()
    return config.DATA_DIR / "paper_sessions.db"


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """打开（或创建）数据库，应用 DDL，返回连接。

    调用方负责关闭：建议用 `with get_conn() as conn:` 或 try/finally。
    row_factory 设为 sqlite3.Row，结果可按列名访问。
    """
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(PAPER_DDL)
    return conn


# ── ID 生成 ──────────────────────────────────────────────────────────────────

def new_session_id() -> str:
    """生成符合 GLOB 'ps_????????????????????????????????' 的唯一会话 ID。"""
    return "ps_" + uuid.uuid4().hex   # uuid4().hex 恰好 32 个十六进制字符


# ── LLM DML 执行 ─────────────────────────────────────────────────────────────

def execute_dml(conn: sqlite3.Connection, sql: str) -> dict:
    """在事务里执行 LLM 产出的 DML（INSERT / UPDATE / DELETE）。

    成功：{"ok": True, "rows_affected": N}
    失败：{"ok": False, "error": "sqlite3.IntegrityError: ..."}  ← 原文返回给 LLM 自修

    约束：
    - 只允许 INSERT / UPDATE / DELETE（不允许 DDL / ATTACH / PRAGMA 修改）
    - 失败时整体回滚，不留脏数据
    """
    # 简单安全过滤：剔除 DDL 和危险语句
    normalized = sql.strip().upper()
    forbidden = ("CREATE", "DROP", "ALTER", "ATTACH", "DETACH",
                 "PRAGMA", "VACUUM", "REINDEX")
    for kw in forbidden:
        if normalized.startswith(kw) or f"\n{kw}" in normalized or f";{kw}" in normalized:
            return {"ok": False, "error": f"DML only: forbidden keyword '{kw}' detected."}

    try:
        with conn:   # sqlite3 连接的 context manager：成功 commit，异常 rollback
            cursor = conn.executescript(sql) if ";" in sql.rstrip(";") else conn.execute(sql)
            rows = conn.total_changes
        return {"ok": True, "rows_affected": rows}
    except sqlite3.Error as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── 常用读取辅助 ─────────────────────────────────────────────────────────────

def load_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM paper_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def list_sessions(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM paper_sessions WHERE status = ? ORDER BY updated_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM paper_sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def load_section(conn: sqlite3.Connection,
                 session_id: str, section_key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM section_state WHERE session_id=? AND section_key=?",
        (session_id, section_key),
    ).fetchone()
    return dict(row) if row else None


def list_sections(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """按 HiMCM 章节顺序返回所有 section_state 行。"""
    _ORDER = [
        "abstract", "restate", "assume", "notation", "build",
        "solve", "analyze", "sensitivity", "evaluate", "extend", "references",
    ]
    rows = conn.execute(
        "SELECT * FROM section_state WHERE session_id=?", (session_id,)
    ).fetchall()
    by_key = {dict(r)["section_key"]: dict(r) for r in rows}
    return [by_key[k] for k in _ORDER if k in by_key]


def load_symbols(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM symbols WHERE session_id=? ORDER BY rowid", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def load_model_cards(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM model_cards WHERE session_id=? ORDER BY model_id", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def load_results(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM results_ledger WHERE session_id=? ORDER BY result_id", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def record_rejection(conn: sqlite3.Connection,
                     stage: str, kb_ref: str, issue_type: str) -> str:
    """记录一条永久拒绝，返回 fingerprint（幂等：已存在则忽略）。"""
    import hashlib, datetime
    fp = hashlib.sha256(f"{stage}:{kb_ref}:{issue_type}".encode()).hexdigest()[:16]
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT OR IGNORE INTO rejected_corrections"
        " (fingerprint, stage, kb_ref, issue_type, rejected_at)"
        " VALUES (?,?,?,?,?)",
        (fp, stage, kb_ref, issue_type, now),
    )
    conn.commit()
    return fp


def load_rejections(conn: sqlite3.Connection) -> set[str]:
    """返回所有已拒绝的 fingerprint 集合，用于过滤诊断结果。"""
    rows = conn.execute("SELECT fingerprint FROM rejected_corrections").fetchall()
    return {r["fingerprint"] for r in rows}


def load_fig_table_registry(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM fig_table_registry WHERE session_id=? "
        "ORDER BY item_type, item_no", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── 论文生成辅助 ─────────────────────────────────────────────────────────────

SECTION_ORDER = [
    "restate", "assume", "notation", "build", "solve",
    "analyze", "sensitivity", "evaluate", "extend", "abstract",
]


def create_paper_session(
    conn: sqlite3.Connection,
    *,
    topic: str,
    problem_md: str,
    source_solve_id: str = "",
    contest: str = "HiMCM",
) -> str:
    """创建论文会话并预置所有阶段的 section_state 行（status=pending）。

    返回新的 session_id。事务由调用方管理。
    """
    import datetime
    sid = new_session_id()
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO paper_sessions"
        " (session_id, topic, problem_md, contest, source_solve_id, created_at, updated_at, status)"
        " VALUES (?,?,?,?,?,?,?,'active')",
        (sid, topic, problem_md, contest, source_solve_id, now, now),
    )
    for sk in SECTION_ORDER:
        conn.execute(
            "INSERT INTO section_state (session_id, section_key, status, attempt_no, updated_at)"
            " VALUES (?,?,'pending',0,?)",
            (sid, sk, now),
        )
    return sid


def upsert_section(
    conn: sqlite3.Connection,
    session_id: str,
    section_key: str,
    content_md: str,
    status: str = "draft",
) -> dict:
    """写入/更新一节内容。返回 {ok, section_key, status, chars}。"""
    import datetime
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    # 先查是否存在
    row = conn.execute(
        "SELECT attempt_no FROM section_state WHERE session_id=? AND section_key=?",
        (session_id, section_key),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO section_state"
            " (session_id, section_key, status, content_md, attempt_no, updated_at)"
            " VALUES (?,?,?,?,0,?)",
            (session_id, section_key, status, content_md, now),
        )
    else:
        conn.execute(
            "UPDATE section_state SET content_md=?, status=?, attempt_no=attempt_no+1, updated_at=?"
            " WHERE session_id=? AND section_key=?",
            (content_md, status, now, session_id, section_key),
        )
    conn.execute(
        "UPDATE paper_sessions SET updated_at=? WHERE session_id=?",
        (now, session_id),
    )
    conn.commit()
    return {"ok": True, "section_key": section_key, "status": status, "chars": len(content_md)}


def load_approved_sections(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """加载所有已批准的节（按 SECTION_ORDER 排序），供上下文引用。"""
    rows = conn.execute(
        "SELECT * FROM section_state WHERE session_id=? AND status IN ('draft','approved')"
        " ORDER BY CASE section_key "
        + " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(SECTION_ORDER))
        + " END",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]



# ── 数据集管理 ────────────────────────────────────────────────────────────────

def store_dataset(
    conn: sqlite3.Connection,
    session_id: str,
    dataset_id: str,
    dataset_name: str,
    source_type: str,
    data_rows: list[dict],
    section_key: str | None = None,
) -> dict:
    """存储数据集到session_datasets表。

    Args:
        conn: 数据库连接
        session_id: 论文会话ID
        dataset_id: 数据集ID（格式：DS1, DS2, ...）
        dataset_name: 可读名称
        source_type: 'user_upload' | 'generated' | 'scraped'
        data_rows: 数据行列表，每行是dict
        section_key: 关联章节（通常是'analyze'）

    Returns:
        {"ok": True, "dataset_id": "DS1", "rows": 10}
    """
    import datetime
    import json

    if not data_rows:
        return {"ok": False, "error": "data_rows is empty"}

    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    total_rows = len(data_rows)

    # 推断schema
    schema = _infer_schema(data_rows)

    # 计算统计摘要
    stats = _compute_stats(data_rows, schema)

    # 采样（小数据集全部，大数据集采样）
    sample_size = min(10, total_rows)
    import random
    sample = random.sample(data_rows, sample_size) if total_rows > sample_size else data_rows

    # 插入主表
    conn.execute("""
        INSERT OR REPLACE INTO session_datasets
        (session_id, dataset_id, dataset_name, source_type, total_rows,
         created_at, schema_json, stats_json, sample_rows, section_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id, dataset_id, dataset_name, source_type, total_rows,
        now, json.dumps(schema), json.dumps(stats), json.dumps(sample), section_key
    ))

    # 如果数据量大，存入dataset_rows
    if total_rows > 100:
        # 先清空旧数据
        conn.execute("""
            DELETE FROM dataset_rows
            WHERE session_id = ? AND dataset_id = ?
        """, (session_id, dataset_id))

        # 插入完整数据
        for idx, row in enumerate(data_rows, 1):
            conn.execute("""
                INSERT INTO dataset_rows (session_id, dataset_id, row_id, row_data)
                VALUES (?, ?, ?, ?)
            """, (session_id, dataset_id, idx, json.dumps(row)))

    conn.commit()
    return {"ok": True, "dataset_id": dataset_id, "rows": total_rows}


def _infer_schema(data_rows: list[dict]) -> list[dict]:
    """从数据行推断schema"""
    if not data_rows:
        return []

    first_row = data_rows[0]
    schema = []

    for key, value in first_row.items():
        col_type = "string"
        if isinstance(value, (int, float)):
            col_type = "number"
        elif isinstance(value, bool):
            col_type = "boolean"
        elif isinstance(value, dict):
            col_type = "object"
        elif isinstance(value, list):
            col_type = "array"

        schema.append({
            "name": key,
            "type": col_type
        })

    return schema


def _compute_stats(data_rows: list[dict], schema: list[dict]) -> dict:
    """计算数值列的统计摘要"""
    stats = {}

    for col in schema:
        if col["type"] == "number":
            col_name = col["name"]
            values = []

            for row in data_rows:
                val = row.get(col_name)
                if val is not None and isinstance(val, (int, float)):
                    values.append(float(val))

            if values:
                stats[col_name] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": sum(values) / len(values),
                    "count": len(values)
                }

    return stats


def load_datasets(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """加载会话的所有数据集"""
    rows = conn.execute("""
        SELECT * FROM session_datasets WHERE session_id = ?
    """, (session_id,)).fetchall()

    return [dict(r) for r in rows]


def build_dataset_context(conn: sqlite3.Connection, session_id: str) -> str:
    """构建数据集的简洁上下文（用于prompt）"""
    import json

    datasets = load_datasets(conn, session_id)

    if not datasets:
        return ""

    context_parts = []

    for ds in datasets:
        schema = json.loads(ds['schema_json'])
        stats = json.loads(ds['stats_json']) if ds['stats_json'] else {}
        sample = json.loads(ds['sample_rows']) if ds['sample_rows'] else []

        # 简洁描述
        desc = f"**{ds['dataset_id']}** ({ds['dataset_name']}): {ds['total_rows']} records"

        # Schema
        cols = ", ".join([col['name'] for col in schema])
        desc += f"\n  Columns: {cols}"

        # 关键统计
        if stats:
            stats_summary = []
            for col, stat in list(stats.items())[:3]:  # 最多3列
                if 'mean' in stat:
                    stats_summary.append(
                        f"{col}: {stat['min']:.1f}-{stat['max']:.1f} (mean {stat['mean']:.1f})"
                    )
            if stats_summary:
                desc += f"\n  Key stats: " + "; ".join(stats_summary)

        # 采样（只显示前3行的关键字段）
        if sample:
            desc += f"\n  Sample (first 3 of {len(sample)}):"
            for i, row in enumerate(sample[:3]):
                # 只显示前4个字段
                fields = list(row.items())[:4]
                row_str = ", ".join([f"{k}={v}" for k, v in fields])
                desc += f"\n    {i+1}. {row_str}"

        context_parts.append(desc)

    return "\n\n".join(context_parts)


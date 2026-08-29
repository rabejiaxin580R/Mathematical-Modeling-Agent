"""论文文献知识库 - 临时隔离的Per-Session文献存储与检索

设计原则
--------
- **隔离性**: 每个论文会话有独立的文献知识库，不污染全局knowledge_base
- **上下文管理**: LLM读取完整PDF → 提取相关片段 → 存入数据库 → 喂给论文生成LLM
- **两阶段处理**:
  1. 预处理阶段：DeepSeek读取PDF全文，提取与问题相关的内容
  2. 引用阶段：论文生成时检索相关文献片段，作为上下文

数据库表结构
-----------
  literature_papers       原始论文元数据（标题/作者/摘要/PDF链接）
  literature_chunks       LLM提取的相关内容片段（可检索）
  literature_citations    实际引用记录（哪一节引用了哪篇文献）

使用流程
-------
1. 搜索文献: search_literature() → 返回论文列表
2. 预处理: process_paper_with_llm() → LLM读取PDF并提取关键片段
3. 检索片段: search_chunks() → BM25检索相关片段
4. 生成引用: cite_paper() → 记录引用关系
"""

import sqlite3
import logging
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# ── DDL 定义 ─────────────────────────────────────────────────────────────────

LITERATURE_DDL = """
PRAGMA foreign_keys = ON;

-- ① 原始论文元数据（从搜索API获取）
CREATE TABLE IF NOT EXISTS literature_papers (
    session_id  TEXT NOT NULL,
    paper_id    TEXT NOT NULL,              -- 格式: LP1, LP2, LP3...
    source      TEXT NOT NULL CHECK(source IN ('arxiv', 'semantic_scholar')),
    ext_id      TEXT NOT NULL,              -- 外部ID（arXiv ID或S2 paper ID）
    title       TEXT NOT NULL CHECK(length(title) >= 5),
    authors     TEXT NOT NULL,              -- JSON数组: ["Author1", "Author2"]
    abstract    TEXT,
    published   TEXT,                       -- 发表日期 YYYY-MM-DD
    pdf_url     TEXT,
    venue       TEXT,                       -- 会议/期刊
    citation_count INTEGER DEFAULT 0,
    metadata    TEXT,                       -- JSON: 其他元数据（categories等）
    added_at    TEXT NOT NULL CHECK(added_at GLOB '????-??-??T??:??:??*'),
    PRIMARY KEY (session_id, paper_id),
    UNIQUE (session_id, ext_id)            -- 同一会话不重复添加同篇论文
);

-- ② LLM提取的内容片段（可检索）
CREATE TABLE IF NOT EXISTS literature_chunks (
    session_id  TEXT NOT NULL,
    chunk_id    TEXT NOT NULL,              -- 格式: LC1, LC2, LC3...
    paper_id    TEXT NOT NULL,              -- 关联 literature_papers
    chunk_type  TEXT NOT NULL CHECK(chunk_type IN (
        'abstract', 'introduction', 'method', 'result', 'conclusion', 'formula', 'other'
    )),
    content     TEXT NOT NULL CHECK(length(content) >= 10),
    relevance   TEXT,                       -- LLM评估的相关性说明
    page_ref    TEXT,                       -- 页码引用（如有）
    extracted_at TEXT NOT NULL CHECK(extracted_at GLOB '????-??-??T??:??:??*'),
    PRIMARY KEY (session_id, chunk_id),
    FOREIGN KEY (session_id, paper_id)
        REFERENCES literature_papers(session_id, paper_id) ON DELETE CASCADE
);

-- ③ 引用记录（论文生成时实际使用）
CREATE TABLE IF NOT EXISTS literature_citations (
    session_id  TEXT NOT NULL,
    section_key TEXT NOT NULL,              -- 哪一节引用了文献
    paper_id    TEXT NOT NULL,
    chunk_id    TEXT,                       -- 引用了哪个具体片段（可NULL表示整篇）
    citation_context TEXT,                  -- 引用上下文（为什么引用）
    cited_at    TEXT NOT NULL CHECK(cited_at GLOB '????-??-??T??:??:??*'),
    FOREIGN KEY (session_id, paper_id)
        REFERENCES literature_papers(session_id, paper_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id, chunk_id)
        REFERENCES literature_chunks(session_id, chunk_id) ON DELETE SET NULL
);

-- ④ 处理状态表（跟踪哪些论文已处理）
CREATE TABLE IF NOT EXISTS literature_processing (
    session_id  TEXT NOT NULL,
    paper_id    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'processing', 'completed', 'failed'
    )),
    error_msg   TEXT,
    processed_at TEXT CHECK(processed_at IS NULL OR processed_at GLOB '????-??-??T??:??:??*'),
    PRIMARY KEY (session_id, paper_id),
    FOREIGN KEY (session_id, paper_id)
        REFERENCES literature_papers(session_id, paper_id) ON DELETE CASCADE
);
"""

# 紧凑索引（给LLM看的）
LITERATURE_DDL_INDEX = (
    "literature_papers(session_id, paper_id PK GLOB 'LP[0-9]*', source IN arxiv/semantic_scholar, "
    "ext_id UNIQUE, title>=5chars, authors JSON, abstract, published, pdf_url, venue, "
    "citation_count, metadata JSON, added_at ISO8601) | "
    "literature_chunks(session_id, chunk_id PK GLOB 'LC[0-9]*', paper_id FK, "
    "chunk_type IN abstract/introduction/method/result/conclusion/formula/other, "
    "content>=10chars, relevance, page_ref, extracted_at ISO8601) | "
    "literature_citations(session_id, section_key, paper_id FK, chunk_id FK nullable, "
    "citation_context, cited_at ISO8601) | "
    "literature_processing(session_id, paper_id FK PK, status IN pending/processing/completed/failed, "
    "error_msg nullable, processed_at ISO8601 nullable)"
)


# ── 数据库连接 ───────────────────────────────────────────────────────────────

def get_lit_conn() -> sqlite3.Connection:
    """打开文献数据库（复用paper_sessions.db，加上文献表）

    这样做的好处：
    1. 文献数据与论文会话绑定，自动cascade删除
    2. 统一备份，无需管理多个数据库文件
    3. 事务一致性
    """
    try:
        from .paper_db import get_db_path
    except ImportError:
        # 直接运行时的fallback
        from paper_db import get_db_path
    path = get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(LITERATURE_DDL)
    return conn


# ── 论文管理 ────────────────────────────────────────────────────────────────

def store_papers(
    conn: sqlite3.Connection,
    session_id: str,
    papers: List[Dict]
) -> Dict:
    """批量存储搜索到的论文元数据

    Args:
        conn: 数据库连接
        session_id: 论文会话ID
        papers: 文献搜索返回的论文列表（来自literature_search.py）

    Returns:
        {"ok": True, "stored": 5, "skipped": 2}
    """
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    stored = 0
    skipped = 0

    for paper in papers:
        # 生成paper_id
        paper_id = _next_paper_id(conn, session_id)

        # 提取字段
        source = paper.get("source", "arxiv")
        ext_id = paper.get("id", "")
        title = paper.get("title", "Untitled")
        authors_list = paper.get("authors", [])
        authors_json = json.dumps(authors_list)
        abstract = paper.get("abstract", "")
        published = paper.get("published") or str(paper.get("year", ""))
        pdf_url = paper.get("pdf_url")
        venue = paper.get("venue") or (paper.get("categories", [""])[0] if paper.get("categories") else "")
        citation_count = paper.get("citation_count", 0)

        # 其他元数据
        metadata = {k: v for k, v in paper.items()
                   if k not in ["source", "id", "title", "authors", "abstract",
                               "published", "year", "pdf_url", "venue", "citation_count"]}
        metadata_json = json.dumps(metadata)

        try:
            conn.execute("""
                INSERT INTO literature_papers
                (session_id, paper_id, source, ext_id, title, authors, abstract,
                 published, pdf_url, venue, citation_count, metadata, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, paper_id, source, ext_id, title, authors_json, abstract,
                  published, pdf_url, venue, citation_count, metadata_json, now))

            # 初始化处理状态
            conn.execute("""
                INSERT INTO literature_processing (session_id, paper_id, status)
                VALUES (?, ?, 'pending')
            """, (session_id, paper_id))

            stored += 1

        except sqlite3.IntegrityError as e:
            # 重复论文，跳过
            logger.debug(f"跳过重复论文 {ext_id}: {e}")
            skipped += 1
            continue

    conn.commit()
    logger.info(f"存储文献：{stored}篇新增，{skipped}篇重复跳过")
    return {"ok": True, "stored": stored, "skipped": skipped}


def _next_paper_id(conn: sqlite3.Connection, session_id: str) -> str:
    """生成下一个paper_id"""
    row = conn.execute("""
        SELECT MAX(CAST(substr(paper_id, 3) AS INTEGER)) as max_id
        FROM literature_papers
        WHERE session_id = ?
    """, (session_id,)).fetchone()

    max_id = row["max_id"] if row and row["max_id"] else 0
    return f"LP{max_id + 1}"


def load_papers(conn: sqlite3.Connection, session_id: str) -> List[Dict]:
    """加载会话的所有论文"""
    rows = conn.execute("""
        SELECT * FROM literature_papers WHERE session_id = ?
        ORDER BY paper_id
    """, (session_id,)).fetchall()

    return [dict(r) for r in rows]


def get_paper(conn: sqlite3.Connection, session_id: str, paper_id: str) -> Optional[Dict]:
    """获取单篇论文详情"""
    row = conn.execute("""
        SELECT * FROM literature_papers
        WHERE session_id = ? AND paper_id = ?
    """, (session_id, paper_id)).fetchone()

    return dict(row) if row else None


# ── LLM 内容提取 ────────────────────────────────────────────────────────────

def process_paper_with_llm(
    conn: sqlite3.Connection,
    session_id: str,
    paper_id: str,
    llm_client,
    problem_description: str
) -> Dict:
    """使用LLM读取并提取论文相关内容

    工作流:
    1. 从数据库加载论文元数据
    2. 如果有PDF链接，让LLM读取全文（模拟，实际需要PDF解析）
    3. LLM提取与problem_description相关的片段
    4. 存入literature_chunks表

    Args:
        conn: 数据库连接
        session_id: 会话ID
        paper_id: 论文ID
        llm_client: OpenAI客户端（使用DeepSeek API）
        problem_description: 当前问题描述

    Returns:
        {"ok": True, "chunks": 3, "paper_id": "LP1"}
    """
    # 更新状态为processing
    conn.execute("""
        UPDATE literature_processing SET status = 'processing'
        WHERE session_id = ? AND paper_id = ?
    """, (session_id, paper_id))
    conn.commit()

    # 加载论文
    paper = get_paper(conn, session_id, paper_id)
    if not paper:
        return {"ok": False, "error": "Paper not found"}

    try:
        # 构建提取prompt
        prompt = _build_extraction_prompt(paper, problem_description)

        # 调用LLM（使用DeepSeek）
        response = llm_client.chat.completions.create(
            model="deepseek-v4-pro",  # 使用DeepSeek最强模型（1M上下文，384K输出）
            messages=[
                {"role": "system", "content": "你是论文分析助手，负责从学术论文中提取与数学建模问题相关的内容。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=4000,
        )

        result_text = response.choices[0].message.content.strip()

        # 解析LLM返回的JSON
        chunks_data = _parse_extraction_result(result_text)

        # 存储chunks
        chunks_stored = _store_chunks(conn, session_id, paper_id, chunks_data)

        # 更新状态为completed
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("""
            UPDATE literature_processing
            SET status = 'completed', processed_at = ?
            WHERE session_id = ? AND paper_id = ?
        """, (now, session_id, paper_id))
        conn.commit()

        logger.info(f"论文 {paper_id} 处理完成：提取 {chunks_stored} 个片段")
        return {"ok": True, "chunks": chunks_stored, "paper_id": paper_id}

    except Exception as e:
        logger.error(f"论文 {paper_id} 处理失败: {e}")
        conn.execute("""
            UPDATE literature_processing
            SET status = 'failed', error_msg = ?
            WHERE session_id = ? AND paper_id = ?
        """, (str(e), session_id, paper_id))
        conn.commit()
        return {"ok": False, "error": str(e)}


def _build_extraction_prompt(paper: Dict, problem_desc: str) -> str:
    """构建文献提取prompt"""
    authors_list = json.loads(paper["authors"])
    authors_str = ", ".join(authors_list[:3])
    if len(authors_list) > 3:
        authors_str += " et al."

    return f"""请从以下学术论文中提取与数学建模问题相关的关键内容。

**数学建模问题**:
{problem_desc[:800]}

**论文信息**:
- 标题: {paper['title']}
- 作者: {authors_str}
- 发表: {paper['published']}
- 摘要: {paper['abstract'][:500]}...

**任务**:
1. 判断该论文与问题的相关性（1-5分，5分最相关）
2. 如果相关性>=2，提取以下内容：
   - **核心方法**: 论文使用的数学模型/算法（200字以内）
   - **关键结果**: 主要发现和结论（150字以内）
   - **公式**: 重要的数学公式（如有）
   - **适用场景**: 该论文的方法适用于什么类型的问题

输出格式（JSON）:
```json
{{
  "relevance_score": 4,
  "relevance_reason": "该论文使用排队论建模急救资源分配，与本题的...",
  "chunks": [
    {{
      "type": "method",
      "content": "论文提出了基于M/M/c排队模型的...",
      "relevance": "直接适用于本题的资源分配建模"
    }},
    {{
      "type": "formula",
      "content": "期望等待时间公式: W_q = \\\\lambda / (\\\\mu (\\\\mu - \\\\lambda))",
      "relevance": "可用于计算响应时间"
    }},
    {{
      "type": "result",
      "content": "实验表明，当服务率提高20%时...",
      "relevance": "为灵敏度分析提供参考"
    }}
  ]
}}
```

**注意**:
- 只提取与问题直接相关的内容
- 如果relevance_score<2，返回空chunks数组
- 每个chunk控制在200字以内
"""


def _parse_extraction_result(result_text: str) -> List[Dict]:
    """解析LLM返回的提取结果"""
    import re

    # 尝试提取JSON
    match = re.search(r'```json\s*(.*?)\s*```', result_text, re.DOTALL)
    if match:
        json_text = match.group(1)
    else:
        json_text = result_text

    try:
        data = json.loads(json_text)
        relevance_score = data.get("relevance_score", 0)

        if relevance_score < 2:
            logger.info(f"论文相关性过低 ({relevance_score}/5)，跳过")
            return []

        chunks = data.get("chunks", [])
        return chunks

    except json.JSONDecodeError as e:
        logger.error(f"解析LLM返回失败: {e}\n{result_text[:200]}")
        return []


def _store_chunks(
    conn: sqlite3.Connection,
    session_id: str,
    paper_id: str,
    chunks_data: List[Dict]
) -> int:
    """存储提取的内容片段"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    count = 0

    # 允许的chunk_type枚举值
    VALID_TYPES = {'abstract', 'introduction', 'method', 'result', 'conclusion', 'formula', 'other'}

    # LLM返回值映射（防止LLM返回不规范的值）
    TYPE_MAPPING = {
        'methods': 'method',
        'methodology': 'method',
        'approach': 'method',
        'results': 'result',
        'findings': 'result',
        'conclusions': 'conclusion',
        'discussion': 'conclusion',
        'equation': 'formula',
        'equations': 'formula',
        'math': 'formula',
        'scenario': 'other',
        'applicable_scenario': 'other',
        'applicability': 'other',
        'application': 'other',
    }

    for chunk in chunks_data:
        chunk_id = _next_chunk_id(conn, session_id)
        raw_type = chunk.get("type", "other").lower().strip()

        # 映射到标准类型
        chunk_type = TYPE_MAPPING.get(raw_type, raw_type)

        # 验证是否在允许的枚举中
        if chunk_type not in VALID_TYPES:
            logger.warning(f"无效的chunk_type '{raw_type}'，使用默认值 'other'")
            chunk_type = "other"
        content = chunk.get("content", "")
        relevance = chunk.get("relevance", "")

        if not content or len(content) < 10:
            continue

        conn.execute("""
            INSERT INTO literature_chunks
            (session_id, chunk_id, paper_id, chunk_type, content, relevance, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, chunk_id, paper_id, chunk_type, content, relevance, now))

        count += 1

    conn.commit()
    return count


def _next_chunk_id(conn: sqlite3.Connection, session_id: str) -> str:
    """生成下一个chunk_id"""
    row = conn.execute("""
        SELECT MAX(CAST(substr(chunk_id, 3) AS INTEGER)) as max_id
        FROM literature_chunks
        WHERE session_id = ?
    """, (session_id,)).fetchone()

    max_id = row["max_id"] if row and row["max_id"] else 0
    return f"LC{max_id + 1}"


# ── 检索与引用 ──────────────────────────────────────────────────────────────

def search_chunks(
    conn: sqlite3.Connection,
    session_id: str,
    query: str,
    top_k: int = 5
) -> List[Dict]:
    """检索相关的文献片段（改进版：支持多关键词OR匹配）

    TODO: 可升级为BM25或向量检索

    Args:
        conn: 数据库连接
        session_id: 会话ID
        query: 检索查询（如"排队论模型"或"modeling formulation method"）
        top_k: 返回前K个结果

    Returns:
        片段列表，每个包含chunk信息+关联的paper信息
    """
    # 拆分关键词，支持多关键词OR匹配
    keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 2]

    if not keywords:
        # 空查询，返回所有
        rows = conn.execute("""
            SELECT
                c.*,
                p.title as paper_title,
                p.authors as paper_authors,
                p.published as paper_published
            FROM literature_chunks c
            JOIN literature_papers p ON c.session_id = p.session_id AND c.paper_id = p.paper_id
            WHERE c.session_id = ?
            ORDER BY c.chunk_id
            LIMIT ?
        """, (session_id, top_k)).fetchall()
        return [dict(r) for r in rows]

    # 构建OR条件
    conditions = []
    params = [session_id]

    for kw in keywords:
        conditions.append("(c.content LIKE ? OR c.relevance LIKE ? OR p.title LIKE ? OR p.abstract LIKE ?)")
        like_pattern = f"%{kw}%"
        params.extend([like_pattern, like_pattern, like_pattern, like_pattern])

    where_clause = " OR ".join(conditions)
    params.append(top_k)

    sql = f"""
        SELECT
            c.*,
            p.title as paper_title,
            p.authors as paper_authors,
            p.published as paper_published
        FROM literature_chunks c
        JOIN literature_papers p ON c.session_id = p.session_id AND c.paper_id = p.paper_id
        WHERE c.session_id = ? AND ({where_clause})
        ORDER BY c.chunk_id
        LIMIT ?
    """

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def cite_paper(
    conn: sqlite3.Connection,
    session_id: str,
    section_key: str,
    paper_id: str,
    chunk_id: Optional[str] = None,
    citation_context: str = ""
) -> Dict:
    """记录文献引用

    Args:
        conn: 数据库连接
        session_id: 会话ID
        section_key: 章节标识（如'build'）
        paper_id: 被引用的论文ID
        chunk_id: 具体引用的片段ID（可选）
        citation_context: 引用上下文说明

    Returns:
        {"ok": True}
    """
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.execute("""
        INSERT INTO literature_citations
        (session_id, section_key, paper_id, chunk_id, citation_context, cited_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, section_key, paper_id, chunk_id, citation_context, now))

    conn.commit()
    return {"ok": True}


def load_citations(conn: sqlite3.Connection, session_id: str, section_key: Optional[str] = None) -> List[Dict]:
    """加载引用记录"""
    if section_key:
        rows = conn.execute("""
            SELECT c.*, p.title, p.authors, p.published
            FROM literature_citations c
            JOIN literature_papers p ON c.session_id = p.session_id AND c.paper_id = p.paper_id
            WHERE c.session_id = ? AND c.section_key = ?
            ORDER BY c.cited_at
        """, (session_id, section_key)).fetchall()
    else:
        rows = conn.execute("""
            SELECT c.*, p.title, p.authors, p.published
            FROM literature_citations c
            JOIN literature_papers p ON c.session_id = p.session_id AND c.paper_id = p.paper_id
            WHERE c.session_id = ?
            ORDER BY c.section_key, c.cited_at
        """, (session_id,)).fetchall()

    return [dict(r) for r in rows]


# ── 批量处理 ────────────────────────────────────────────────────────────────

def batch_process_papers(
    conn: sqlite3.Connection,
    session_id: str,
    llm_client,
    problem_description: str,
    max_papers: int = 10
) -> Dict:
    """批量处理待处理的论文

    Args:
        conn: 数据库连接
        session_id: 会话ID
        llm_client: LLM客户端
        problem_description: 问题描述
        max_papers: 最多处理多少篇（避免API成本过高）

    Returns:
        {"ok": True, "processed": 5, "failed": 1, "total_chunks": 23}
    """
    # 获取待处理论文
    rows = conn.execute("""
        SELECT paper_id FROM literature_processing
        WHERE session_id = ? AND status = 'pending'
        ORDER BY paper_id
        LIMIT ?
    """, (session_id, max_papers)).fetchall()

    pending_papers = [r["paper_id"] for r in rows]

    if not pending_papers:
        logger.info("没有待处理的论文")
        return {"ok": True, "processed": 0, "failed": 0, "total_chunks": 0}

    logger.info(f"开始批量处理 {len(pending_papers)} 篇论文...")

    processed = 0
    failed = 0
    total_chunks = 0

    for paper_id in pending_papers:
        result = process_paper_with_llm(conn, session_id, paper_id, llm_client, problem_description)

        if result.get("ok"):
            processed += 1
            total_chunks += result.get("chunks", 0)
        else:
            failed += 1

    logger.info(f"批量处理完成：{processed}篇成功，{failed}篇失败，共提取{total_chunks}个片段")

    return {
        "ok": True,
        "processed": processed,
        "failed": failed,
        "total_chunks": total_chunks
    }


# ── 上下文构建 ──────────────────────────────────────────────────────────────

def build_literature_context(
    conn: sqlite3.Connection,
    session_id: str,
    query: str = "",
    max_chunks: int = 5
) -> str:
    """构建文献上下文（供论文生成LLM使用）

    Args:
        conn: 数据库连接
        session_id: 会话ID
        query: 检索关键词（为空则返回所有）
        max_chunks: 最多包含多少个片段

    Returns:
        格式化的文献上下文字符串
    """
    if query:
        chunks = search_chunks(conn, session_id, query, top_k=max_chunks)
    else:
        # 返回所有chunks（按paper分组）
        rows = conn.execute("""
            SELECT
                c.*,
                p.title as paper_title,
                p.authors as paper_authors,
                p.published as paper_published
            FROM literature_chunks c
            JOIN literature_papers p ON c.session_id = p.session_id AND c.paper_id = p.paper_id
            WHERE c.session_id = ?
            ORDER BY c.paper_id, c.chunk_id
            LIMIT ?
        """, (session_id, max_chunks)).fetchall()
        chunks = [dict(r) for r in rows]

    if not chunks:
        return ""

    lines = ["## 📚 相关文献\n"]

    current_paper_id = None

    for chunk in chunks:
        # 新论文，输出论文标题
        if chunk["paper_id"] != current_paper_id:
            current_paper_id = chunk["paper_id"]
            authors_list = json.loads(chunk["paper_authors"])
            authors_str = authors_list[0] if authors_list else "Unknown"
            if len(authors_list) > 1:
                authors_str += " et al."

            lines.append(f"\n**[{chunk['paper_id']}]** {authors_str} ({chunk['paper_published'][:4]}). {chunk['paper_title']}")

        # 输出chunk
        chunk_type_label = {
            "method": "方法",
            "result": "结果",
            "formula": "公式",
            "abstract": "摘要",
            "conclusion": "结论",
            "other": "其他"
        }.get(chunk["chunk_type"], chunk["chunk_type"])

        lines.append(f"  - **{chunk_type_label}**: {chunk['content']}")
        if chunk.get("relevance"):
            lines.append(f"    *相关性*: {chunk['relevance']}")

    return "\n".join(lines)


# ── 统计与调试 ──────────────────────────────────────────────────────────────

def get_literature_stats(conn: sqlite3.Connection, session_id: str) -> Dict:
    """获取文献库统计信息"""
    stats = {}

    # 论文总数
    row = conn.execute("""
        SELECT COUNT(*) as total FROM literature_papers WHERE session_id = ?
    """, (session_id,)).fetchone()
    stats["total_papers"] = row["total"]

    # 处理状态
    rows = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM literature_processing
        WHERE session_id = ?
        GROUP BY status
    """, (session_id,)).fetchall()
    stats["processing_status"] = {r["status"]: r["count"] for r in rows}

    # 片段总数
    row = conn.execute("""
        SELECT COUNT(*) as total FROM literature_chunks WHERE session_id = ?
    """, (session_id,)).fetchone()
    stats["total_chunks"] = row["total"]

    # 引用次数
    row = conn.execute("""
        SELECT COUNT(*) as total FROM literature_citations WHERE session_id = ?
    """, (session_id,)).fetchone()
    stats["total_citations"] = row["total"]

    return stats

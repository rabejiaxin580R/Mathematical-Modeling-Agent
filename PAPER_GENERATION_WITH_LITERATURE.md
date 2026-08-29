# 📚 论文生成与文献集成系统 - 完整调用流程

## 🎯 系统概述

本系统实现了**自动搜索文献 → LLM提取相关内容 → 数据库存储 → 论文生成时引用**的完整流程。

### 核心特性

- ✅ **独立临时知识库**：每个论文会话拥有独立的文献数据库
- ✅ **智能提取**：DeepSeek-V4-Pro 自动提取论文中与问题相关的内容
- ✅ **多查询检索**：每个章节使用3个不同查询词，覆盖更多文献
- ✅ **自动引用**：检索到的文献自动记录引用关系
- ✅ **去重机制**：避免重复片段和重复引用

---

## 📂 核心模块

### 1. `paper_literature.py` - 文献知识库核心

**位置**: `agent/backend/paper_literature.py`

**数据库**: `paper_sessions.db` (SQLite)

**表结构**:
```sql
-- 会话表
paper_sessions (session_id, problem_md, created_at, metadata)

-- 文献元数据表
literature_papers (paper_id, session_id, title, authors, year, abstract, source, url)

-- 文献片段表（LLM提取）
literature_chunks (
    chunk_id, paper_id, session_id,
    chunk_type,    -- abstract/introduction/method/result/conclusion/formula/other
    content,       -- 提取的内容
    relevance,     -- 与问题的相关性说明
    keywords       -- 关键词（逗号分隔）
)

-- 引用记录表
literature_citations (
    citation_id, session_id, section_key, paper_id,
    context,      -- 引用上下文
    cited_at
)
```

**核心函数**:

```python
# 创建会话
create_paper_session(conn, session_id, problem_md, metadata) -> dict

# 存储文献元数据
store_literature(conn, session_id, papers: List[Dict]) -> dict

# LLM批量提取相关内容
process_paper_batch(conn, session_id, problem_md, llm_client, paper_ids) -> dict

# 检索文献片段
retrieve_chunks(conn, session_id, query, limit=10) -> List[Dict]

# 构建文献上下文（用于注入prompt）
build_literature_context(conn, session_id, query, max_chars=3000) -> str

# 记录引用
record_citation(conn, session_id, section_key, paper_id, context) -> dict

# 查询引用记录
load_citations(conn, session_id, section_key=None) -> List[Dict]

# 统计信息
get_session_stats(conn, session_id) -> dict
```

---

### 2. `paper_gen.py` - 论文生成引擎

**位置**: `agent/backend/paper_gen.py`

**集成点**: 3个关键位置

#### **集成点 1: 初始化文献库** (line 975)

```python
# 在生成第一个章节时初始化
if section_key == sections[0]:
    try:
        from . import paper_literature
        lit_conn = sqlite3.connect("paper_sessions.db")
        
        # 创建会话
        paper_literature.create_paper_session(
            lit_conn, 
            session_id, 
            problem_md
        )
        
        # 搜索并存储文献
        from .literature_search import search_literature
        papers = search_literature(query, max_per_source=10)
        paper_literature.store_literature(lit_conn, session_id, papers)
        
        # LLM提取相关内容
        llm_client = OpenAI(
            api_key=config.get_llm_api_key(),
            base_url=config.get_llm_base_url()
        )
        paper_literature.process_paper_batch(
            lit_conn, session_id, problem_md, llm_client
        )
    except Exception as e:
        logger.warning(f"文献库初始化失败，将继续: {e}")
```

#### **集成点 2: 检索文献片段** (line 1020)

```python
# 为 build/solve/analyze 章节检索文献
if section_key in ['build', 'solve', 'analyze']:
    try:
        from . import paper_literature
        lit_conn = sqlite3.connect("paper_sessions.db")
        
        # 多查询策略
        queries = {
            'build': [
                "modeling formulation method",
                "optimization model",
                "mathematical framework"
            ],
            'solve': [
                "algorithm solution",
                "optimization solver",
                "computational method"
            ],
            'analyze': [
                "validation analysis",
                "sensitivity test",
                "result evaluation"
            ]
        }
        
        # 检索并去重
        all_chunks = []
        seen_ids = set()
        
        for q in queries[section_key]:
            chunks = paper_literature.retrieve_chunks(
                lit_conn, session_id, q, limit=10
            )
            for chunk in chunks:
                if chunk['chunk_id'] not in seen_ids:
                    all_chunks.append(chunk)
                    seen_ids.add(chunk['chunk_id'])
        
        # 取前15个
        selected_chunks = all_chunks[:15]
        
        # 构建上下文
        literature_context = paper_literature.build_literature_context_from_chunks(
            lit_conn, session_id, selected_chunks, max_chars=3000
        )
        
        # 记录引用
        cited_papers = set(c['paper_id'] for c in selected_chunks)
        for paper_id in cited_papers:
            paper_literature.record_citation(
                lit_conn, session_id, section_key, paper_id, 
                context=f"使用该论文的方法/结果支持{section_key}章节"
            )
            
        logger.info(f"为 {section_key} 章节检索到 {len(selected_chunks)} 个文献片段，引用 {len(cited_papers)} 篇论文")
        
    except Exception as e:
        logger.warning(f"文献检索失败: {e}")
        literature_context = ""
```

#### **集成点 3: 生成参考文献** (line 1120)

```python
# 在论文末尾添加 References 章节
try:
    from . import paper_literature
    lit_conn = sqlite3.connect("paper_sessions.db")
    
    # 查询所有引用记录
    citations = paper_literature.load_citations(lit_conn, session_id)
    
    if citations:
        # 去重
        cited_paper_ids = list(set(c['paper_id'] for c in citations))
        
        # 查询论文元数据
        cursor = lit_conn.execute("""
            SELECT paper_id, title, authors, year
            FROM literature_papers
            WHERE paper_id IN ({})
        """.format(','.join('?' * len(cited_paper_ids))), cited_paper_ids)
        
        papers = cursor.fetchall()
        
        # 格式化引用
        refs_text = "\n# References\n\n"
        for paper_id, title, authors, year in papers:
            # 截断标题
            title_short = title[:60] + "..." if len(title) > 60 else title
            refs_text += f"- [{paper_id}] {authors} ({year}). *{title_short}*.\n"
        
        # 追加到论文
        paper_markdown += "\n\n" + refs_text
        
except Exception as e:
    logger.warning(f"生成参考文献失败: {e}")
```

---

### 3. `literature_search.py` - 文献搜索

**位置**: `agent/backend/literature_search.py`

**数据源**:
- arXiv API (学术论文)
- Semantic Scholar API (论文引用数据)

**函数**:
```python
def search_literature(
    query: str,
    max_per_source: int = 10
) -> List[Dict]:
    """
    搜索文献
    
    Returns:
        [
            {
                'title': str,
                'authors': str,
                'year': int,
                'abstract': str,
                'source': 'arxiv' | 'semantic_scholar',
                'url': str,
                'fulltext': str  # 如果可用
            },
            ...
        ]
    """
```

---

## 🔄 完整调用流程

### Step 1: 用户发起论文生成请求

```python
# 前端 → 后端
POST /api/paper/generate
{
    "problem_md": "# 问题描述\n...",
    "query": "emergency facility location optimization"
}
```

### Step 2: 创建论文会话

```python
# paper_gen.py
session_id = f"ps_{uuid.uuid4().hex}"
lit_conn = sqlite3.connect("paper_sessions.db")

paper_literature.create_paper_session(
    lit_conn, 
    session_id, 
    problem_md,
    metadata={'query': query, 'created_at': datetime.now()}
)
```

### Step 3: 搜索并存储文献

```python
# 调用文献搜索
from .literature_search import search_literature
papers = search_literature(query, max_per_source=10)

# 存储到数据库
result = paper_literature.store_literature(lit_conn, session_id, papers)
# → {'ok': True, 'stored': 10, 'skipped': 0}
```

### Step 4: LLM提取相关内容

```python
# 初始化 LLM 客户端
llm_client = OpenAI(
    api_key=config.get_llm_api_key(),
    base_url=config.get_llm_base_url()
)

# 批量处理所有论文
result = paper_literature.process_paper_batch(
    lit_conn, 
    session_id, 
    problem_md, 
    llm_client,
    paper_ids=None  # None = 处理所有论文
)
# → {'ok': True, 'processed': 10, 'failed': 0, 'total_chunks': 30}
```

**LLM Prompt 示例**:
```
你是文献分析专家。给定一个数学建模问题和一篇学术论文，请提取与问题相关的关键内容。

问题描述：
[problem_md]

论文信息：
标题: [title]
摘要: [abstract]
全文: [fulltext if available]

请提取以下内容（JSON格式）：
{
    "chunks": [
        {
            "type": "method|result|formula|introduction|conclusion|other",
            "content": "提取的文本（保留公式和关键细节）",
            "relevance": "与问题的相关性说明",
            "keywords": "关键词1, 关键词2, ..."
        },
        ...
    ]
}

要求：
1. 只提取与问题直接相关的内容
2. 每个chunk保持完整性（不要截断句子）
3. 保留数学公式和符号
4. 每篇论文提取2-5个chunks
5. 如果论文不相关，返回空数组
```

### Step 5: 生成论文章节（循环）

```python
sections = ['restate', 'assume', 'notation', 'build', 'solve', 'analyze', ...]

for section_key in sections:
    # 5.1 检索文献片段（仅 build/solve/analyze）
    if section_key in ['build', 'solve', 'analyze']:
        queries = {
            'build': ["modeling formulation", "optimization model", "mathematical framework"],
            'solve': ["algorithm solution", "optimization solver", "computational method"],
            'analyze': ["validation", "sensitivity", "result evaluation"]
        }
        
        # 多查询检索
        all_chunks = []
        seen_ids = set()
        for q in queries[section_key]:
            chunks = paper_literature.retrieve_chunks(
                lit_conn, session_id, q, limit=10
            )
            for chunk in chunks:
                if chunk['chunk_id'] not in seen_ids:
                    all_chunks.append(chunk)
                    seen_ids.add(chunk['chunk_id'])
        
        selected_chunks = all_chunks[:15]  # 取前15个
        
        # 构建文献上下文
        literature_context = paper_literature.build_literature_context_from_chunks(
            lit_conn, session_id, selected_chunks, max_chars=3000
        )
        
        # 记录引用
        cited_papers = set(c['paper_id'] for c in selected_chunks)
        for paper_id in cited_papers:
            paper_literature.record_citation(
                lit_conn, session_id, section_key, paper_id
            )
    
    # 5.2 构建 prompt
    prompt = _build_user_prompt(
        section_key=section_key,
        problem_md=problem_md,
        literature_context=literature_context,  # 注入文献
        ...
    )
    
    # 5.3 调用 LLM 生成
    response = llm_client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[
            {"role": "system", "content": writer_persona},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=100000
    )
    
    section_content = response.choices[0].message.content
    paper_markdown += "\n\n" + section_content
```

### Step 6: 生成参考文献

```python
# 查询所有引用记录
citations = paper_literature.load_citations(lit_conn, session_id)

# 去重
cited_paper_ids = list(set(c['paper_id'] for c in citations))

# 查询论文元数据
cursor = lit_conn.execute("""
    SELECT paper_id, title, authors, year
    FROM literature_papers
    WHERE paper_id IN ({})
""".format(','.join('?' * len(cited_paper_ids))), cited_paper_ids)

papers = cursor.fetchall()

# 格式化
refs_text = "\n# References\n\n"
for paper_id, title, authors, year in papers:
    refs_text += f"- [{paper_id}] {authors} ({year}). *{title}*.\n"

paper_markdown += "\n\n" + refs_text
```

### Step 7: 导出为 Word

```python
from .export_docx import markdown_to_docx

docx_path = markdown_to_docx(paper_markdown, output_path)
```

### Step 8: 返回结果

```python
{
    "ok": True,
    "session_id": "ps_xxx",
    "markdown_path": "complete_paper.md",
    "docx_path": "complete_paper.docx",
    "stats": {
        "sections": 9,
        "papers": 10,
        "chunks": 30,
        "citations": 18
    }
}
```

---

## 🔍 检索算法详解

### 多查询策略

每个章节使用3个不同的查询词，覆盖不同角度：

```python
SECTION_QUERIES = {
    'build': [
        "modeling formulation method",    # 建模方法
        "optimization model",              # 优化模型
        "mathematical framework"           # 数学框架
    ],
    'solve': [
        "algorithm solution",              # 算法求解
        "optimization solver",             # 优化求解器
        "computational method"             # 计算方法
    ],
    'analyze': [
        "validation analysis",             # 验证分析
        "sensitivity test",                # 敏感性测试
        "result evaluation"                # 结果评估
    ]
}
```

### 关键词OR匹配

```python
def retrieve_chunks(conn, session_id, query, limit=10):
    # 拆分查询词
    terms = query.lower().split()
    
    # 构建 OR 条件
    conditions = []
    params = []
    for term in terms:
        conditions.append("(content LIKE ? OR keywords LIKE ?)")
        params.extend([f"%{term}%", f"%{term}%"])
    
    where_clause = " OR ".join(conditions)
    
    # 执行查询
    cursor = conn.execute(f"""
        SELECT chunk_id, paper_id, chunk_type, content, relevance, keywords
        FROM literature_chunks
        WHERE session_id = ? AND ({where_clause})
        ORDER BY LENGTH(content) DESC
        LIMIT ?
    """, [session_id] + params + [limit])
    
    return cursor.fetchall()
```

### 去重机制

```python
all_chunks = []
seen_ids = set()

for q in queries:
    chunks = retrieve_chunks(conn, session_id, q, limit=10)
    for chunk in chunks:
        if chunk['chunk_id'] not in seen_ids:
            all_chunks.append(chunk)
            seen_ids.add(chunk['chunk_id'])

# 取前15个
selected_chunks = all_chunks[:15]
```

---

## 📊 数据流图

```
┌──────────────┐
│   用户请求    │
│ (问题描述)    │
└──────┬───────┘
       │
       ▼
┌──────────────────────┐
│  1. 创建论文会话      │
│  paper_sessions       │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  2. 搜索文献          │
│  (arXiv + S2)        │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  3. 存储文献元数据     │
│  literature_papers    │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  4. LLM提取相关内容   │
│  (DeepSeek-V4-Pro)   │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  5. 存储文献片段       │
│  literature_chunks    │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  6. 生成章节 (循环)   │
│  ┌────────────────┐  │
│  │ 6.1 检索片段   │  │
│  │ 6.2 构建上下文 │  │
│  │ 6.3 记录引用   │  │
│  │ 6.4 调用LLM    │  │
│  └────────────────┘  │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  7. 生成参考文献      │
│  literature_citations │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  8. 导出 Word         │
│  (.md + .docx)       │
└──────────────────────┘
```

---

## 🎯 关键参数配置

### LLM 配置

```python
MODEL = "deepseek-v4-pro"
BASE_URL = "https://api.deepseek.com"
API_KEY = "sk-..."

# 提取参数
EXTRACT_TEMPERATURE = 0.3
EXTRACT_MAX_TOKENS = 384000  # V4-Pro 支持 384K 输出

# 生成参数
GENERATE_TEMPERATURE = 0.3
GENERATE_MAX_TOKENS = 100000
```

### 检索参数

```python
# 每个查询返回的片段数
CHUNKS_PER_QUERY = 10

# 每个章节最多使用的片段数
MAX_CHUNKS_PER_SECTION = 15

# 文献上下文最大字符数
MAX_LITERATURE_CONTEXT_CHARS = 3000

# 每个章节的查询数
QUERIES_PER_SECTION = 3
```

### 搜索参数

```python
# 每个数据源搜索的论文数
MAX_PAPERS_PER_SOURCE = 10

# 数据源
SOURCES = ['arxiv', 'semantic_scholar']
```

---

## 🐛 错误处理

### 1. 文献库初始化失败

```python
try:
    paper_literature.create_paper_session(...)
except Exception as e:
    logger.warning(f"文献库初始化失败，将继续: {e}")
    # 继续生成论文，但没有文献支持
```

### 2. LLM 提取失败

```python
try:
    result = paper_literature.process_paper_batch(...)
    logger.info(f"提取 {result['total_chunks']} 个片段")
except Exception as e:
    logger.error(f"文献提取失败: {e}")
    # 仍然存储了元数据，可以手动重试
```

### 3. 检索失败

```python
try:
    chunks = paper_literature.retrieve_chunks(...)
except Exception as e:
    logger.warning(f"文献检索失败: {e}")
    literature_context = ""  # 使用空上下文
```

### 4. 引用生成失败

```python
try:
    citations = paper_literature.load_citations(...)
except Exception as e:
    logger.warning(f"生成参考文献失败: {e}")
    # 论文仍然可用，只是没有 References 章节
```

---

## 📈 性能数据

### 测试案例: "应急设施选址优化"

| 阶段 | 时间 | 备注 |
|------|------|------|
| 搜索文献 | ~10秒 | arXiv API + Semantic Scholar |
| 存储元数据 | <1秒 | SQLite |
| LLM提取片段 | ~8分钟 | 10篇论文 × 平均50秒 |
| 生成章节 | ~15分钟 | 9个章节 × 平均1.5分钟 |
| **总计** | **~25分钟** | |

### 输出统计

| 指标 | 数值 |
|------|------|
| 搜索论文 | 10 篇 |
| 提取片段 | 30 个 |
| 引用记录 | 18 次 |
| 实际引用论文 | 7 篇 |
| 论文长度 | 47,351 字符 |
| Word 文件大小 | 59 KB |

---

## 🔧 调试工具

### 查看会话统计

```python
from backend.paper_literature import get_session_stats
import sqlite3

conn = sqlite3.connect("paper_sessions.db")
stats = get_session_stats(conn, "ps_xxx")

print(stats)
# {
#     'total_papers': 10,
#     'processing_status': {'completed': 10},
#     'total_chunks': 30,
#     'citation_count': 18
# }
```

### 查看引用记录

```python
from backend.paper_literature import load_citations

citations = load_citations(conn, "ps_xxx", section_key="build")

for c in citations:
    print(f"{c['paper_id']}: {c['context']}")
```

### 手动检索测试

```python
from backend.paper_literature import retrieve_chunks

chunks = retrieve_chunks(
    conn, 
    session_id="ps_xxx", 
    query="optimization algorithm", 
    limit=5
)

for chunk in chunks:
    print(f"[{chunk['paper_id']}] {chunk['chunk_type']}")
    print(chunk['content'][:200])
    print()
```

---

## 📚 API 接口

### 创建论文会话

```python
POST /api/paper/session/create
{
    "problem_md": "# 问题\n...",
    "query": "optimization location"
}

Response:
{
    "ok": True,
    "session_id": "ps_xxx",
    "papers_found": 10
}
```

### 手动添加文献

```python
POST /api/paper/session/{session_id}/literature
{
    "papers": [
        {
            "title": "...",
            "authors": "...",
            "year": 2024,
            "abstract": "...",
            "url": "...",
            "fulltext": "..."
        }
    ]
}

Response:
{
    "ok": True,
    "stored": 5,
    "skipped": 0
}
```

### 提取文献内容

```python
POST /api/paper/session/{session_id}/extract
{
    "paper_ids": ["LP1", "LP2"]  # 可选，null=全部
}

Response:
{
    "ok": True,
    "processed": 2,
    "failed": 0,
    "total_chunks": 8
}
```

### 生成论文

```python
POST /api/paper/generate
{
    "session_id": "ps_xxx",
    "problem_md": "...",
    "sections": ["restate", "build", "solve", ...]
}

Response:
{
    "ok": True,
    "markdown": "...",
    "markdown_path": "...",
    "docx_path": "...",
    "stats": {...}
}
```

---

## 🎓 最佳实践

### 1. 搜索关键词优化

✅ **好的关键词**:
```python
"emergency facility location optimization queueing"
"ambulance station placement coverage response time"
"stochastic location model capacity planning"
```

❌ **不好的关键词**:
```python
"optimization"  # 太宽泛
"modeling"      # 太宽泛
"math"          # 无关
```

### 2. 控制文献数量

```python
# 推荐: 10-15 篇论文
papers = search_literature(query, max_per_source=10)

# 太少: 引用不够
papers = search_literature(query, max_per_source=3)

# 太多: 提取时间过长
papers = search_literature(query, max_per_source=50)
```

### 3. 章节文献匹配

只为**关键章节**检索文献:

```python
LITERATURE_SECTIONS = ['build', 'solve', 'analyze']

# 不需要文献支持的章节:
# - restate (重述问题)
# - assume (假设)
# - notation (符号)
# - abstract (摘要)
```

### 4. 错误恢复

```python
# 提取失败 → 重试单篇论文
paper_literature.process_paper_batch(
    conn, session_id, problem_md, llm_client,
    paper_ids=["LP4"]  # 只重试 LP4
)

# 检索失败 → 降级到空上下文
try:
    literature_context = build_literature_context(...)
except:
    literature_context = ""  # 继续生成，但没有文献
```

---

## 🚀 未来改进

### 1. 向量检索

```python
# 当前: 关键词匹配
WHERE content LIKE '%optimization%'

# 改进: 向量相似度
WHERE embedding <-> query_embedding < 0.3
```

### 2. 引用排序

```python
# 按引用次数排序
ORDER BY citation_count DESC

# 按相关性排序
ORDER BY relevance_score DESC
```

### 3. 增量更新

```python
# 论文生成后，用户可以添加新文献
add_literature(session_id, new_papers)
regenerate_section(session_id, section_key="build")
```

### 4. 引用格式化

```python
# 支持多种引用格式
format_citations(citations, style="APA")
format_citations(citations, style="IEEE")
format_citations(citations, style="MLA")
```

---

## 📞 联系与支持

- **问题报告**: 查看 `paper_sessions.db` 日志
- **性能优化**: 调整 `CHUNKS_PER_QUERY` 和 `MAX_CHUNKS_PER_SECTION`
- **自定义提取**: 修改 `paper_literature.py` 中的 LLM prompt

---

**最后更新**: 2026-08-19  
**版本**: 1.0.0

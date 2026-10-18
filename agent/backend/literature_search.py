"""文献搜索与管理模块

为论文生成系统提供自主文献检索能力：
1. 搜索 arXiv 和 Semantic Scholar
2. LLM 筛选相关文献
3. 提取摘要、方法、结论
4. 存入知识库供后续引用

依赖安装：
    pip install arxiv requests
"""
import logging
import json
import os
import time
import hashlib
import math
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
from datetime import datetime
import requests

logger = logging.getLogger(__name__)

# ========== 配置 ==========

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
# Semantic Scholar 免费，但有速率限制（100 req/5min，未认证时更紧，常 429）。
# 可设置环境变量 SEMANTIC_SCHOLAR_API_KEY 提高限额；见 _semantic_headers()。
# 网络代理：requests 会自动读取 HTTPS_PROXY / HTTP_PROXY 环境变量（含 .env 经 load_dotenv 注入的），
# 用于国内访问 arXiv / Semantic Scholar 时挂代理。无需额外代码。

# Source limits apply across all conversations in this local server process.
# Busy sources fail fast; callers keep their drafts and can retry later.
_arxiv_lock = threading.Lock()
_arxiv_client = None
_arxiv_next_at = 0.0
_semantic_lock = threading.Lock()
_semantic_next_at = 0.0
_search_capacity = threading.BoundedSemaphore(2)
_search_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="literature-search")
_SEARCH_TIMEOUT = 20.0
_RATE_WAIT_BUDGET = 3.1
_monotonic = time.monotonic
_sleep = time.sleep
_wall_time = time.time


class _TimedSession(requests.Session):
    """arxiv.Client has no public request-timeout option in arxiv 2.x/4.x."""

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (4, 10)
        return super().request(method, url, **kwargs)


def _wait_until(next_at):
    delay = max(0.0, next_at - _monotonic())
    if delay > _RATE_WAIT_BUDGET:
        return False
    if delay:
        _sleep(delay)
    return True


def _semantic_interval():
    # Anonymous public requests commonly need more spacing than a keyed client.
    default = 1.0 if _semantic_headers() else 3.0
    try:
        value = float(os.getenv("LITERATURE_SEMANTIC_INTERVAL_SECONDS", str(default)))
        return max(default, value) if math.isfinite(value) else default
    except ValueError:
        return default


def _retry_after_seconds(value):
    """Honor seconds and HTTP dates; missing/invalid headers use a short cooldown."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(str(value)).timestamp() - _wall_time()
        except (TypeError, ValueError, OverflowError):
            return 60.0
    return max(0.0, seconds) if math.isfinite(seconds) else 60.0


def _semantic_headers() -> Dict[str, str]:
    """Semantic Scholar 请求头；配置了 API key 则附带，未配置则匿名。"""
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        return {"x-api-key": api_key}
    return {}


# ========== arXiv 搜索 ==========

def search_arxiv(query: str, max_results: int = 10) -> List[Dict]:
    """搜索 arXiv 论文

    Args:
        query: 搜索关键词（如 "mathematical modeling optimization"）
        max_results: 最多返回论文数

    Returns:
        论文列表，每篇包含：
        {
            "source": "arxiv",
            "id": "2301.12345",
            "title": "...",
            "authors": ["Author1", "Author2"],
            "abstract": "...",
            "published": "2023-01-15",
            "pdf_url": "https://arxiv.org/pdf/2301.12345",
            "categories": ["cs.LG", "math.OC"]
        }
    """
    global _arxiv_client, _arxiv_next_at
    if not _arxiv_lock.acquire(blocking=False):
        logger.info("arXiv 已有请求运行，本次查询跳过，避免排队积压")
        return []
    try:
        import arxiv
    except ImportError:
        _arxiv_lock.release()
        logger.error("arxiv 库未安装，请运行: pip install arxiv")
        return []

    try:
        deadline = _monotonic() + _SEARCH_TIMEOUT
        # arxiv 4.0+ API: 使用 Client().results(Search)
        # Shared client plus lock preserves spacing across queries and pages.
        # delay_seconds 用 3.0：arXiv 官方限流是「同一 IP 每 3 秒最多 1 次请求」，低于 3s 反而更容易 429。
        # 代理：arxiv 客户端内部用 requests，自动读取 HTTPS_PROXY / HTTP_PROXY 环境变量。
        if not _wait_until(_arxiv_next_at):
            return []
        _arxiv_next_at = _monotonic() + 3.0
        if _arxiv_client is None:
            _arxiv_client = arxiv.Client(num_retries=0, delay_seconds=3.0, page_size=100)
            # The upstream client uses this requests.Session for each feed page.
            _arxiv_client._session.close()
            _arxiv_client._session = _TimedSession()
        client = _arxiv_client
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance
        )

        papers = []
        for result in client.results(search):
            if _monotonic() >= deadline:
                break
            papers.append({
                "source": "arxiv",
                "id": result.entry_id.split("/")[-1],  # 提取 ID
                "title": result.title,
                "authors": [author.name for author in result.authors],
                "abstract": result.summary,
                "published": result.published.strftime("%Y-%m-%d"),
                "pdf_url": result.pdf_url,
                "categories": result.categories,
            })

        logger.info(f"arXiv 搜索 '{query}': 找到 {len(papers)} 篇论文")
        return papers

    except Exception as e:
        logger.error(f"arXiv 搜索失败: {e}")
        return []
    finally:
        _arxiv_lock.release()


# ========== Semantic Scholar 搜索 ==========

def search_semantic_scholar(query: str, max_results: int = 10) -> List[Dict]:
    """搜索 Semantic Scholar 论文

    Args:
        query: 搜索关键词
        max_results: 最多返回论文数

    Returns:
        论文列表，每篇包含：
        {
            "source": "semantic_scholar",
            "id": "paper_id",
            "title": "...",
            "authors": ["Author1"],
            "abstract": "...",
            "year": 2023,
            "citation_count": 42,
            "influential_citation_count": 5,
            "venue": "NeurIPS",
            "pdf_url": "https://..."
        }
    """
    global _semantic_next_at
    if not _semantic_lock.acquire(blocking=False):
        logger.info("Semantic Scholar 已有请求运行，本次查询跳过")
        return []
    try:
        if not _wait_until(_semantic_next_at):
            logger.info("Semantic Scholar 处于限流冷却中，本次查询跳过")
            return []
        url = f"{SEMANTIC_SCHOLAR_API}/paper/search"
        params = {
            "query": query,
            "limit": max_results,
            "fields": "paperId,title,abstract,year,authors,citationCount,influentialCitationCount,venue,openAccessPdf"
        }

        headers = _semantic_headers()

        _semantic_next_at = _monotonic() + _semantic_interval()
        response = requests.get(url, params=params, headers=headers, timeout=(4, 10))
        if response.status_code == 429:
            cooldown = _retry_after_seconds(response.headers.get("Retry-After"))
            _semantic_next_at = max(_semantic_next_at, _monotonic() + cooldown)
            logger.warning("Semantic Scholar 返回429，已记录全局冷却；本次不立即重试")
            return []
        response.raise_for_status()
        data = response.json()

        papers = []
        for item in data.get("data", []):
            if not isinstance(item, dict) or not item.get("paperId") or not item.get("title"):
                continue
            # 提取作者名
            authors = [author.get("name", "Unknown") for author in item.get("authors", [])]

            # 提取 PDF URL
            pdf_info = item.get("openAccessPdf")
            pdf_url = pdf_info.get("url") if pdf_info else None

            papers.append({
                "source": "semantic_scholar",
                "id": item.get("paperId"),
                "title": item.get("title"),
                "authors": authors,
                "abstract": item.get("abstract"),
                "year": item.get("year"),
                "citation_count": item.get("citationCount", 0),
                "influential_citation_count": item.get("influentialCitationCount", 0),
                "venue": item.get("venue"),
                "pdf_url": pdf_url,
            })

        logger.info(f"Semantic Scholar 搜索 '{query}': 找到 {len(papers)} 篇论文")
        return papers

    except Exception as e:
        logger.error(f"Semantic Scholar 搜索失败: {e}")
        return []
    finally:
        _semantic_lock.release()


# ========== 聚合搜索 ==========

def _search_via_gateway(query: str, sources: List[str], max_per_source: int) -> Optional[List[Dict]]:
    """优先走网关服务端文献检索（/v1/literature/search）。

    成功返回论文列表；不适用（未走网关模式）或失败返回 None，交由调用方回退直连。
    """
    try:
        from .config import config
    except ImportError:
        from config import config

    gateway_url = (config.GATEWAY_BASE_URL or "").rstrip("/")
    base_url = (config.get_llm_base_url() or "").rstrip("/")
    # 只有用户走网关模式（Base URL 指向网关）时才调网关；own-key 直连上游时没有网关 key
    if not gateway_url or base_url != gateway_url:
        return None

    key = config.get_llm_api_key()
    if not key:
        return None

    url = f"{gateway_url}/literature/search"
    try:
        resp = requests.post(
            url,
            json={"query": query, "sources": sources, "max_per_source": max_per_source},
            headers={"Authorization": f"Bearer {key}"},
            timeout=(3, 6),
        )
        if resp.status_code != 200:
            logger.warning(f"网关文献检索返回 {resp.status_code}")
            return None
        papers = resp.json().get("papers")
        return papers if isinstance(papers, list) else None
    except Exception as e:
        logger.warning(f"网关文献检索失败，回退直连: {e}")
        return None


def search_literature(
    query: str,
    sources: List[str] = ["arxiv", "semantic_scholar"],
    max_per_source: int = 5
) -> List[Dict]:
    """聚合搜索多个学术数据源。

    优先走网关服务端（无需本地代理/key）；网关不可用或未配置时回退直连 arXiv/Semantic Scholar。
    全局工作线程有容量上限，调用方最多等待20秒；后台HTTP也有连接和读取超时。
    """
    if not _search_capacity.acquire(blocking=False):
        logger.info("文献检索繁忙，本次返回空结果，可稍后重试")
        return []
    deadline = _monotonic() + _SEARCH_TIMEOUT
    def _search():
        try:
            gateway_papers = _search_via_gateway(query, sources, max_per_source)
            if gateway_papers is not None:
                return _deduplicate_papers(gateway_papers)
            all_papers = []
            if "arxiv" in sources and _monotonic() < deadline:
                all_papers.extend(search_arxiv(query, max_per_source))
            if "semantic_scholar" in sources and _monotonic() < deadline:
                all_papers.extend(search_semantic_scholar(query, max_per_source))
            return _deduplicate_papers(all_papers)
        finally:
            _search_capacity.release()
    try:
        future = _search_pool.submit(_search)
    except RuntimeError:
        _search_capacity.release()
        return []
    try:
        papers = future.result(timeout=_SEARCH_TIMEOUT)
        logger.info(f"聚合搜索 '{query}': 总共 {len(papers)} 篇论文（去重后）")
        return papers
    except FutureTimeout:
        logger.warning("文献搜索超时（>%ss），返回空结果，可稍后重试", _SEARCH_TIMEOUT)
        return []
    except Exception as exc:
        logger.warning("文献搜索失败：%s", type(exc).__name__)
        return []


def _deduplicate_papers(papers: List[Dict]) -> List[Dict]:
    """基于标题去重（简单版：标题 hash）"""
    seen = set()
    unique = []

    for paper in papers:
        if not isinstance(paper, dict) or not paper.get("id") or not isinstance(paper.get("title"), str):
            continue
        title = paper["title"].lower().strip()
        if not title:
            continue
        title_hash = hashlib.md5(title.encode()).hexdigest()[:16]

        if title_hash not in seen:
            seen.add(title_hash)
            unique.append(paper)

    return unique


# ========== LLM 筛选相关论文 ==========

def filter_relevant_papers(
    papers: List[Dict],
    problem_description: str,
    llm_client,
    top_k: int = 3
) -> List[Dict]:
    """使用 LLM 从搜索结果中筛选最相关的论文

    Args:
        papers: 搜索到的论文列表
        problem_description: 当前问题描述
        llm_client: OpenAI 客户端
        top_k: 保留最相关的 K 篇

    Returns:
        筛选后的论文列表（按相关性排序）
    """
    if not papers:
        return []

    # 构建 prompt
    papers_text = "\n\n".join([
        f"[{i+1}] {p['title']}\n"
        f"Authors: {', '.join(p['authors'][:3])}\n"
        f"Abstract: {(p.get('abstract') or '')[:400]}..."
        for i, p in enumerate(papers)
    ])

    prompt = f"""You are reviewing academic papers for relevance to a mathematical modeling problem.

**Problem**: {problem_description[:500]}

**Papers found**:
{papers_text}

**Task**: Identify the TOP {top_k} most relevant papers. Consider:
1. Does the paper address similar problem types (e.g., facility location, queueing, scheduling)?
2. Does it use applicable mathematical methods (e.g., MILP, simulation, graph theory)?
3. Is it recent and well-cited (if citation data available)?

Output ONLY a JSON array of paper indices (1-indexed): [1, 3, 5]
No explanations."""

    try:
        response = llm_client.chat.completions.create(
            model="deepseek-v4-pro",  # DeepSeek最强模型
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=100,
        )

        result_text = response.choices[0].message.content.strip()

        # 解析 JSON
        import re
        match = re.search(r'\[[\d,\s]+\]', result_text)
        if match:
            indices = json.loads(match.group(0))
            selected = [papers[i-1] for i in indices if 1 <= i <= len(papers)]
            logger.info(f"LLM 筛选: {len(selected)}/{len(papers)} 篇论文相关")
            return selected
        else:
            logger.warning("LLM 返回格式错误，保留前K篇")
            return papers[:top_k]

    except Exception as e:
        logger.error(f"LLM 筛选失败: {e}")
        return papers[:top_k]


# ========== 数据库存储 ==========

def store_literature(conn, session_id: str, papers: List[Dict]) -> int:
    """将论文存入数据库（需要先创建表）

    Returns:
        插入的论文数量
    """
    # 创建表（如果不存在）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS literature_cache (
            session_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            authors TEXT,  -- JSON array
            abstract TEXT,
            published TEXT,
            citation_count INTEGER,
            pdf_url TEXT,
            metadata TEXT,  -- JSON: venue, categories, etc.
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, paper_id)
        )
    """)

    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    count = 0

    for paper in papers:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO literature_cache
                (session_id, paper_id, source, title, authors, abstract,
                 published, citation_count, pdf_url, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                paper["id"],
                paper["source"],
                paper["title"],
                json.dumps(paper["authors"]),
                paper.get("abstract"),
                paper.get("published") or str(paper.get("year", "")),
                paper.get("citation_count", 0),
                paper.get("pdf_url"),
                json.dumps({k: v for k, v in paper.items() if k not in [
                    "id", "source", "title", "authors", "abstract", "published", "citation_count", "pdf_url"
                ]}),
                now
            ))
            count += 1
        except Exception as e:
            logger.error(f"存储论文失败 {paper['title']}: {e}")

    conn.commit()
    logger.info(f"已存储 {count} 篇论文到数据库")
    return count


def load_literature(conn, session_id: str) -> List[Dict]:
    """从数据库加载论文"""
    rows = conn.execute("""
        SELECT * FROM literature_cache
        WHERE session_id = ?
        ORDER BY citation_count DESC
    """, (session_id,)).fetchall()

    papers = []
    for row in rows:
        papers.append({
            "id": row["paper_id"],
            "source": row["source"],
            "title": row["title"],
            "authors": json.loads(row["authors"]),
            "abstract": row["abstract"],
            "published": row["published"],
            "citation_count": row["citation_count"],
            "pdf_url": row["pdf_url"],
            **json.loads(row["metadata"])
        })

    return papers


# ========== 构建引用上下文 ==========

def build_literature_context(papers: List[Dict], max_chars: int = 2000) -> str:
    """将论文列表转换为 LLM 可用的上下文

    Returns:
        格式化的文献综述上下文
    """
    if not papers:
        return ""

    lines = ["## Relevant Literature\n"]

    for i, paper in enumerate(papers, 1):
        citation = _format_apa_citation(paper)
        abstract = (paper.get("abstract") or "")[:300]

        lines.append(f"**[{i}]** {citation}")
        lines.append(f"   Abstract: {abstract}...")
        lines.append("")

    context = "\n".join(lines)

    # 截断到最大长度
    if len(context) > max_chars:
        context = context[:max_chars] + "\n\n(... truncated)"

    return context


def _format_apa_citation(paper: Dict) -> str:
    """格式化 APA 引用"""
    authors = paper.get("authors", [])
    if len(authors) == 0:
        author_str = "Unknown"
    elif len(authors) == 1:
        author_str = authors[0]
    elif len(authors) == 2:
        author_str = f"{authors[0]} & {authors[1]}"
    else:
        author_str = f"{authors[0]} et al."

    year = paper.get("year") or paper.get("published", "")[:4] or "n.d."
    title = paper.get("title", "Untitled")

    venue = paper.get("venue") or paper.get("categories", [""])[0] if paper.get("categories") else ""

    citation = f"{author_str} ({year}). {title}."
    if venue:
        citation += f" {venue}."

    return citation


# ========== 演示 ==========

if __name__ == "__main__":
    import sys

    # 测试 arXiv
    print("=== 测试 arXiv 搜索 ===")
    arxiv_papers = search_arxiv("mathematical modeling emergency service", max_results=3)
    for p in arxiv_papers:
        print(f"- {p['title']}")
        print(f"  {p['authors'][0]} et al. ({p['published']})")
        print(f"  {p['pdf_url']}\n")

    # 测试 Semantic Scholar
    print("\n=== 测试 Semantic Scholar 搜索 ===")
    s2_papers = search_semantic_scholar("ambulance location optimization", max_results=3)
    for p in s2_papers:
        print(f"- {p['title']}")
        print(f"  Citations: {p['citation_count']} (influential: {p['influential_citation_count']})")
        print(f"  {p['venue']} ({p['year']})\n")

    # 聚合搜索
    print("\n=== 聚合搜索 ===")
    all_papers = search_literature("facility location queueing theory", max_per_source=2)
    print(f"找到 {len(all_papers)} 篇论文（去重后）")

    # 构建上下文
    context = build_literature_context(all_papers[:3])
    print("\n=== 文献上下文 ===")
    print(context[:500] + "...")

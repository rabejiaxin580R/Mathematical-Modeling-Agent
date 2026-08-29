"""文献检索（服务端）：arXiv + Semantic Scholar + 结果缓存。

由网关提供 /v1/literature/search 端点，让本地 app 无需自己配置代理 / API key：
  - 服务端挂干净 IP 或配置 SEMANTIC_SCHOLAR_API_KEY，突破匿名限流；
  - 结果按 query+sources 缓存 7 天，相同题目命中缓存后不再消耗上游额度。

搜索逻辑与 agent/backend/literature_search.py 保持一致（同样的返回字段），
但配置来源改为网关的 config（SEMANTIC_SCHOLAR_API_KEY），并叠加 SQLite 缓存。
"""
import logging
import json
import time
import hashlib
from typing import List, Dict

import requests

from .config import config
from . import db

logger = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"

# 搜索失败时的重试次数与退避基数（处理 429 限流 / 瞬时 SSL 抖动）
_SEARCH_RETRIES = 3
_RETRY_BASE_DELAY = 1.5  # 秒，指数退避：1.5s, 3s, 6s
# 结果缓存有效期（秒）。HiMCM 题目文献变化慢，7 天足够。
_CACHE_TTL = 7 * 24 * 3600


def _semantic_headers() -> Dict[str, str]:
    """Semantic Scholar 请求头；配置了 API key 则附带，未配置则匿名。"""
    api_key = config.SEMANTIC_SCHOLAR_API_KEY
    if api_key:
        return {"x-api-key": api_key}
    return {}


def _sleep_backoff(attempt: int) -> None:
    """指数退避等待；attempt 从 0 起算。"""
    time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))


# ========== arXiv 搜索 ==========

def search_arxiv(query: str, max_results: int = 10) -> List[Dict]:
    """搜索 arXiv 论文。返回字段与 agent 端一致：
    {source, id, title, authors, abstract, published, pdf_url, categories}
    """
    try:
        import arxiv
    except ImportError:
        logger.error("arxiv 库未安装，请运行: pip install arxiv")
        return []

    try:
        # num_retries/delay_seconds：对瞬时网络抖动（含 SSL EOF）自动重试。
        # delay_seconds 用 3.0：arXiv 官方限流是「同一 IP 每 3 秒最多 1 次请求」。
        client = arxiv.Client(num_retries=_SEARCH_RETRIES, delay_seconds=3.0)
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )

        papers = []
        for result in client.results(search):
            papers.append({
                "source": "arxiv",
                "id": result.entry_id.split("/")[-1],
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


# ========== Semantic Scholar 搜索 ==========

def search_semantic_scholar(query: str, max_results: int = 10) -> List[Dict]:
    """搜索 Semantic Scholar 论文。返回字段与 agent 端一致。"""
    try:
        url = f"{SEMANTIC_SCHOLAR_API}/paper/search"
        params = {
            "query": query,
            "limit": max_results,
            "fields": "paperId,title,abstract,year,authors,citationCount,influentialCitationCount,venue,openAccessPdf",
        }
        headers = _semantic_headers()

        last_err = None
        for attempt in range(_SEARCH_RETRIES):
            try:
                response = requests.get(url, params=params, headers=headers, timeout=15)
                if response.status_code == 429:
                    last_err = RuntimeError("429 Too Many Requests (Semantic Scholar 限流)")
                    if attempt < _SEARCH_RETRIES - 1:
                        logger.warning(f"Semantic Scholar 429，第 {attempt + 1} 次退避重试")
                        _sleep_backoff(attempt)
                        continue
                    raise last_err
                response.raise_for_status()
                data = response.json()
                break
            except Exception as e:
                last_err = e
                if attempt < _SEARCH_RETRIES - 1:
                    logger.warning(f"Semantic Scholar 搜索第 {attempt + 1} 次失败: {e}，重试")
                    _sleep_backoff(attempt)
                    continue
                raise

        papers = []
        for item in data.get("data", []):
            authors = [author.get("name", "Unknown") for author in item.get("authors", [])]
            pdf_info = item.get("openAccessPdf")
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
                "pdf_url": pdf_info.get("url") if pdf_info else None,
            })

        logger.info(f"Semantic Scholar 搜索 '{query}': 找到 {len(papers)} 篇论文")
        return papers

    except Exception as e:
        logger.error(f"Semantic Scholar 搜索失败: {e}")
        return []


def _deduplicate_papers(papers: List[Dict]) -> List[Dict]:
    """基于标题去重（标题 hash）。"""
    seen = set()
    unique = []
    for paper in papers:
        title = paper.get("title", "").lower().strip()
        title_hash = hashlib.md5(title.encode()).hexdigest()[:16]
        if title_hash not in seen:
            seen.add(title_hash)
            unique.append(paper)
    return unique


# ========== 结果缓存（SQLite） ==========

def _query_key(query: str, sources: List[str], max_per_source: int) -> str:
    raw = f"{query}|{','.join(sorted(sources))}|{max_per_source}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> List[Dict] | None:
    try:
        row = db.get_conn().execute(
            "SELECT papers, created_at FROM literature_cache WHERE key=?", (key,)
        ).fetchone()
    except Exception as e:
        logger.warning(f"读文献缓存失败: {e}")
        return None
    if row is None:
        return None
    if time.time() - row["created_at"] > _CACHE_TTL:
        return None
    try:
        return json.loads(row["papers"])
    except (json.JSONDecodeError, TypeError):
        return None


def _cache_put(key: str, papers: List[Dict]) -> None:
    try:
        with db.write_lock():
            conn = db.get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO literature_cache (key, papers, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(papers, ensure_ascii=False), time.time()),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"写文献缓存失败: {e}")


# ========== 聚合搜索（含缓存） ==========

def search_literature(
    query: str,
    sources: List[str] = ["arxiv", "semantic_scholar"],
    max_per_source: int = 5,
) -> List[Dict]:
    """聚合搜索多个学术数据源，结果缓存 7 天。"""
    key = _query_key(query, sources, max_per_source)
    cached = _cache_get(key)
    if cached is not None:
        logger.info(f"文献命中缓存 '{query}': {len(cached)} 篇")
        return cached

    all_papers: List[Dict] = []
    if "arxiv" in sources:
        all_papers.extend(search_arxiv(query, max_per_source))
    if "semantic_scholar" in sources:
        all_papers.extend(search_semantic_scholar(query, max_per_source))

    papers = _deduplicate_papers(all_papers)
    logger.info(f"聚合搜索 '{query}': {len(papers)} 篇（去重后）")

    if papers:
        _cache_put(key, papers)
    return papers

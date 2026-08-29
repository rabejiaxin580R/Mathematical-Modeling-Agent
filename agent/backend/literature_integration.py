"""将文献搜索集成到论文生成流程

在生成关键章节时自动搜索并引用相关文献：
1. Model Formulation - 搜索相关建模方法
2. Solution - 搜索求解算法
3. Model Evaluation - 搜索验证方法
"""
import logging
from typing import Dict, List, Optional
from openai import OpenAI

from .literature_search import (
    search_literature,
    filter_relevant_papers,
    store_literature,
    load_literature,
    build_literature_context,
)
from .config import config

logger = logging.getLogger(__name__)


# ========== 自动文献检索触发器 ==========

def should_search_literature(section_key: str) -> bool:
    """判断某章节是否需要搜索文献

    Returns:
        True 如果该章节应该引用文献
    """
    # 核心理论章节需要文献支持
    literature_sections = {
        "build",       # Model Formulation - 搜索建模方法
        "solve",       # Solution - 搜索求解算法
        "evaluate",    # Evaluation - 搜索验证方法
    }
    return section_key in literature_sections


def generate_search_query(
    section_key: str,
    problem_md: str,
    conversation_text: str
) -> Optional[str]:
    """根据章节和问题内容生成文献搜索关键词

    Args:
        section_key: 章节标识
        problem_md: 问题描述
        conversation_text: 做题对话记录

    Returns:
        搜索关键词字符串，如 "facility location queueing theory optimization"
    """
    # 从问题描述中提取关键词（简化版）
    problem_lower = problem_md.lower()

    # 识别问题类型关键词
    keywords = []

    # 建模方法关键词
    modeling_keywords = {
        "optimization", "linear programming", "integer programming",
        "queueing", "simulation", "graph theory", "network flow",
        "scheduling", "routing", "allocation", "location",
        "regression", "classification", "forecasting"
    }

    # 应用领域关键词
    domain_keywords = {
        "ambulance", "emergency", "facility", "transportation",
        "supply chain", "inventory", "production", "healthcare",
        "energy", "finance", "epidemic", "climate"
    }

    # 章节特定关键词
    section_keywords = {
        "build": ["mathematical modeling", "formulation", "model"],
        "solve": ["algorithm", "solution method", "solver", "heuristic"],
        "evaluate": ["validation", "sensitivity analysis", "robustness"],
    }

    # 提取关键词
    for kw in modeling_keywords:
        if kw in problem_lower:
            keywords.append(kw)

    for kw in domain_keywords:
        if kw in problem_lower:
            keywords.append(kw)

    # 添加章节特定关键词
    if section_key in section_keywords:
        keywords.extend(section_keywords[section_key][:1])

    # 如果关键词太少，使用 LLM 提取
    if len(keywords) < 2:
        keywords = _extract_keywords_with_llm(problem_md, section_key)

    query = " ".join(keywords[:5])  # 最多5个关键词
    logger.info(f"为 {section_key} 章节生成搜索关键词: {query}")
    return query if query.strip() else None


def _extract_keywords_with_llm(problem_md: str, section_key: str) -> List[str]:
    """使用 LLM 从问题描述中提取搜索关键词"""
    try:
        client = OpenAI(api_key=config.get_llm_api_key(), base_url=config.get_llm_base_url())

        prompt = f"""Extract 3-5 academic search keywords from this problem for literature search.

Problem:
{problem_md[:800]}

Focus on: {section_key} (mathematical modeling methods)

Output ONLY keywords separated by commas, like: "queueing theory, facility location, MILP optimization"
"""

        response = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=50,
        )

        keywords_str = response.choices[0].message.content.strip()
        keywords = [kw.strip() for kw in keywords_str.split(",")]
        return keywords[:5]

    except Exception as e:
        logger.error(f"LLM 提取关键词失败: {e}")
        return ["mathematical modeling", "optimization"]


# ========== 集成到 generate_section() ==========

def augment_section_with_literature(
    conn,
    session_id: str,
    section_key: str,
    problem_md: str,
    conversation_text: str,
    force_refresh: bool = False
) -> str:
    """为章节生成搜索文献并返回上下文

    Args:
        conn: 数据库连接
        session_id: 论文会话 ID
        section_key: 章节标识
        problem_md: 问题描述
        conversation_text: 做题对话
        force_refresh: 是否强制重新搜索（否则使用缓存）

    Returns:
        文献上下文字符串（供 LLM prompt 使用）
    """
    # 1. 检查是否需要搜索
    if not should_search_literature(section_key):
        return ""

    # 2. 尝试从缓存加载
    if not force_refresh:
        try:
            cached_papers = load_literature(conn, session_id)
            if cached_papers:
                logger.info(f"使用缓存的 {len(cached_papers)} 篇文献")
                return build_literature_context(cached_papers[:5])
        except Exception as e:
            logger.warning(f"加载文献缓存失败: {e}")

    # 3. 生成搜索关键词
    query = generate_search_query(section_key, problem_md, conversation_text)
    if not query:
        logger.warning(f"无法为 {section_key} 生成搜索关键词")
        return ""

    # 4. 搜索文献
    logger.info(f"搜索文献: {query}")
    papers = search_literature(
        query=query,
        sources=["arxiv", "semantic_scholar"],
        max_per_source=5
    )

    if not papers:
        logger.warning(f"未找到相关文献: {query}")
        return ""

    # 5. LLM 筛选相关论文
    client = OpenAI(api_key=config.get_llm_api_key(), base_url=config.get_llm_base_url())
    relevant_papers = filter_relevant_papers(
        papers=papers,
        problem_description=problem_md,
        llm_client=client,
        top_k=3
    )

    # 6. 存入数据库
    store_literature(conn, session_id, relevant_papers)

    # 7. 构建上下文
    context = build_literature_context(relevant_papers)
    logger.info(f"为 {section_key} 准备了 {len(relevant_papers)} 篇文献")

    return context


# ========== 文献综述章节生成 ==========

def generate_literature_review_section(
    conn,
    session_id: str,
    problem_md: str
) -> Optional[str]:
    """生成独立的文献综述章节（可选功能）

    Returns:
        Markdown 格式的文献综述，或 None 如果无文献
    """
    papers = load_literature(conn, session_id)
    if not papers:
        return None

    # 按引用数排序
    papers_sorted = sorted(papers, key=lambda p: p.get("citation_count", 0), reverse=True)

    lines = ["## Literature Review\n"]
    lines.append("This section reviews relevant prior work that informs our modeling approach.\n")

    # 分组：建模方法、求解算法、应用案例
    lines.append("### Modeling Approaches\n")
    for i, paper in enumerate(papers_sorted[:3], 1):
        citation = f"{paper['authors'][0]} et al. ({paper.get('year', 'n.d.')})"
        lines.append(f"{i}. **{paper['title']}** ({citation})")
        lines.append(f"   - {(paper.get('abstract') or '')[:200]}...")
        lines.append("")

    lines.append("### Solution Methods\n")
    lines.append("(Additional papers on algorithms and solvers)\n")

    return "\n".join(lines)


# ========== 引用格式化 ==========

def format_citations_for_paper(papers: List[Dict]) -> str:
    """生成 APA 格式的参考文献列表

    Returns:
        Markdown 格式的 References 章节
    """
    if not papers:
        return ""

    lines = ["# References\n"]

    # 按第一作者姓氏排序
    papers_sorted = sorted(papers, key=lambda p: p.get("authors", ["Unknown"])[0])

    for paper in papers_sorted:
        authors = paper.get("authors", [])
        if len(authors) == 0:
            author_str = "Unknown"
        elif len(authors) == 1:
            author_str = authors[0]
        elif len(authors) == 2:
            author_str = f"{authors[0]}, & {authors[1]}"
        else:
            # APA 风格：前6位作者
            if len(authors) <= 6:
                author_str = ", ".join(authors[:-1]) + f", & {authors[-1]}"
            else:
                author_str = ", ".join(authors[:6]) + ", ... " + authors[-1]

        year = paper.get("year") or paper.get("published", "")[:4] or "n.d."
        title = paper.get("title", "Untitled")
        venue = paper.get("venue") or "arXiv"

        citation = f"- {author_str} ({year}). *{title}*. {venue}."

        # 添加 DOI 或 URL
        if paper.get("pdf_url"):
            citation += f" Retrieved from {paper['pdf_url']}"

        lines.append(citation)

    return "\n".join(lines)


# ========== 演示：完整工作流 ==========

if __name__ == "__main__":
    import sqlite3
    from pathlib import Path

    # 创建测试数据库
    test_db = Path("test_literature.db")
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row

    # 测试问题
    problem = """
    An emergency medical service (EMS) system must determine optimal locations for ambulance stations
    to serve a city divided into 20 districts. Each station has a fixed cost of $100,000 per year,
    and each ambulance costs $50,000 per year to operate. The goal is to minimize total cost while
    ensuring that 95% of calls are reached within 8 minutes.
    """

    session_id = "ps_test123"

    print("=== 测试文献搜索集成 ===\n")

    # 1. 为 Model Formulation 章节搜索文献
    print("1. 搜索 Model Formulation 相关文献...")
    context = augment_section_with_literature(
        conn=conn,
        session_id=session_id,
        section_key="build",
        problem_md=problem,
        conversation_text="",
        force_refresh=True
    )

    print(f"\n生成的文献上下文（前500字符）：\n{context[:500]}...\n")

    # 2. 加载缓存的文献
    papers = load_literature(conn, session_id)
    print(f"2. 数据库中缓存了 {len(papers)} 篇文献\n")

    # 3. 生成引用列表
    refs = format_citations_for_paper(papers)
    print(f"3. 生成的参考文献列表：\n{refs[:300]}...\n")

    # 清理
    conn.close()
    test_db.unlink()

    print("✅ 测试完成")

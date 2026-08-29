"""PaSa 集成包装器

直接使用字节跳动 PaSa Agent 进行学术文献搜索
无需重新造轮子，直接调用成熟的开源方案

前置条件：
    1. git clone https://github.com/bytedance/pasa.git 到 agent/backend/
    2. pip install -r pasa/requirements.txt
    3. 配置 LLM API (OpenAI 兼容接口)

使用示例：
    from pasa_integration import search_with_pasa

    papers = search_with_pasa(
        query="facility location optimization queueing theory",
        max_results=10
    )
"""
import sys
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# 添加 PaSa 到 Python 路径
PASA_DIR = Path(__file__).parent / "pasa"
if PASA_DIR.exists():
    sys.path.insert(0, str(PASA_DIR))
    logger.info(f"PaSa 路径已添加: {PASA_DIR}")
else:
    logger.warning(f"PaSa 未找到！请先克隆: git clone https://github.com/bytedance/pasa.git")


def check_pasa_available() -> bool:
    """检查 PaSa 是否可用"""
    try:
        # 尝试导入 PaSa 的核心模块
        # 注意：具体模块名需要查看 PaSa 的实际代码结构
        import pasa  # 假设的导入，需要根据实际调整
        return True
    except ImportError as e:
        logger.error(f"PaSa 不可用: {e}")
        return False


def search_with_pasa(
    query: str,
    max_results: int = 10,
    config: Optional[Dict] = None
) -> List[Dict]:
    """使用 PaSa Agent 搜索学术文献

    Args:
        query: 学术查询（如 "queueing theory ambulance location"）
        max_results: 最多返回论文数
        config: PaSa 配置（LLM API、搜索工具等）

    Returns:
        论文列表，每篇包含：
        {
            "title": "论文标题",
            "authors": ["作者1", "作者2"],
            "abstract": "摘要",
            "year": 2024,
            "venue": "会议/期刊",
            "citations": 42,
            "pdf_url": "https://...",
            "relevance_score": 0.95  # PaSa 的相关性评分
        }
    """
    if not check_pasa_available():
        logger.error("PaSa 不可用，回退到基础搜索")
        return _fallback_search(query, max_results)

    try:
        # 这里需要根据 PaSa 的实际 API 调整
        # 以下是假设的调用方式
        from pasa.agent import PaSaAgent
        from pasa.config import load_config

        # 加载配置
        if config is None:
            config = load_config()  # 从 PaSa 的默认配置加载

        # 初始化 Agent
        agent = PaSaAgent(config)

        # 执行搜索
        results = agent.search(
            query=query,
            max_papers=max_results,
            expand_citations=True,  # 自动扩展引用链
            filter_strategy="relevance"  # 按相关性排序
        )

        # 转换为统一格式
        papers = []
        for result in results:
            papers.append({
                "title": result.get("title"),
                "authors": result.get("authors", []),
                "abstract": result.get("abstract"),
                "year": result.get("year"),
                "venue": result.get("venue"),
                "citations": result.get("citation_count", 0),
                "pdf_url": result.get("pdf_url"),
                "relevance_score": result.get("score", 0.0),
                "source": "pasa",
            })

        logger.info(f"PaSa 搜索 '{query}': 找到 {len(papers)} 篇论文")
        return papers

    except Exception as e:
        logger.error(f"PaSa 搜索失败: {e}", exc_info=True)
        return _fallback_search(query, max_results)


def _fallback_search(query: str, max_results: int) -> List[Dict]:
    """回退方案：使用基础搜索"""
    logger.warning("使用回退搜索方案")
    try:
        from literature_search import search_literature
        return search_literature(query, max_per_source=max_results // 2)
    except ImportError:
        logger.error("回退搜索也不可用")
        return []


# ========== 集成到论文生成流程 ==========

def augment_section_with_pasa(
    conn,
    session_id: str,
    section_key: str,
    problem_md: str,
    conversation_text: str,
    force_refresh: bool = False
) -> str:
    """使用 PaSa 为章节搜索文献（替代 literature_integration.py）

    Args:
        conn: 数据库连接
        session_id: 论文会话 ID
        section_key: 章节标识
        problem_md: 问题描述
        conversation_text: 做题对话
        force_refresh: 是否强制重新搜索

    Returns:
        文献上下文字符串（供 LLM prompt 使用）
    """
    from literature_integration import (
        should_search_literature,
        generate_search_query
    )
    from literature_search import (
        store_literature,
        load_literature,
        build_literature_context
    )

    # 1. 检查是否需要搜索
    if not should_search_literature(section_key):
        return ""

    # 2. 尝试从缓存加载
    if not force_refresh:
        cached = load_literature(conn, session_id)
        if cached:
            logger.info(f"使用缓存的 {len(cached)} 篇文献")
            return build_literature_context(cached[:5])

    # 3. 生成搜索关键词
    query = generate_search_query(section_key, problem_md, conversation_text)
    if not query:
        logger.warning(f"无法为 {section_key} 生成搜索关键词")
        return ""

    # 4. 使用 PaSa 搜索
    logger.info(f"使用 PaSa 搜索文献: {query}")
    papers = search_with_pasa(query, max_results=10)

    if not papers:
        logger.warning(f"PaSa 未找到相关文献: {query}")
        return ""

    # PaSa 已经做了相关性筛选和排序，直接取前3篇
    top_papers = papers[:3]

    # 5. 存入数据库
    store_literature(conn, session_id, top_papers)

    # 6. 构建上下文
    context = build_literature_context(top_papers)
    logger.info(f"为 {section_key} 准备了 {len(top_papers)} 篇文献（PaSa 筛选）")

    return context


# ========== 演示 ==========

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    print("="*70)
    print("PaSa 集成测试")
    print("="*70)

    # 测试 PaSa 可用性
    print("\n1. 检查 PaSa 是否可用...")
    available = check_pasa_available()
    if available:
        print("✅ PaSa 可用")
    else:
        print("❌ PaSa 不可用")
        print("   请先安装:")
        print("   cd agent/backend")
        print("   git clone https://github.com/bytedance/pasa.git")
        print("   cd pasa && pip install -r requirements.txt")
        exit(1)

    # 测试搜索
    print("\n2. 测试 PaSa 搜索...")
    query = "facility location optimization queueing theory"
    print(f"   查询: {query}")

    papers = search_with_pasa(query, max_results=5)

    if papers:
        print(f"\n✅ 找到 {len(papers)} 篇论文：\n")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper['title']}")
            print(f"   相关性: {paper.get('relevance_score', 0):.2f}")
            print(f"   引用数: {paper['citations']}")
            print()
    else:
        print("❌ 未找到论文")

    print("\n✅ 测试完成")

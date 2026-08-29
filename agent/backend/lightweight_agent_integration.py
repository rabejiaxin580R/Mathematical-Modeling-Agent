"""轻量级 Agent 集成到论文生成

使用轻量级 Agent 替代基础搜索，提供更智能的文献检索
"""
import logging
from typing import Optional
from openai import OpenAI

from .lightweight_agent import lightweight_pasa_search
from .config import config
from .literature_search import store_literature, load_literature, build_literature_context

logger = logging.getLogger(__name__)


def augment_section_with_lightweight_agent(
    conn,
    session_id: str,
    section_key: str,
    problem_md: str,
    conversation_text: str,
    force_refresh: bool = False
) -> str:
    """使用轻量级 Agent 为章节搜索文献

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

    # 4. 使用轻量级 Agent 搜索
    logger.info(f"🤖 使用轻量级 Agent 搜索文献: {query}")

    try:
        client = OpenAI(
            api_key=config.get_llm_api_key(),
            base_url=config.get_llm_base_url()
        )

        papers = lightweight_pasa_search(
            query=query,
            llm_client=client,
            max_rounds=3,  # 最多3轮搜索
            top_k=5        # 返回5篇最相关
        )

        if not papers:
            logger.warning(f"轻量级 Agent 未找到相关文献: {query}")
            return ""

        # 5. 存入数据库
        store_literature(conn, session_id, papers)

        # 6. 构建上下文
        context = build_literature_context(papers)
        logger.info(f"为 {section_key} 准备了 {len(papers)} 篇文献（轻量级 Agent 筛选）")

        return context

    except Exception as e:
        logger.error(f"轻量级 Agent 搜索失败: {e}", exc_info=True)
        # 回退到基础搜索
        logger.warning("回退到基础搜索方案")
        from literature_integration import augment_section_with_literature
        return augment_section_with_literature(
            conn, session_id, section_key, problem_md, conversation_text, force_refresh
        )


# ========== 演示：完整工作流 ==========

if __name__ == "__main__":
    import sqlite3
    from pathlib import Path

    print("="*70)
    print("🤖 轻量级 Agent 集成测试")
    print("="*70)

    # 创建测试数据库
    test_db = Path("test_lightweight_agent.db")
    if test_db.exists():
        test_db.unlink()

    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row

    # 测试问题
    problem = """
    An emergency medical service (EMS) system must determine optimal locations
    for ambulance stations to minimize response time while staying within budget.
    The city has 20 districts with varying population densities.
    """

    session_id = "test_lightweight_session"

    print("\n1. 使用轻量级 Agent 搜索文献...\n")

    context = augment_section_with_lightweight_agent(
        conn=conn,
        session_id=session_id,
        section_key="build",
        problem_md=problem,
        conversation_text="",
        force_refresh=True
    )

    if context:
        print("\n✅ 生成的文献上下文（前500字符）：")
        print("-"*70)
        print(context[:500] + "...")
        print("-"*70)

        # 测试缓存
        print("\n2. 测试缓存机制（不重新搜索）...\n")
        context2 = augment_section_with_lightweight_agent(
            conn=conn,
            session_id=session_id,
            section_key="build",
            problem_md=problem,
            conversation_text="",
            force_refresh=False  # 使用缓存
        )

        if len(context2) > 0:
            print("✅ 缓存机制正常工作")
        else:
            print("❌ 缓存机制失败")
    else:
        print("❌ 未找到相关文献")

    # 清理
    conn.close()
    test_db.unlink()

    print("\n✅ 测试完成！")

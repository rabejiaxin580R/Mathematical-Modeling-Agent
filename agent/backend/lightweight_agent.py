"""轻量级论文搜索 Agent - 模拟 PaSa 核心逻辑

不需要下载大模型，使用现有 LLM API 实现：
1. Crawler Agent - 多轮搜索决策 + 引用扩展
2. Selector Agent - 智能筛选最相关论文

模型：DeepSeek-V4-Pro (1M上下文，384K输出)
"""
import logging
import json
from typing import List, Dict, Optional, Tuple
from openai import OpenAI

logger = logging.getLogger(__name__)


# ========== Crawler Agent（搜索与扩展）==========

class CrawlerAgent:
    """模拟 PaSa Crawler - 多轮搜索决策 + 引用扩展"""

    def __init__(self, llm_client: OpenAI):
        self.llm = llm_client
        self.paper_queue = []  # 收集到的论文队列
        self.visited = set()   # 已访问的论文 ID

    def search(
        self,
        query: str,
        max_rounds: int = 3,
        max_papers: int = 50
    ) -> List[Dict]:
        """多轮搜索，模拟 PaSa Crawler 的自主决策"""
        logger.info(f"🤖 Crawler Agent 开始搜索: {query}")

        # Round 1: 初始搜索
        logger.info("📡 Round 1: 初始搜索...")
        initial_papers = self._initial_search(query)
        self._add_to_queue(initial_papers)

        # Round 2-N: 多轮决策
        for round_num in range(2, max_rounds + 1):
            if len(self.paper_queue) >= max_papers:
                logger.info(f"✅ 已收集 {len(self.paper_queue)} 篇论文，达到上限")
                break

            logger.info(f"🤔 Round {round_num}: Agent 决策中...")
            decision = self._make_decision(query, round_num)

            if decision["action"] == "stop":
                logger.info(f"🛑 Agent 决定停止搜索: {decision['reason']}")
                break

            elif decision["action"] == "expand_citations":
                logger.info(f"🔗 扩展引用: {decision['paper_title'][:50]}...")
                citations = self._expand_citations(decision["paper_id"])
                self._add_to_queue(citations)

            elif decision["action"] == "search_more":
                logger.info(f"🔍 继续搜索: {decision['refined_query']}")
                more_papers = self._refined_search(decision["refined_query"])
                self._add_to_queue(more_papers)

        logger.info(f"✅ Crawler 完成，收集了 {len(self.paper_queue)} 篇论文")
        return self.paper_queue

    def _initial_search(self, query: str) -> List[Dict]:
        """初始搜索"""
        from literature_search import search_literature

        papers = search_literature(
            query=query,
            sources=["arxiv", "semantic_scholar"],
            max_per_source=5
        )

        logger.info(f"  找到 {len(papers)} 篇初始论文")
        return papers

    def _make_decision(self, query: str, round_num: int) -> Dict:
        """LLM 决定下一步行动"""

        # 构建 prompt
        recent_papers = self.paper_queue[-5:]  # 最近收集的 5 篇
        papers_summary = "\n".join([
            f"- [{i+1}] {p['title'][:60]}... (引用: {p.get('citation_count', 0)})"
            for i, p in enumerate(recent_papers)
        ])

        prompt = f"""You are a paper search agent. Based on the current search progress, decide the next action.

**User Query**: {query}

**Current Progress**:
- Round: {round_num}
- Collected papers: {len(self.paper_queue)}

**Recent papers**:
{papers_summary}

**Available Actions**:
1. "stop" - Stop searching (if already found enough relevant papers)
2. "expand_citations" - Expand citations of the most relevant paper found
3. "search_more" - Continue searching with a refined query

**Decision Format** (output ONLY valid JSON):
{{
    "action": "stop" | "expand_citations" | "search_more",
    "reason": "brief explanation",
    "paper_id": "paper ID if action is expand_citations",
    "paper_title": "paper title if action is expand_citations",
    "refined_query": "new query if action is search_more"
}}

Think step by step:
1. Are the recent papers highly relevant? (citation count > 50 is good signal)
2. Have we found a seminal paper worth expanding?
3. Or should we continue searching with different keywords?

Output your decision in JSON:"""

        try:
            response = self.llm.chat.completions.create(
                model="deepseek-v4-pro",  # DeepSeek V4 Pro
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )

            result_text = response.choices[0].message.content.strip()

            # 提取 JSON
            import re
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group(0))
                logger.info(f"  决策: {decision['action']} - {decision['reason']}")
                return decision
            else:
                logger.warning("  LLM 返回格式错误，默认停止")
                return {"action": "stop", "reason": "LLM response error"}

        except Exception as e:
            logger.error(f"  决策失败: {e}")
            return {"action": "stop", "reason": f"Error: {e}"}

    def _expand_citations(self, paper_id: str) -> List[Dict]:
        """扩展论文的引用链"""
        if not paper_id:
            return []

        try:
            import requests
            import time

            # 避免速率限制，添加延迟
            time.sleep(1)

            # 使用 Semantic Scholar API 获取引用
            url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}/citations"
            params = {
                "fields": "paperId,title,abstract,year,authors,citationCount,venue,openAccessPdf",
                "limit": 10
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            citations = []
            for item in data.get("data", []):
                cited_paper = item.get("citedPaper", {})
                if not cited_paper:
                    continue

                # 转换为统一格式
                authors = [a.get("name", "Unknown") for a in cited_paper.get("authors", [])]
                pdf_info = cited_paper.get("openAccessPdf")

                citations.append({
                    "source": "semantic_scholar",
                    "id": cited_paper.get("paperId"),
                    "title": cited_paper.get("title"),
                    "authors": authors,
                    "abstract": cited_paper.get("abstract"),
                    "year": cited_paper.get("year"),
                    "citation_count": cited_paper.get("citationCount", 0),
                    "venue": cited_paper.get("venue"),
                    "pdf_url": pdf_info.get("url") if pdf_info else None,
                })

            logger.info(f"  扩展了 {len(citations)} 篇引用论文")
            return citations

        except Exception as e:
            logger.error(f"  扩展引用失败: {e}")
            return []

    def _refined_search(self, refined_query: str) -> List[Dict]:
        """使用优化后的查询继续搜索"""
        from literature_search import search_literature

        papers = search_literature(
            query=refined_query,
            sources=["arxiv", "semantic_scholar"],
            max_per_source=3
        )

        logger.info(f"  精炼搜索找到 {len(papers)} 篇论文")
        return papers

    def _add_to_queue(self, papers: List[Dict]):
        """添加论文到队列（去重）"""
        added = 0
        for paper in papers:
            paper_id = paper.get("id", "")
            if paper_id and paper_id not in self.visited:
                self.paper_queue.append(paper)
                self.visited.add(paper_id)
                added += 1

        if added > 0:
            logger.info(f"  添加了 {added} 篇新论文到队列")


# ========== Selector Agent（筛选）==========

class SelectorAgent:
    """模拟 PaSa Selector - 智能筛选最相关论文"""

    def __init__(self, llm_client: OpenAI):
        self.llm = llm_client

    def select(
        self,
        query: str,
        papers: List[Dict],
        top_k: int = 5
    ) -> List[Dict]:
        """从论文队列中筛选最相关的 Top-K"""
        logger.info(f"🎯 Selector Agent 开始筛选: {len(papers)} 篇 → Top {top_k}")

        if len(papers) <= top_k:
            logger.info("  论文数量少于 top_k，全部返回")
            return papers

        # 分批评估（避免 prompt 过长）
        batch_size = 10
        scored_papers = []

        for i in range(0, len(papers), batch_size):
            batch = papers[i:i + batch_size]
            scores = self._score_batch(query, batch)

            for paper, score in zip(batch, scores):
                paper_with_score = paper.copy()
                paper_with_score["relevance_score"] = score
                scored_papers.append(paper_with_score)

        # 按得分排序
        scored_papers.sort(key=lambda p: p["relevance_score"], reverse=True)

        top_papers = scored_papers[:top_k]
        logger.info(f"✅ Selector 完成，返回 Top {len(top_papers)} 篇论文")

        return top_papers

    def _score_batch(self, query: str, papers: List[Dict]) -> List[float]:
        """批量评估论文相关性"""

        # 构建 prompt
        papers_text = []
        for i, paper in enumerate(papers):
            abstract = (paper.get("abstract") or "")[:300]
            papers_text.append(
                f"[{i+1}] Title: {paper['title']}\n"
                f"    Authors: {', '.join(paper['authors'][:2])}\n"
                f"    Year: {paper.get('year', 'N/A')}\n"
                f"    Citations: {paper.get('citation_count', 0)}\n"
                f"    Abstract: {abstract}..."
            )

        prompt = f"""You are evaluating academic papers for relevance to a research query.

**Query**: {query}

**Papers to evaluate**:
{chr(10).join(papers_text)}

**Task**: Rate each paper's relevance on a scale of 0.0 to 1.0, where:
- 0.0-0.3: Not relevant (different problem domain or method)
- 0.4-0.6: Somewhat relevant (related topic but different focus)
- 0.7-0.9: Highly relevant (same problem type or applicable method)
- 0.9-1.0: Perfect match (exactly what the query asks for)

Consider:
1. Does the paper address the same problem type?
2. Are the methods applicable?
3. Is it a seminal work (high citations)?
4. Is it recent enough to be relevant?

Output ONLY a JSON array of scores: [0.85, 0.62, 0.91, ...]
One score per paper, in the same order."""

        try:
            response = self.llm.chat.completions.create(
                model="deepseek-v4-pro",  # DeepSeek V4 Pro
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100,
            )

            result_text = response.choices[0].message.content.strip()

            # 提取 JSON 数组
            import re
            json_match = re.search(r'\[[\d.,\s]+\]', result_text)
            if json_match:
                scores = json.loads(json_match.group(0))
                logger.info(f"  评估了 {len(scores)} 篇论文")
                return scores
            else:
                logger.warning("  LLM 返回格式错误，使用默认得分")
                return [0.5] * len(papers)

        except Exception as e:
            logger.error(f"  评估失败: {e}")
            # 回退：按引用数排序
            return [float(p.get("citation_count", 0)) / 1000 for p in papers]


# ========== 主入口：轻量级 PaSa ==========

def lightweight_pasa_search(
    query: str,
    llm_client: OpenAI,
    max_rounds: int = 3,
    top_k: int = 5
) -> List[Dict]:
    """轻量级 PaSa - 完整搜索流程"""
    logger.info("="*60)
    logger.info("🚀 轻量级 PaSa 启动")
    logger.info("="*60)

    # Stage 1: Crawler Agent 收集论文
    crawler = CrawlerAgent(llm_client)
    all_papers = crawler.search(query, max_rounds=max_rounds)

    if not all_papers:
        logger.warning("❌ Crawler 未找到任何论文")
        return []

    # Stage 2: Selector Agent 筛选最相关
    selector = SelectorAgent(llm_client)
    selected_papers = selector.select(query, all_papers, top_k=top_k)

    logger.info("="*60)
    logger.info(f"✅ 轻量级 PaSa 完成：返回 {len(selected_papers)} 篇论文")
    logger.info("="*60)

    return selected_papers


# ========== 演示 ==========

if __name__ == "__main__":
    import logging
    from config import config

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )

    print("\n" + "="*70)
    print("🤖 轻量级论文搜索 Agent 演示")
    print("="*70)

    # 初始化 LLM 客户端
    client = OpenAI(
        api_key=config.get_llm_api_key(),
        base_url=config.get_llm_base_url()
    )

    # 测试查询
    query = "facility location optimization queueing theory ambulance"
    print(f"\n📝 查询: {query}\n")

    # 运行轻量级 PaSa
    papers = lightweight_pasa_search(
        query=query,
        llm_client=client,
        max_rounds=2,  # 只运行2轮（演示用）
        top_k=5
    )

    # 显示结果
    print("\n" + "="*70)
    print("📊 搜索结果")
    print("="*70)

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper['title']}")
        print(f"   👤 {', '.join(paper['authors'][:2])}")
        print(f"   📅 {paper.get('year', 'N/A')}")
        print(f"   📊 引用: {paper.get('citation_count', 0)}")
        print(f"   🎯 相关性: {paper.get('relevance_score', 0):.2f}")
        if paper.get('pdf_url'):
            print(f"   🔗 {paper['pdf_url']}")

    print("\n✅ 演示完成！")

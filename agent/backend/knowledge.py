"""知识库加载与检索。

从 json_outputs 加载所有结构化知识单元，使用 jieba 分词 + BM25 做本地检索，
无需额外 API 成本。检索接口预留，后续可替换为向量检索。
"""
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path

import jieba

from .config import config

logger = logging.getLogger(__name__)


class KnowledgeUnit:
    """单个知识单元，对应一个 JSON chunk。"""

    def __init__(self, raw: dict, source_file: str):
        self.chunk_id: str = raw.get("chunk_id", "unknown")
        self.title: str = raw.get("title", "未命名")
        self.category: str = raw.get("category", "未分类")
        self.difficulty: str = raw.get("difficulty", "")
        self.prerequisites: list = raw.get("prerequisites", []) or []
        self.summary: dict = raw.get("detailed_summary", {}) or {}
        self.keywords: list = raw.get("keywords", []) or []
        self.formulas: list = raw.get("formulas", []) or []
        self.source_excerpt: str = raw.get("source_excerpt", "")
        self.source_file = source_file
        # 新 schema_version=3 的分层字段（旧讲义回退时保持空值，不影响检索/LLM 上下文）。
        # 前端全屏「学习卡片」直接消费这些原始字段以保留学习梯度；node_detail 透传。
        self.explain: dict = {}
        self.roles: list = []
        self.tools: str = ""
        self.cases: list = []
        self.taxonomy_path: list = []
        self.priority: str = ""
        self.module_id: str = ""
        self.subcat_id: str = ""
        self._search_text = self._build_search_text()

    def _build_search_text(self) -> str:
        """拼接用于检索的全文。

        标题与关键词重复多次拼入，等价于字段加权，提升其在 BM25 中的命中权重。
        """
        parts = [self.title, self.title, self.category]
        kw = " ".join(self.keywords)
        parts.extend([kw, kw, kw])  # 关键词加权
        s = self.summary
        parts.append(s.get("definition", ""))
        parts.append(s.get("math_principle", ""))
        parts.append(s.get("teaching_examples", ""))
        parts.extend(s.get("application_scenarios", []) or [])
        parts.extend(s.get("key_caveats", []) or [])
        parts.extend(s.get("step_by_step", []) or [])
        parts.extend(self.prerequisites)
        for f in self.formulas:
            parts.append(f.get("latex_code", ""))
        parts.append(self.source_excerpt)
        return " ".join(p for p in parts if p)

    def to_context(self) -> str:
        """格式化为喂给大模型的上下文片段（带出处标记）。"""
        s = self.summary
        lines = [
            f"【知识点 {self.chunk_id}｜{self.title}｜分类：{self.category}】",
        ]
        meta = []
        if self.difficulty:
            meta.append(f"难度：{self.difficulty}")
        if self.prerequisites:
            meta.append(f"前置知识：{'、'.join(self.prerequisites)}")
        if meta:
            lines.append("｜".join(meta))
        if s.get("definition"):
            lines.append(f"定义：{s['definition']}")
        if s.get("math_principle"):
            lines.append(f"数学原理：{s['math_principle']}")
        if self.formulas:
            for f in self.formulas:
                latex = f.get("latex_code", "")
                if latex:
                    lines.append(f"公式：$$ {latex} $$")
                variables = f.get("variables", {})
                if variables:
                    var_desc = "；".join(f"{k}: {v}" for k, v in variables.items())
                    lines.append(f"变量说明：{var_desc}")
        if s.get("application_scenarios"):
            lines.append("应用场景：" + "；".join(s["application_scenarios"]))
        if s.get("key_caveats"):
            lines.append("关键要点：" + "；".join(s["key_caveats"]))
        if s.get("step_by_step"):
            steps = "；".join(f"{i+1}.{x}" for i, x in enumerate(s["step_by_step"]))
            lines.append(f"步骤：{steps}")
        return "\n".join(lines)

    def to_citation(self) -> dict:
        """返回给前端的引用元数据。"""
        return {
            "chunk_id": self.chunk_id,
            "title": self.title,
            "category": self.category,
            "difficulty": self.difficulty,
        }

    @classmethod
    def from_concept_page(cls, page: dict, source_file: str) -> "KnowledgeUnit":
        """把 build_knowledge.py 生成的方法百科页（schema_version=3）映射成 KnowledgeUnit。

        新 schema 的 explain 分层字段折叠进旧 detailed_summary 结构，
        BM25 检索与 to_context 渲染逻辑全部复用，无需改动。
        """
        ex = page.get("explain", {}) or {}
        # one_liner/intuition/when_to_use/tools 拼进可检索文本与定义
        definition = " ".join(p for p in [ex.get("one_liner", ""), ex.get("intuition", ""),
                                           ex.get("definition", "")] if p)
        scenarios = [s for s in [ex.get("when_to_use", ""), ex.get("tools", "")] if s]
        raw = {
            "chunk_id": page.get("concept_id", "unknown"),
            "title": page.get("title", "未命名"),
            "category": (page.get("taxonomy_path") or ["未分类"])[0],
            "difficulty": page.get("difficulty", ""),
            "prerequisites": page.get("prerequisites", []) or [],
            "detailed_summary": {
                "definition": definition,
                "math_principle": ex.get("math_principle", ""),
                "teaching_examples": ex.get("worked_example", ""),
                "application_scenarios": scenarios,
                "key_caveats": ex.get("pitfalls", []) or [],
                "step_by_step": ex.get("step_by_step", []) or [],
            },
            "keywords": page.get("aliases", []) or [],
            "formulas": page.get("formulas", []) or [],
            "source_excerpt": "",
        }
        unit = cls(raw, source_file)
        # 保留原始分层字段，供前端学习卡片渲染（detailed_summary 的压平仅用于 BM25/LLM 上下文）。
        unit.explain = ex
        unit.roles = page.get("roles", []) or []
        unit.tools = ex.get("tools", "")
        unit.cases = page.get("cases", []) or []
        unit.taxonomy_path = page.get("taxonomy_path", []) or []
        unit.priority = page.get("priority", "")
        unit.module_id = page.get("module_id", "")
        unit.subcat_id = page.get("subcat_id", "")
        return unit


_TOKEN_RE = re.compile(r"[\w一-鿿]+")

# 中文停用词：功能词/疑问词/口语词。过滤后，闲聊或跑题 query 的 BM25 分数会塌向 0，
# 与真正命中知识库的 query 拉开差距，让「超出范围」判定更可靠。
_STOPWORDS = {
    "的", "了", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "是", "在", "有", "和", "与", "及",
    "这", "那", "之", "也", "就", "都", "而", "或", "我", "你", "他", "她", "它", "我们",
    "你们", "他们", "怎么", "怎样", "如何", "怎么样", "什么", "为什么", "哪些", "哪个",
    "可以", "能不能", "需要", "想要", "请", "帮", "帮我", "一下", "一个", "关于", "对于",
    "今天", "明天", "昨天", "现在", "一些", "这个", "那个", "如果", "因为", "所以", "但是",
    "还是", "已经", "应该", "可能", "比较", "非常", "很", "最", "更", "再", "还", "把", "被",
    "给", "让", "对", "向", "从", "到", "用", "做", "要", "会", "得", "着", "过", "上", "下",
}


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    """中英混合分词：jieba 切中文，正则保留英文/数字 token，过滤停用词。"""
    tokens = []
    for tok in jieba.cut(text):
        tok = tok.strip().lower()
        if not tok or not _TOKEN_RE.match(tok):
            continue
        if drop_stopwords and tok in _STOPWORDS:
            continue
        tokens.append(tok)
    return tokens


class BM25Index:
    """简易 BM25 检索（Okapi BM25）。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs_tokens: list[list[str]] = []
        self.doc_freqs: list[Counter] = []
        self.df: Counter = Counter()
        self.idf: dict[str, float] = {}
        self.doc_len: list[int] = []
        self.avg_len: float = 0.0
        self.n_docs: int = 0

    def build(self, documents: list[str]):
        self.docs_tokens = [tokenize(d) for d in documents]
        self.n_docs = len(self.docs_tokens)
        self.doc_len = [len(t) for t in self.docs_tokens]
        self.avg_len = (sum(self.doc_len) / self.n_docs) if self.n_docs else 0.0

        for tokens in self.docs_tokens:
            freqs = Counter(tokens)
            self.doc_freqs.append(freqs)
            for term in freqs:
                self.df[term] += 1

        for term, freq in self.df.items():
            # BM25 idf（加 0.5 平滑，下限 0）
            self.idf[term] = max(
                math.log((self.n_docs - freq + 0.5) / (freq + 0.5) + 1), 0.0
            )

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        q_tokens = tokenize(query)
        scores = [0.0] * self.n_docs
        for term in q_tokens:
            if term not in self.idf:
                continue
            idf = self.idf[term]
            for idx in range(self.n_docs):
                freq = self.doc_freqs[idx].get(term, 0)
                if freq == 0:
                    continue
                denom = freq + self.k1 * (
                    1 - self.b + self.b * self.doc_len[idx] / (self.avg_len or 1)
                )
                scores[idx] += idf * (freq * (self.k1 + 1)) / denom
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class KnowledgeBase:
    def __init__(self):
        self.units: list[KnowledgeUnit] = []
        self.index = BM25Index()
        self._loaded = False

    def load(self):
        # 新库优先：若 concepts/ 已生成方法百科页，则只加载新库（151 个 concept_id 已去重，
        # 391/394 旧讲义碎片已并入对应百科页，仅 3 个 NEW 碎片未并入）。
        # 仅当新库为空时，回退加载旧 structured_lecture_*.json 碎片。
        cdir: Path = config.CONCEPTS_DIR
        concept_files = sorted(cdir.glob("*.json")) if cdir.exists() else []
        if concept_files:
            for cf in concept_files:
                try:
                    with open(cf, "r", encoding="utf-8") as f:
                        page = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("跳过百科页 %s：%s", cf.name, e)
                    continue
                if isinstance(page, dict) and page.get("concept_id"):
                    self.units.append(KnowledgeUnit.from_concept_page(page, cf.name))
            self.index.build([u._search_text for u in self.units])
            self._loaded = True
            logger.info("已加载 %d 个方法百科页（新库 concepts/）", len(self.units))
            return

        # 回退：旧讲义碎片库
        kdir: Path = config.KNOWLEDGE_DIR
        files = sorted(kdir.glob("structured_lecture_*.json"))
        for jf in files:
            if jf.name.startswith("~"):
                continue
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("跳过 %s：%s", jf.name, e)
                continue
            if isinstance(data, list):
                for raw in data:
                    self.units.append(KnowledgeUnit(raw, jf.name))

        self.index.build([u._search_text for u in self.units])
        self._loaded = True
        logger.info("已加载 %d 个知识单元（旧讲义 %d 文件，新库未生成）", len(self.units), len(files))

    def search(self, query: str, top_k: int | None = None) -> list[tuple[KnowledgeUnit, float]]:
        if not self._loaded:
            self.load()
        top_k = top_k or config.RETRIEVAL_TOP_K
        results = self.index.search(query, top_k)
        return [(self.units[i], score) for i, score in results if score > 0]

    def categories(self) -> dict[str, int]:
        counts: Counter = Counter()
        for u in self.units:
            counts[u.category] += 1
        return dict(counts)


# 全局单例
knowledge_base = KnowledgeBase()

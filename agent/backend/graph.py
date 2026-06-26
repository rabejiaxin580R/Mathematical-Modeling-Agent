"""知识图谱构建：把方法百科页按三级分类（模块 → 子类 → 概念）连成「知识地图」。

新知识库（schema_version=3）不再携带 prerequisites，而用 taxonomy_path / module_id /
subcat_id 表达层级。图谱据此构建一棵分类树：

    module 节点 (mod::A)  →  subcat 节点 (sub::A.1)  →  concept 叶子 (concept_id)

概念叶子被点击后打开学习卡片；node_detail 额外返回「所属分类面包屑」与「同类知识点」，
供阅读视图内导航（取代旧的前置/后续）。结果模块级缓存。
"""
import logging

from .knowledge import knowledge_base

logger = logging.getLogger(__name__)

# 难度排序：用于把同类知识点按由易到难排列
_DIFF_RANK = {"beginner": 0, "intermediate": 1, "advanced": 2, "": 1}

_GRAPH: dict | None = None


def _mod_id(mid: str) -> str:
    return f"mod::{mid}"


def _sub_id(sid: str) -> str:
    return f"sub::{sid}"


def _module_of(unit):
    """返回 (module_id, subcat_id, module_name, subcat_name)，对缺字段做兜底。"""
    tax = unit.taxonomy_path or []
    mid = unit.module_id or (tax[0] if tax else "?")
    sid = unit.subcat_id or mid
    mod_name = tax[0] if len(tax) > 0 else (unit.category or "未分类")
    sub_name = tax[1] if len(tax) > 1 else mod_name
    return mid, sid, mod_name, sub_name


def build_graph(force: bool = False) -> dict:
    """构建（或返回缓存的）三级分类知识地图。"""
    global _GRAPH
    if _GRAPH is not None and not force:
        return _GRAPH

    units = knowledge_base.units
    if not units:
        knowledge_base.load()
        units = knowledge_base.units

    nodes: list[dict] = []
    edges: list[dict] = []
    modules: dict[str, str] = {}            # mid -> 模块名
    subcats: dict[str, dict] = {}           # sid -> {name, mid}
    sub_counts: dict[str, int] = {}         # sid -> 概念数

    # 先扫一遍，收集模块 / 子类
    for u in units:
        mid, sid, mod_name, sub_name = _module_of(u)
        modules.setdefault(mid, mod_name)
        subcats.setdefault(sid, {"name": sub_name, "mid": mid})
        sub_counts[sid] = sub_counts.get(sid, 0) + 1

    mod_counts: dict[str, int] = {}
    for sid, info in subcats.items():
        mod_counts[info["mid"]] = mod_counts.get(info["mid"], 0) + sub_counts.get(sid, 0)

    # 模块节点（顶层）
    for mid, name in sorted(modules.items()):
        nodes.append({
            "id": _mod_id(mid), "type": "module",
            "title": f"{mid} · {name}" if mid and mid != "?" else name,
            "name": name, "category": name, "module_id": mid,
            "difficulty": "", "keyword_top": [], "count": mod_counts.get(mid, 0),
        })

    # 子类节点（中层）+ 模块→子类 边
    for sid, info in sorted(subcats.items()):
        nodes.append({
            "id": _sub_id(sid), "type": "subcat",
            "title": info["name"], "name": info["name"],
            "category": modules.get(info["mid"], ""), "module_id": info["mid"],
            "subcat_id": sid, "difficulty": "", "keyword_top": [],
            "count": sub_counts.get(sid, 0),
        })
        edges.append({"from": _mod_id(info["mid"]), "to": _sub_id(sid), "rel": "contains"})

    # 概念叶子（底层）+ 子类→概念 边
    for u in units:
        mid, sid, _, _ = _module_of(u)
        nodes.append({
            "id": u.chunk_id, "type": "chunk",
            "title": u.title, "category": u.category,
            "difficulty": u.difficulty,
            "module_id": mid, "subcat_id": sid,
            "keyword_top": u.keywords[:3],
        })
        edges.append({"from": _sub_id(sid), "to": u.chunk_id, "rel": "contains"})

    _GRAPH = {
        "meta": {
            "node_count": len(nodes),
            "module_count": len(modules),
            "subcat_count": len(subcats),
            "chunk_count": len(units),
            "edge_count": len(edges),
        },
        "categories": knowledge_base.categories(),
        "nodes": nodes,
        "edges": edges,
    }
    logger.info("知识图谱构建完成：%d 模块 / %d 子类 / %d 概念, %d 边",
                len(modules), len(subcats), len(units), len(edges))
    return _GRAPH


_MAP: dict | None = None


def build_map(force: bool = False) -> dict:
    """构建（或返回缓存的）课程地图：模块 → 子类 → 概念 的嵌套结构。

    供「课程地图·卡片分栏」总览使用。概念按难度（易→难）再标题排序，
    便于初学者循序渐进。
    """
    global _MAP
    if _MAP is not None and not force:
        return _MAP

    units = knowledge_base.units
    if not units:
        knowledge_base.load()
        units = knowledge_base.units

    # module_id -> {name, subcats: {sid -> {name, concepts: []}}}
    modules: dict[str, dict] = {}
    for u in units:
        mid, sid, mod_name, sub_name = _module_of(u)
        m = modules.setdefault(mid, {"name": mod_name, "subcats": {}})
        sc = m["subcats"].setdefault(sid, {"name": sub_name, "concepts": []})
        sc["concepts"].append({
            "id": u.chunk_id,
            "title": u.title,
            "difficulty": u.difficulty or "",
        })

    out_modules = []
    for mid in sorted(modules):
        m = modules[mid]
        subcats = []
        count = 0
        for sid in sorted(m["subcats"]):
            sc = m["subcats"][sid]
            concepts = sorted(
                sc["concepts"],
                key=lambda c: (_DIFF_RANK.get((c["difficulty"] or "").lower(), 1), c["title"]),
            )
            count += len(concepts)
            subcats.append({"subcat_id": sid, "name": sc["name"], "concepts": concepts})
        out_modules.append({
            "module_id": mid, "name": m["name"], "count": count, "subcats": subcats,
        })

    _MAP = {
        "meta": {
            "module_count": len(out_modules),
            "concept_count": sum(m["count"] for m in out_modules),
        },
        "modules": out_modules,
    }
    return _MAP


def node_detail(chunk_id: str) -> dict | None:
    """返回单个概念的完整学习内容 + 所属分类面包屑 + 同类知识点。"""
    unit = next((u for u in knowledge_base.units if u.chunk_id == chunk_id), None)
    if unit is None:
        return None

    # 同一子类下的其它知识点，按难度由易到难排序，便于循序渐进
    siblings = [
        u for u in knowledge_base.units
        if u.chunk_id != chunk_id and u.subcat_id and u.subcat_id == unit.subcat_id
    ]
    siblings.sort(key=lambda x: _DIFF_RANK.get((x.difficulty or "").lower(), 1))
    sibling_list = [
        {"id": u.chunk_id, "title": u.title, "type": "chunk", "difficulty": u.difficulty}
        for u in siblings
    ]

    s = unit.summary
    ex = unit.explain or {}

    def _field(key: str, fallback=""):
        """新库优先取原始 explain 分层字段，旧讲义回退到压平后的 summary。"""
        v = ex.get(key)
        if v not in (None, "", []):
            return v
        return s.get(key, fallback)

    return {
        "chunk_id": unit.chunk_id,
        "title": unit.title,
        "category": unit.category,
        "difficulty": unit.difficulty,
        # 分层学习字段（新库 explain 原样透传，旧库回退 detailed_summary）
        "one_liner": ex.get("one_liner", ""),
        "intuition": ex.get("intuition", ""),
        "when_to_use": ex.get("when_to_use", ""),
        "definition": _field("definition"),
        "math_principle": _field("math_principle"),
        "worked_example": _field("worked_example", s.get("teaching_examples", "")),
        "pitfalls": _field("pitfalls", s.get("key_caveats", [])) or [],
        "tools": unit.tools,
        # 兼容旧前端字段名
        "teaching_examples": s.get("teaching_examples", ""),
        "application_scenarios": s.get("application_scenarios", []) or [],
        "key_caveats": s.get("key_caveats", []) or [],
        "step_by_step": _field("step_by_step", s.get("step_by_step", [])) or [],
        "formulas": unit.formulas,
        "keywords": unit.keywords,
        # 侧重点 / 真题 / 分类元数据
        "roles": unit.roles or [],
        "cases": unit.cases or [],
        "taxonomy_path": unit.taxonomy_path or [],
        "priority": unit.priority or "",
        "module_id": unit.module_id or "",
        "subcat_id": unit.subcat_id or "",
        # 同类知识点导航（取代旧的前置/后续）
        "siblings": sibling_list,
        "source_excerpt": unit.source_excerpt,
    }

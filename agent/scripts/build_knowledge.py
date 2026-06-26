"""知识库重构流水线 v3：把「讲义碎片 + 150 篇优秀论文」归并成
「主索引(taxonomy) + 方法百科(concepts) + 论文案例库(cases) + 学习路径」双层知识库。

由开发者本地运行，用 DeepSeek（OpenAI 兼容）做苦力。密钥仅运行时传入，绝不写入源码/产物/日志。
复用 build_problems.py 的成熟部件：MarkItDown 抽取、call_json 两阶段调用、parse_json 容错、_largest_remainder。

五阶段（可独立跑、产物落盘、断点续跑）：
  stage1  清洗旧讲义碎片：每个旧 chunk → 映射到一个 concept_id（去重锚定）
  stage2  抽取论文：自动发现全部论文 → MarkItDown 全文缓存 → DeepSeek 抽知识点并映射 concept_id
  stage3  归并百科：同一 concept_id 下汇总讲义碎片+多篇论文 → 合成一篇「讲透」的百科页
  stage4  案例库+反链+学习路径：cases/<paper>.json、concept.cases[]、learning_paths.json
  all     依次跑 1→2→3→4

用法：
  python scripts/build_knowledge.py --api-key sk-xxx --stage all
  python scripts/build_knowledge.py --api-key sk-xxx --stage 2 --limit 5      # 先小批试跑 5 篇
  python scripts/build_knowledge.py --api-key sk-xxx --stage 3 --only ts.arima # 只重建某个百科页
  $env:LLM_API_KEY="sk-xxx"; python scripts/build_knowledge.py --stage all
"""
import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent
REVENT_DIR = AGENT_DIR.parent
sys.path.insert(0, str(AGENT_DIR))

# ────────── 路径布局 ──────────
KB_DIR = AGENT_DIR / "data" / "knowledge"
TAXONOMY_PATH = KB_DIR / "taxonomy.json"
CONCEPTS_DIR = KB_DIR / "concepts"
CASES_DIR = KB_DIR / "cases"
FULLTEXT_DIR = KB_DIR / "_fulltext"            # 论文全文缓存（避免重复抽 PDF）
RAW_DIR = KB_DIR / "_raw_extractions"          # stage2 每篇论文原始抽取
LECTURE_MAP_PATH = KB_DIR / "_lecture_map.json"  # stage1 旧 chunk → concept_id
NEW_CONCEPTS_PATH = KB_DIR / "_unmapped.json"  # 映射不上的，待人工新建
LEARNING_PATHS_PATH = KB_DIR / "learning_paths.json"

# 论文来源（自动发现）
HIMCM_DIR = AGENT_DIR / "assets" / "HiMCMpapers"     # <year>/<letter>/<digits>.pdf
PROBLEM_PAPERS_DIR = AGENT_DIR / "data" / "problem_papers"  # <prob_id>/<paper_id>.txt（已抽好）
ESSAY_TEXT_DIR = REVENT_DIR / "essay" / "extracted_texts"   # 范文 txt
LECTURES_DIR = REVENT_DIR / "json_outputs"            # structured_lecture_*.json

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
PAPER_CHARS = 14000  # 每篇论文喂给模型的截断字符数

_md = None


# ════════════════════════════════════════════════════════════
# 复用部件（移植自 build_problems.py）
# ════════════════════════════════════════════════════════════

def get_md():
    global _md
    if _md is None:
        from markitdown import MarkItDown
        _md = MarkItDown()
    return _md


def extract_text(path: Path, limit: int | None = None) -> str:
    try:
        text = get_md().convert(str(path)).text_content or ""
    except Exception as e:
        print(f"    [警告] 抽取失败 {path.name}: {e}")
        return ""
    if limit and len(text) > limit:
        text = text[:limit]
    return text.strip()


def parse_json(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
        t = t.strip()
    l, r = t.find("{"), t.rfind("}")
    if l != -1 and r > l:
        t = t[l:r + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def call_json(client, model, system, user, max_tokens=8000, retry_hint=""):
    def _do(extra_sys=""):
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": system + extra_sys},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        # 暂时性错误（网络抖动/限流/超载）指数退避重试，避免长跑因一次中断全军覆没
        last_err = None
        for attempt in range(5):
            try:
                try:
                    resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                except Exception:
                    resp = client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                wait = min(2 ** attempt * 3, 60)  # 3,6,12,24,48s
                print(f"      [重试 {attempt+1}/5] {type(e).__name__}: {str(e)[:60]} → {wait}s 后重试")
                time.sleep(wait)
        raise last_err

    out = parse_json(_do())
    if out is None:
        out = parse_json(_do("\n\n再次强调：只输出合法 JSON。" + retry_hint))
    return out


# ════════════════════════════════════════════════════════════
# 主索引加载 + concept_id 映射校验（去重锚点）
# ════════════════════════════════════════════════════════════

class Taxonomy:
    """加载 taxonomy.json，提供 concept_id 校验、别名映射、阶段/角色元信息。"""

    def __init__(self, raw: dict):
        self.raw = raw
        self.concepts: dict[str, dict] = {}      # concept_id -> 扁平元信息
        self.alias_to_id: dict[str, str] = {}    # 归一化别名/标题 -> concept_id
        for mod in raw.get("modules", []):
            for sub in mod.get("subcategories", []):
                for c in sub.get("concepts", []):
                    cid = c["concept_id"]
                    meta = {
                        **c,
                        "module_id": mod["module_id"],
                        "module_title": mod["title"],
                        "subcat_id": sub["subcat_id"],
                        "subcat_title": sub["title"],
                        "priority": mod.get("priority", ""),
                    }
                    self.concepts[cid] = meta
                    self._register_alias(c["title"], cid)
                    self._register_alias(cid, cid)
                    for a in c.get("aliases", []) or []:
                        self._register_alias(a, cid)

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[\s（）()\-_,，。.、/]+", "", str(s or "")).lower()

    def _register_alias(self, alias: str, cid: str):
        k = self._norm(alias)
        if k:
            self.alias_to_id.setdefault(k, cid)

    def valid(self, cid: str) -> bool:
        return cid in self.concepts

    def resolve(self, label: str) -> str | None:
        """把模型给的标题/别名解析成已知 concept_id；解析不上返回 None。"""
        if not label:
            return None
        if label in self.concepts:
            return label
        return self.alias_to_id.get(self._norm(label))

    def catalog_brief(self) -> str:
        """给模型看的「合法 concept_id 清单」，按模块/子类分组。"""
        lines = []
        for mod in self.raw.get("modules", []):
            for sub in mod.get("subcategories", []):
                ids = "  ".join(
                    f"{c['concept_id']}（{c['title']}）" for c in sub.get("concepts", [])
                )
                lines.append(f"[{sub['subcat_id']} {sub['title']}] {ids}")
        return "\n".join(lines)

    def all_ids(self) -> list[str]:
        return list(self.concepts.keys())


def load_taxonomy() -> Taxonomy:
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        return Taxonomy(json.load(f))


# ════════════════════════════════════════════════════════════
# 论文自动发现（替代旧脚本手写的 PAPER_META）
# ════════════════════════════════════════════════════════════

def discover_papers() -> list[dict]:
    """自动发现所有论文，统一成 {paper_id, source, contest, year, problem, path, kind}。
    kind: 'pdf'（需抽取） | 'txt'（已是文本）。paper_id 全库唯一。
    """
    found, seen = [], set()

    def add(pid, **kw):
        if pid in seen:
            return
        seen.add(pid)
        found.append({"paper_id": pid, **kw})

    # 1) HiMCMpapers/<year>/<letter>/<digits>.pdf
    if HIMCM_DIR.exists():
        for year_dir in sorted(HIMCM_DIR.iterdir()):
            if not (year_dir.is_dir() and year_dir.name.isdigit()):
                continue
            for letter_dir in sorted(year_dir.iterdir()):
                if not letter_dir.is_dir():
                    continue
                for pf in sorted(letter_dir.glob("*.pdf")):
                    if pf.stem.isdigit():
                        add(pf.stem, source="himcm", contest="HiMCM",
                            year=int(year_dir.name), problem=letter_dir.name,
                            path=str(pf), kind="pdf")

    # 2) data/problem_papers/<prob_id>/<paper_id>.txt（build_problems 已抽好的全文）
    if PROBLEM_PAPERS_DIR.exists():
        for prob_dir in sorted(PROBLEM_PAPERS_DIR.iterdir()):
            if not prob_dir.is_dir():
                continue
            m = re.match(r"prob_(\d{4})_([A-Z]+)", prob_dir.name)
            year = int(m.group(1)) if m else 0
            letter = m.group(2) if m else "?"
            for tf in sorted(prob_dir.glob("*.txt")):
                add(tf.stem, source="problem_papers", contest="HiMCM",
                    year=year, problem=letter, path=str(tf), kind="txt")

    # 3) essay/extracted_texts/*.txt（范文）
    if ESSAY_TEXT_DIR.exists():
        for tf in sorted(ESSAY_TEXT_DIR.glob("*.txt")):
            pid = tf.stem if tf.stem.isdigit() else f"essay_{tf.stem}"
            add(pid, source="essay", contest="HiMCM", year=0, problem="?",
                path=str(tf), kind="txt")

    return found


def get_fulltext(paper: dict, force: bool = False) -> str:
    """取论文全文（带缓存）：txt 直接读，pdf 用 MarkItDown 抽取后落盘缓存。"""
    cache = FULLTEXT_DIR / f"{paper['paper_id']}.txt"
    if cache.exists() and not force:
        return cache.read_text(encoding="utf-8")
    if paper["kind"] == "txt":
        text = Path(paper["path"]).read_text(encoding="utf-8", errors="ignore").strip()
    else:
        text = extract_text(Path(paper["path"]))
    if text:
        FULLTEXT_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return text


# ════════════════════════════════════════════════════════════
# Stage 1：清洗旧讲义碎片 → 映射 concept_id
# ════════════════════════════════════════════════════════════

SYS_MAP_LECTURE = """你是数学建模知识库管理员。给你一批「旧讲义知识碎片」的标题+分类+关键词，以及一份「权威 concept_id 主索引清单」。请把每个旧碎片映射到主索引中最贴切的一个 concept_id。

只输出一个合法 JSON 对象：
{"mappings": [{"chunk_id": "原碎片id", "concept_id": "映射到的concept_id 或 NEW", "confidence": 0.0-1.0, "reason": "一句话理由"}]}

规则：
1. concept_id 必须从给定清单中精确选择（区分大小写，原样复制）。
2. 一个碎片只映射到最主要的一个 concept_id。
3. 实在没有任何贴切的 concept_id，才填 "NEW"，并在 reason 里说明这是个什么新知识点。
4. 不要遗漏任何 chunk_id，逐条给出。"""


def load_lecture_chunks() -> list[dict]:
    chunks = []
    for jf in sorted(LECTURES_DIR.glob("structured_lecture_*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, list):
            for raw in data:
                raw["_source_file"] = jf.name
                chunks.append(raw)
    return chunks


def stage1_clean_lectures(client, model, tax: Taxonomy, force: bool):
    if LECTURE_MAP_PATH.exists() and not force:
        print(f"[stage1] 已存在 {LECTURE_MAP_PATH.name}（--force 重跑）")
        return
    chunks = load_lecture_chunks()
    print(f"[stage1] 载入 {len(chunks)} 个旧讲义碎片，分批映射 concept_id…")
    catalog = tax.catalog_brief()
    mapping = {}
    BATCH = 25
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        listing = "\n".join(
            f"- chunk_id={c.get('chunk_id','?')} | 标题={c.get('title','')} | "
            f"原分类={c.get('category','')} | 关键词={'、'.join(c.get('keywords',[]) or [])}"
            for c in batch
        )
        user = f"=== 权威 concept_id 主索引清单 ===\n{catalog}\n\n=== 待映射旧碎片 ===\n{listing}\n\n请输出 mappings JSON。"
        res = call_json(client, model, SYS_MAP_LECTURE, user, max_tokens=4000)
        for m in (res or {}).get("mappings", []) if res else []:
            cid = m.get("concept_id", "NEW")
            if cid != "NEW" and not tax.valid(cid):
                cid = tax.resolve(cid) or "NEW"
            mapping[m.get("chunk_id")] = {
                "concept_id": cid,
                "confidence": m.get("confidence", 0),
                "reason": m.get("reason", ""),
            }
        print(f"    映射进度 {min(i+BATCH,len(chunks))}/{len(chunks)}")
        time.sleep(0.5)
    KB_DIR.mkdir(parents=True, exist_ok=True)
    LECTURE_MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    n_new = sum(1 for v in mapping.values() if v["concept_id"] == "NEW")
    print(f"[stage1] 完成：{len(mapping)} 个碎片已映射，其中 {n_new} 个标记 NEW（待人工新建）")


# ════════════════════════════════════════════════════════════
# Stage 2：抽取论文 → 知识点 + 映射 concept_id
# ════════════════════════════════════════════════════════════

SYS_EXTRACT_PAPER = """你是数学建模竞赛评委，正在分析一篇优秀竞赛论文。请提取论文中实际使用的、可独立学习的「知识点」，并把每个知识点对应到给定的「权威 concept_id 主索引」。

只输出一个合法 JSON 对象：
{"items": [
  {
    "concept_id": "从主索引清单精确选择，或 NEW",
    "concept_label": "若为 NEW，给出这个新知识点的中文标题；否则留空",
    "stage": "该方法用在建模哪一步：restate/assume/notation/build/solve/sensitivity/evaluate/extend/write 之一",
    "how_used": "这篇论文具体怎么用这个方法的（中文，1-3句，点出关键处理/参数/技巧）",
    "formulas": [{"latex_code": "论文中实际出现的公式", "variables": {"符号": "含义"}}],
    "source_excerpt": "论文原文片段（英文即可），支撑上面的描述"
  }
]}

规则：
1. concept_id 必须从给定清单精确复制；找不到贴切的才填 NEW 并给 concept_label。
2. 只提取论文真正展开使用的方法，简单提一句的不要提；覆盖：核心建模方法、求解算法、评价/检验方法、以及突出的论文写作/可视化亮点。
3. 公式必须与原文一致，不要编造。每篇 4-8 个知识点为宜。
4. 不要输出主索引里没有、论文里也没有的内容。"""


def stage2_extract_papers(client, model, tax: Taxonomy, force: bool, limit: int, only: set):
    papers = discover_papers()
    if only:
        papers = [p for p in papers if p["paper_id"] in only]
    if limit:
        papers = papers[:limit]
    print(f"[stage2] 发现 {len(papers)} 篇论文待抽取")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    catalog = tax.catalog_brief()
    done = unmapped = 0
    unmapped_labels = []

    for idx, paper in enumerate(papers, 1):
        out = RAW_DIR / f"{paper['paper_id']}.json"
        if out.exists() and not force:
            print(f"  [{idx}/{len(papers)}] {paper['paper_id']} 已抽取，跳过")
            done += 1
            continue
        print(f"  [{idx}/{len(papers)}] {paper['paper_id']} "
              f"({paper['source']} {paper['contest']} {paper['year']} {paper['problem']})")
        text = get_fulltext(paper)
        if not text:
            print("       [跳过] 全文为空")
            continue
        user = (f"=== 权威 concept_id 主索引清单 ===\n{catalog}\n\n"
                f"=== 论文信息 ===\n编号 {paper['paper_id']} | {paper['contest']} {paper['year']} {paper['problem']} 题\n\n"
                f"=== 论文正文（可能截断） ===\n{text[:PAPER_CHARS]}\n\n请输出 items JSON。")
        try:
            res = call_json(client, model, SYS_EXTRACT_PAPER, user, max_tokens=6000,
                            retry_hint=" items 里 concept_id 必须来自清单或为 NEW。")
        except Exception as e:
            print(f"       [失败] 调用出错：{e}")
            continue
        items = (res or {}).get("items", []) if res else []
        # 规范化 + 校验 concept_id
        clean = []
        for it in items:
            cid = it.get("concept_id", "NEW")
            if cid != "NEW" and not tax.valid(cid):
                cid = tax.resolve(cid) or tax.resolve(it.get("concept_label", "")) or "NEW"
            if cid == "NEW":
                unmapped += 1
                unmapped_labels.append(it.get("concept_label") or it.get("how_used", "")[:30])
            clean.append({
                "concept_id": cid,
                "concept_label": it.get("concept_label", ""),
                "stage": it.get("stage", ""),
                "how_used": str(it.get("how_used", "")).strip(),
                "formulas": it.get("formulas", []) or [],
                "source_excerpt": str(it.get("source_excerpt", "")).strip(),
            })
        out.write_text(json.dumps({
            "paper_id": paper["paper_id"], "meta": paper, "items": clean,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"       抽取 {len(clean)} 个知识点")
        done += 1
        time.sleep(0.5)

    if unmapped_labels:
        prev = json.loads(NEW_CONCEPTS_PATH.read_text(encoding="utf-8")) if NEW_CONCEPTS_PATH.exists() else []
        NEW_CONCEPTS_PATH.write_text(
            json.dumps(sorted(set(prev + unmapped_labels)), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[stage2] 完成：处理 {done} 篇，{unmapped} 处映射不上已记入 {NEW_CONCEPTS_PATH.name}（待人工审）")


# ════════════════════════════════════════════════════════════
# Stage 3：按 concept_id 归并 → 方法百科页
# ════════════════════════════════════════════════════════════

SYS_SYNTH_CONCEPT = """你是数学建模教学专家，要为一个知识点写一篇「讲透」的百科页，面向零基础初学者，要做到通俗、详细、有条理。给你该知识点的元信息、来自旧讲义的原始素材、以及多篇优秀论文里它的实战用法。请综合这些素材，输出一篇分层讲解的百科页。

只输出一个合法 JSON 对象，结构：
{
  "explain": {
    "one_liner": "一句话本质：它解决什么问题（小白也能懂）",
    "intuition": "生活化类比/直觉解释，零基础读完能有画面感",
    "when_to_use": "什么样的题目/数据该想到用它（识别信号）",
    "definition": "正式定义",
    "math_principle": "数学原理与推导思路（可含 $LaTeX$）",
    "step_by_step": ["使用步骤1", "步骤2", "..."],
    "worked_example": "一个从头到尾的完整算例（结合论文素材更好）",
    "pitfalls": ["常见坑/局限1", "坑2"],
    "tools": "常用工具与最小可运行代码片段（Python 优先，注明库与函数）"
  },
  "formulas": [{"latex_code": "核心公式", "variables": {"符号": "含义"}}]
}

规则：
1. 内容必须基于给定素材，不要编造论文里没有的数据；素材不足的字段，凭你的专业知识补全通用内容即可。
2. 语言通俗、循序渐进，先直觉后公式。所有讲解用中文，公式/代码/变量名保持原样。
3. step_by_step 要可操作；pitfalls 要具体；tools 要给真能跑的代码骨架。"""


def collect_material(tax: Taxonomy):
    """汇总每个 concept_id 名下的讲义碎片与论文用法。"""
    by_concept = defaultdict(lambda: {"lectures": [], "papers": []})

    # 讲义碎片：经 stage1 映射
    if LECTURE_MAP_PATH.exists():
        lec_map = json.loads(LECTURE_MAP_PATH.read_text(encoding="utf-8"))
        chunks = {c.get("chunk_id"): c for c in load_lecture_chunks()}
        for chunk_id, m in lec_map.items():
            cid = m.get("concept_id")
            if cid and cid != "NEW" and chunk_id in chunks:
                by_concept[cid]["lectures"].append(chunks[chunk_id])

    # 论文用法：stage2 产物
    if RAW_DIR.exists():
        for rf in sorted(RAW_DIR.glob("*.json")):
            data = json.loads(rf.read_text(encoding="utf-8"))
            pid = data["paper_id"]
            for it in data.get("items", []):
                cid = it.get("concept_id")
                if cid and cid != "NEW":
                    by_concept[cid]["papers"].append({"paper_id": pid, **it})
    return by_concept


def stage3_synthesize(client, model, tax: Taxonomy, force: bool, only: set):
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    material = collect_material(tax)
    targets = list(tax.all_ids())
    if only:
        targets = [c for c in targets if c in only]
    print(f"[stage3] 将生成/更新 {len(targets)} 个百科页")
    for idx, cid in enumerate(targets, 1):
        out = CONCEPTS_DIR / f"{cid}.json"
        if out.exists() and not force:
            continue
        meta = tax.concepts[cid]
        mat = material.get(cid, {"lectures": [], "papers": []})
        lec_txt = "\n".join(
            f"- {c.get('title','')}：{(c.get('detailed_summary',{}) or {}).get('definition','')} "
            f"{(c.get('detailed_summary',{}) or {}).get('math_principle','')}"[:400]
            for c in mat["lectures"][:6]
        ) or "（无讲义素材）"
        paper_txt = "\n".join(
            f"- 论文{p['paper_id']}（{p.get('stage','')}）：{p.get('how_used','')}"
            for p in mat["papers"][:10]
        ) or "（无论文素材）"
        user = (
            f"知识点：{meta['title']}（concept_id={cid}）\n"
            f"所属：{meta['module_title']} / {meta['subcat_title']}｜难度：{meta.get('difficulty','')}\n\n"
            f"=== 旧讲义素材 ===\n{lec_txt}\n\n=== 优秀论文里的实战用法 ===\n{paper_txt}\n\n"
            f"请按 system 要求输出这一知识点的百科页 JSON。"
        )
        try:
            res = call_json(client, model, SYS_SYNTH_CONCEPT, user, max_tokens=4000)
        except Exception as e:
            print(f"  [{idx}/{len(targets)}] {cid} 失败：{e}")
            continue
        if not res or "explain" not in res:
            print(f"  [{idx}/{len(targets)}] {cid} 模型未返回 explain，跳过")
            continue
        page = {
            "schema_version": 3,
            "concept_id": cid,
            "title": meta["title"],
            "taxonomy_path": [meta["module_title"], meta["subcat_title"], meta["title"]],
            "module_id": meta["module_id"],
            "subcat_id": meta["subcat_id"],
            "aliases": meta.get("aliases", []),
            "difficulty": meta.get("difficulty", ""),
            "roles": meta.get("roles", []),
            "priority": meta.get("priority", ""),
            "explain": res["explain"],
            "formulas": res.get("formulas", []),
            "cases": sorted({p["paper_id"] for p in mat["papers"]}),
            "provenance": {
                "from_lectures": [c.get("chunk_id") for c in mat["lectures"]],
                "from_papers": sorted({p["paper_id"] for p in mat["papers"]}),
            },
        }
        out.write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [{idx}/{len(targets)}] {cid} ✓ "
              f"（讲义{len(mat['lectures'])} 论文{len(mat['papers'])}）")
        time.sleep(0.3)
    print("[stage3] 完成")


# ════════════════════════════════════════════════════════════
# Stage 4：案例库 + 反向链接 + 学习路径
# ════════════════════════════════════════════════════════════

def stage4_cases_and_paths(tax: Taxonomy):
    CASES_DIR.mkdir(parents=True, exist_ok=True)

    # 4a) 案例库：每篇论文 → 用了哪些 concept_id、各步怎么用
    if RAW_DIR.exists():
        for rf in sorted(RAW_DIR.glob("*.json")):
            data = json.loads(rf.read_text(encoding="utf-8"))
            uses = []
            for it in data.get("items", []):
                cid = it.get("concept_id")
                if cid and cid != "NEW":
                    uses.append({
                        "concept_id": cid,
                        "title": tax.concepts.get(cid, {}).get("title", cid),
                        "stage": it.get("stage", ""),
                        "how_used": it.get("how_used", ""),
                        "source_excerpt": it.get("source_excerpt", ""),
                    })
            (CASES_DIR / f"{data['paper_id']}.json").write_text(json.dumps({
                "schema_version": 3,
                "paper_id": data["paper_id"],
                "meta": data.get("meta", {}),
                "concepts_used": uses,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[stage4] 案例库生成完毕：{len(list(CASES_DIR.glob('*.json')))} 篇")

    # 4b) 学习路径：通用主线（拓扑+难度）+ 三条角色支线
    diff_rank = {"Beginner": 0, "Intermediate": 1, "Advanced": 2}
    mod_order = [m["module_id"] for m in tax.raw.get("modules", [])]
    # 主线顺序：先入门(B)→数学基础(J)→工具(H)→方法(C)/统计(E)→求解(D)→验证(I)→可视化(G)→写作(A)→策略(F)
    main_seq = ["B", "J", "H", "E", "C", "D", "I", "G", "A", "F"]
    order_key = {m: i for i, m in enumerate(main_seq)}

    def node(cid):
        m = tax.concepts[cid]
        return {"concept_id": cid, "title": m["title"],
                "difficulty": m.get("difficulty", ""), "module_id": m["module_id"]}

    def sort_ids(ids):
        return sorted(ids, key=lambda c: (
            order_key.get(tax.concepts[c]["module_id"], 99),
            tax.concepts[c]["subcat_id"],
            diff_rank.get(tax.concepts[c].get("difficulty", "Beginner"), 0),
        ))

    all_ids = tax.all_ids()
    main_line = [node(c) for c in sort_ids(all_ids)]
    role_lines = {}
    for role in ["modeler", "coder", "writer"]:
        ids = [c for c in all_ids if role in (tax.concepts[c].get("roles") or [])]
        role_lines[role] = [node(c) for c in sort_ids(ids)]

    LEARNING_PATHS_PATH.write_text(json.dumps({
        "schema_version": 3,
        "module_order": mod_order,
        "main_line": main_line,
        "role_lines": role_lines,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[stage4] 学习路径生成完毕：主线 {len(main_line)} 节点，"
          f"角色支线 {{ {', '.join(f'{k}:{len(v)}' for k,v in role_lines.items())} }}")


# ════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="知识库重构流水线 v3（DeepSeek 做苦力）")
    ap.add_argument("--api-key", default=os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
                    help="API key（或设环境变量 LLM_API_KEY / DEEPSEEK_API_KEY）")
    ap.add_argument("--base-url", default=os.getenv("LLM_BASE_URL", DEEPSEEK_BASE_URL))
    ap.add_argument("--model", default=os.getenv("LLM_MODEL", "deepseek-chat"))
    ap.add_argument("--stage", default="all", choices=["1", "2", "3", "4", "all"])
    ap.add_argument("--limit", type=int, default=0, help="stage2 只处理前 N 篇（小批试跑）")
    ap.add_argument("--only", default="", help="stage2 限定 paper_id / stage3 限定 concept_id，逗号分隔")
    ap.add_argument("--force", action="store_true", help="覆盖已有产物")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}

    # stage4 不需要 API
    needs_api = args.stage in ("1", "2", "3", "all")
    client = None
    if needs_api:
        if not args.api_key:
            print("[错误] 缺少 API key。用 --api-key 或环境变量 LLM_API_KEY 提供。")
            sys.exit(1)
        from openai import OpenAI
        client = OpenAI(api_key=args.api_key, base_url=args.base_url)

    if not TAXONOMY_PATH.exists():
        print(f"[错误] 找不到主索引 {TAXONOMY_PATH}")
        sys.exit(1)
    tax = load_taxonomy()
    print(f"主索引已加载：{len(tax.all_ids())} 个 concept_id\n")

    if args.stage in ("1", "all"):
        stage1_clean_lectures(client, args.model, tax, args.force)
    if args.stage in ("2", "all"):
        stage2_extract_papers(client, args.model, tax, args.force, args.limit, only)
    if args.stage in ("3", "all"):
        stage3_synthesize(client, args.model, tax, args.force, only)
    if args.stage in ("4", "all"):
        stage4_cases_and_paths(tax)

    print("\n完成。产物目录：", KB_DIR)


if __name__ == "__main__":
    main()

"""真题题库：题目 JSON 存于 data/problems/<id>.json。

schema v2（动态框架）：题目存题面 + 元信息 + 数据文件 + 论文全文缓存指针；
每个 step 由建模框架阶段实例化而来，含 stage_key / modality / criteria
（评分维度）/ reference_outline（参考标尺）/ paper_points（优秀论文这一步怎么做）。

兼容 v1（旧模板）：旧题用 rubric / reference_answer，无 stage_key / modality；
读取层统一回退，保证迁移期与旧 demo 题不挂。

对外暴露：
  list_all()        题库列表，仅元信息
  load_public(id)   单题，剔除 criteria 细则 / reference_outline / paper_points（给学生看）
  load_full(id)     单题完整（仅服务端内部用，绝不返回前端）
  get_step(id, sid) 单步完整（含 criteria / reference_outline / paper_points / modality）
  get_paper_points(id, sid)        某步的论文阶段要点
  load_paper_fulltext(id, paper_id) 读论文全文缓存
  search_papers(id, query)         在论文全文里朴素检索片段（追问时实时摘录）
"""
import json
import logging
from pathlib import Path

from . import framework
from .config import config

logger = logging.getLogger(__name__)


def _path(problem_id: str) -> Path:
    """题目 JSON 路径：先找静态题库，再回退动态题目目录（工作台做题模式生成）。"""
    safe = "".join(c for c in problem_id if c.isalnum() or c in "-_")
    static = config.PROBLEMS_DIR / f"{safe}.json"
    if static.exists():
        return static
    dynamic = config.DYNAMIC_PROBLEMS_DIR / f"{safe}.json"
    if dynamic.exists():
        return dynamic
    return static  # 都不存在时返回静态路径占位（调用方按 exists() 判空）


def _load_raw(problem_id: str) -> dict | None:
    p = _path(problem_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("题目 %s 读取失败：%s", problem_id, e)
        return None


# ────────── step 兼容归一（v1 rubric / reference_answer → v2） ──────────

def _criteria_of(step: dict) -> list[dict]:
    """统一取评分维度：v2 用 criteria，v1 回退 rubric(point→dim)。"""
    crit = step.get("criteria")
    if crit:
        return [
            {"dim": c.get("dim", c.get("point", "")),
             "weight": c.get("weight", 0),
             "detail": c.get("detail", "")}
            for c in crit
        ]
    return [
        {"dim": r.get("point", ""), "weight": r.get("weight", 0), "detail": ""}
        for r in (step.get("rubric") or [])
    ]


def _reference_of(step: dict) -> str:
    return step.get("reference_outline") or step.get("reference_answer") or ""


def _modality_of(step: dict) -> str:
    m = step.get("modality")
    if m in framework.MODALITIES:
        return m
    return framework.modality_of(step.get("stage_key", ""))


def _step_max(step: dict) -> float:
    if step.get("max_score") is not None:
        return step.get("max_score", 0)
    return sum(c["weight"] for c in _criteria_of(step))


def _public_step(step: dict, idx: int) -> dict:
    """剔除评分细则 / 参考答案 / 论文要点，供学生做题。"""
    return {
        "id": step.get("id") or f"s{idx + 1}",
        "stage_key": step.get("stage_key", ""),
        "modality": _modality_of(step),
        "guide_style": step.get("guide_style", ""),
        "deliverable": step.get("deliverable", ""),
        "title": step.get("title", ""),
        "prompt": step.get("prompt", ""),
        "max_score": _step_max(step),
        "hint": step.get("hint", ""),
    }


def list_all() -> list[dict]:
    config.ensure_dirs()
    items = []
    for p in sorted(config.PROBLEMS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        steps = data.get("steps", []) or []
        items.append({
            "id": data.get("id", p.stem),
            "title": data.get("title", "未命名真题"),
            "year": data.get("year"),
            "contest": data.get("contest", ""),
            "difficulty": data.get("difficulty", ""),
            "tags": data.get("tags", []) or [],
            "step_count": len(steps),
            "has_data": bool(data.get("data_files")),
            "total_max_score": data.get("total_max_score")
                or sum(_step_max(s) for s in steps),
        })
    return items


def load_public(problem_id: str) -> dict | None:
    """剔除每步的 criteria 细则、reference_outline、paper_points，供学生做题。"""
    data = _load_raw(problem_id)
    if data is None:
        return None
    steps = data.get("steps", []) or []
    return {
        "id": data.get("id", problem_id),
        "schema_version": data.get("schema_version", 1),
        "title": data.get("title", "未命名真题"),
        "year": data.get("year"),
        "contest": data.get("contest", ""),
        "difficulty": data.get("difficulty", ""),
        "tags": data.get("tags", []) or [],
        "background": data.get("background", ""),
        "data_files": data.get("data_files", []) or [],
        "total_max_score": data.get("total_max_score")
            or sum(_step_max(s) for s in steps),
        "steps": [_public_step(s, i) for i, s in enumerate(steps)],
    }


def load_full(problem_id: str) -> dict | None:
    """完整题目（含 criteria / reference_outline / paper_points），仅服务端内部用。"""
    return _load_raw(problem_id)


def get_step(problem_id: str, step_id: str) -> dict | None:
    """单步完整数据，归一为 v2 字段（criteria / reference_outline / modality / paper_points）。"""
    data = _load_raw(problem_id)
    if data is None:
        return None
    for i, s in enumerate(data.get("steps", []) or []):
        if (s.get("id") or f"s{i + 1}") == step_id:
            return {
                "id": step_id,
                "stage_key": s.get("stage_key", ""),
                "modality": _modality_of(s),
                "guide_style": s.get("guide_style", ""),
                "deliverable": s.get("deliverable", ""),
                "title": s.get("title", ""),
                "prompt": s.get("prompt", ""),
                "max_score": _step_max(s),
                "hint": s.get("hint", ""),
                "criteria": _criteria_of(s),
                "reference_outline": _reference_of(s),
                "paper_points": s.get("paper_points", []) or [],
            }
    return None


def total_max(problem_id: str) -> float:
    data = _load_raw(problem_id)
    if data is None:
        return 0
    return data.get("total_max_score") or sum(
        _step_max(s) for s in (data.get("steps") or [])
    )


def get_paper_points(problem_id: str, step_id: str) -> list[dict]:
    step = get_step(problem_id, step_id)
    return step.get("paper_points", []) if step else []


def resolve_data_file(problem_id: str, filename: str) -> Path | None:
    """把数据文件名解析到该题资产目录内的真实路径；越界或不存在返回 None。

    防路径穿越：只取 basename、resolve() 后用 relative_to 校验仍在资产目录内。
    """
    safe_pid = "".join(c for c in problem_id if c.isalnum() or c in "-_")
    asset_dir = (config.PROBLEM_ASSETS_DIR / safe_pid).resolve()
    name = (filename or "").replace("\\", "/").split("/")[-1]
    if not name:
        return None
    target = (asset_dir / name).resolve()
    try:
        target.relative_to(asset_dir)
    except ValueError:
        return None
    return target if target.is_file() else None


# ────────── 优秀论文全文缓存（对照 / 追问实时摘录） ──────────

def _resolve_paper(problem_id: str, paper_id: str) -> Path | None:
    """把 paper_id 解析到该题论文缓存目录内的 <paper_id>.txt；防路径穿越。"""
    safe_pid = "".join(c for c in problem_id if c.isalnum() or c in "-_")
    paper_dir = (config.PROBLEM_PAPERS_DIR / safe_pid).resolve()
    name = (paper_id or "").replace("\\", "/").split("/")[-1]
    if not name:
        return None
    target = (paper_dir / f"{name}.txt").resolve()
    try:
        target.relative_to(paper_dir)
    except ValueError:
        return None
    return target if target.is_file() else None


def load_paper_fulltext(problem_id: str, paper_id: str) -> str | None:
    target = _resolve_paper(problem_id, paper_id)
    if target is None:
        return None
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return None


def list_papers(problem_id: str) -> list[dict]:
    data = _load_raw(problem_id)
    return (data.get("papers", []) or []) if data else []


def search_papers(problem_id: str, query: str, max_chars: int = 4000,
                  per_paper: int = 2, window: int = 700) -> list[dict]:
    """在该题各论文全文里朴素检索关键词片段，供追问时实时摘录。

    无需向量库：按 query 切出的关键词在全文里命中，截取命中处前后 window
    字符的窗口。每篇最多 per_paper 段，总长不超过 max_chars。
    """
    papers = list_papers(problem_id)
    terms = [t for t in (query or "").split() if len(t) >= 2] or [query.strip()]
    terms = [t for t in terms if t]
    out: list[dict] = []
    used = 0
    for pp in papers:
        pid = pp.get("paper_id", "")
        text = load_paper_fulltext(problem_id, pid)
        if not text:
            continue
        low = text.lower()
        excerpts: list[str] = []
        seen_pos: list[int] = []
        for term in terms:
            pos = low.find(term.lower())
            if pos == -1:
                continue
            if any(abs(pos - s) < window for s in seen_pos):
                continue
            seen_pos.append(pos)
            start = max(0, pos - window // 2)
            seg = text[start:start + window].strip()
            excerpts.append(seg)
            if len(excerpts) >= per_paper:
                break
        if excerpts:
            joined = "\n…\n".join(excerpts)
            if used + len(joined) > max_chars:
                joined = joined[: max(0, max_chars - used)]
            if joined:
                out.append({"paper_id": pid, "excerpt": joined})
                used += len(joined)
        if used >= max_chars:
            break
    return out

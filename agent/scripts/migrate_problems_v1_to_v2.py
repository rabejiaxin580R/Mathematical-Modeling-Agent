"""把现有 v1 题库 JSON 迁移到 v2（动态框架）结构。开发者本地一次性运行。

迁移内容（不调用 LLM，纯结构转换 + 启发式映射）：
  - rubric[{point,weight}]      → criteria[{dim,weight,detail=""}]
  - reference_answer            → reference_outline
  - 按步骤标题关键词 + 顺序     → stage_key，并由框架带出 modality
  - 新增 paper_points=[] / papers=[] / review.curated=false / schema_version=2

迁移结果是「起点」，stage_key / modality / paper_points 之后可人工校对或用
build_problems.py 重录补全。

用法：
  python scripts/migrate_problems_v1_to_v2.py            # 全部，跳过已是 v2 的
  python scripts/migrate_problems_v1_to_v2.py --force    # 含已迁移的也重转
  python scripts/migrate_problems_v1_to_v2.py --only prob_2023_A
  python scripts/migrate_problems_v1_to_v2.py --dry-run  # 只打印不写盘
"""
import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(AGENT_DIR))

from backend import framework  # noqa: E402

PROBLEMS_DIR = AGENT_DIR / "data" / "problems"

# 关键词 → stage_key（按特异性从高到低匹配，先匹配到的优先）
_KEYWORD_RULES = [
    (("灵敏度", "敏感性", "敏感度"), "sensitivity"),
    (("推广", "写作", "摘要", "论文"), "extend"),
    (("评价", "优缺点", "优点", "缺点", "局限"), "evaluate"),
    (("结果分析", "结果检验", "检验", "验证", "拟合优度", "结果"), "analyze"),
    (("求解", "算法", "模拟", "仿真", "编程", "代码", "计算", "数值"), "solve"),
    (("符号", "变量定义", "变量说明", "参数说明"), "notation"),
    (("建立", "构建", "建模", "公式", "方程", "模型设计"), "build"),
    (("假设",), "assume"),
    (("重述", "问题分析", "问题理解", "问题描述", "背景分析"), "restate"),
]


def guess_stage_key(title: str, idx: int, n_steps: int) -> str:
    t = title or ""
    for keywords, key in _KEYWORD_RULES:
        if any(k in t for k in keywords):
            return key
    # 兜底：按相对位置落到框架阶段
    keys = framework.stage_keys()
    if n_steps <= 0:
        return keys[0]
    pos = int(idx / max(1, n_steps) * len(keys))
    return keys[min(pos, len(keys) - 1)]


def migrate_step(step: dict, idx: int, n_steps: int) -> dict:
    rubric = step.get("rubric") or step.get("criteria") or []
    criteria = []
    for r in rubric:
        criteria.append({
            "dim": r.get("dim", r.get("point", "")),
            "weight": r.get("weight", 0),
            "detail": r.get("detail", ""),
        })
    stage_key = step.get("stage_key") or guess_stage_key(
        step.get("title", ""), idx, n_steps)
    return {
        "id": step.get("id") or f"s{idx + 1}",
        "stage_key": stage_key,
        "modality": step.get("modality") or framework.modality_of(stage_key),
        "guide_style": step.get("guide_style", "")
            or (framework.get_stage(stage_key) or {}).get("guide_style", ""),
        "deliverable": step.get("deliverable", "")
            or (framework.get_stage(stage_key) or {}).get("deliverable", ""),
        "title": step.get("title", ""),
        "prompt": step.get("prompt", ""),
        "max_score": step.get("max_score", sum(c["weight"] for c in criteria)),
        "criteria": criteria,
        "reference_outline": step.get("reference_outline")
            or step.get("reference_answer", ""),
        "hint": step.get("hint", ""),
        "paper_points": step.get("paper_points", []) or [],
    }


def migrate(data: dict) -> dict:
    steps_in = data.get("steps", []) or []
    n = len(steps_in)
    steps = [migrate_step(s, i, n) for i, s in enumerate(steps_in)]
    out = dict(data)
    out["schema_version"] = 2
    out["steps"] = steps
    out["papers"] = data.get("papers", []) or []
    out["review"] = data.get("review") or {"curated": False}
    out["total_max_score"] = data.get("total_max_score") or sum(
        s["max_score"] for s in steps)
    return out


def main():
    ap = argparse.ArgumentParser(description="v1→v2 题库迁移")
    ap.add_argument("--only", default="", help="只迁移指定题 id（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="含已是 v2 的也重转")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    files = sorted(PROBLEMS_DIR.glob("*.json"))
    done, skipped = [], []

    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[跳过] {p.name} 读取失败：{e}")
            continue
        pid = data.get("id", p.stem)
        if only and pid not in only:
            continue
        if data.get("schema_version") == 2 and not args.force:
            print(f"[跳过] {pid} 已是 v2（--force 可重转）")
            skipped.append(pid)
            continue

        out = migrate(data)
        stages = ", ".join(f"{s['stage_key']}/{s['modality']}" for s in out["steps"])
        print(f"[迁移] {pid}  {len(out['steps'])} 步：{stages}")
        if not args.dry_run:
            p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        done.append(pid)

    print(f"\n完成 {len(done)}，跳过 {len(skipped)}。" + (" （dry-run 未写盘）" if args.dry_run else ""))


if __name__ == "__main__":
    main()

"""真题录入程序 v2：把 assets/real_problem/ 下的 HiMCM/MidMCM 真题转成动态框架题库 JSON。

由开发者本地运行，用 DeepSeek API 生成（省钱）。每道题：
  - MarkItDown 抽取题面 PDF（全量）
  - MarkItDown 全量抽取每篇获奖论文 → 落盘 data/problem_papers/<id>/<paper_id>.txt
  - 两阶段 LLM：
      ① 阶段实例化：针对「建模框架」每个阶段，生成本题的 prompt / criteria /
         reference_outline / hint（按 modality 给不同侧重）
      ② 论文阶段要点蒸馏：每篇论文 × 每阶段，蒸馏「这一步优秀论文怎么做」要点
  - 拷贝附带数据文件到 data/problem_assets/<id>/，写 data_files 字段
  - 写出 data/problems/prob_<year>_<letter>.json（schema_version=2）

英文题面（保留 HiMCM 原文）+ 中文步骤/criteria/参考标尺/论文要点。

用法：
  python scripts/build_problems.py --api-key sk-xxx
  python scripts/build_problems.py --api-key sk-xxx --only 2023A,2024B --force
  $env:DEEPSEEK_API_KEY="sk-xxx"; python scripts/build_problems.py

密钥仅运行时传入，绝不写入源码 / 题库 / 日志。
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(AGENT_DIR))

from backend import framework  # noqa: E402

REAL_PROBLEM_DIR = AGENT_DIR / "assets" / "real_problem"
PROBLEMS_DIR = AGENT_DIR / "data" / "problems"
ASSETS_DIR = AGENT_DIR / "data" / "problem_assets"
PAPERS_DIR = AGENT_DIR / "data" / "problem_papers"

DATA_EXTS = {".xlsx", ".xls", ".csv"}
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

_md = None


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


def discover_problems():
    """遍历 assets/real_problem/<year>/<letter>/，产出待处理题目列表。"""
    found = []
    if not REAL_PROBLEM_DIR.exists():
        print(f"[错误] 找不到题库目录：{REAL_PROBLEM_DIR}")
        return found
    for year_dir in sorted(REAL_PROBLEM_DIR.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for letter_dir in sorted(year_dir.iterdir()):
            if not letter_dir.is_dir():
                continue
            problem_pdf = None
            papers, data_files = [], []
            for f in sorted(letter_dir.iterdir()):
                if not f.is_file():
                    continue
                if f.suffix.lower() == ".pdf" and "problem_" in f.name.lower():
                    problem_pdf = f
                elif f.suffix.lower() == ".pdf" and f.stem.isdigit():
                    papers.append(f)
                elif f.suffix.lower() in DATA_EXTS:
                    data_files.append(f)
            if problem_pdf is None:
                print(f"[跳过] {year_dir.name}/{letter_dir.name}：无题面 PDF")
                continue
            contest = "MidMCM" if "midmcm" in problem_pdf.name.lower() else "HiMCM"
            found.append({
                "year": int(year_dir.name),
                "letter": letter_dir.name,
                "contest": contest,
                "id": f"prob_{year_dir.name}_{letter_dir.name}",
                "problem_pdf": problem_pdf,
                "papers": papers,
                "data_files": data_files,
            })
    return found


# ────────── 框架描述（喂给模型） ──────────

_MODALITY_DESC = {
    "key-points": "要点拆解：让学生分点列举，重点考查要点覆盖与区分度",
    "formula": "公式建模：让学生写出符号定义与数学公式，重点考查表达正确与符号一致",
    "code": "编程求解：让学生写出算法思路或代码，重点考查逻辑可运行与结果合理",
    "prose": "分析论述：让学生展开论证，重点考查论证完整与结论有据",
}


def framework_brief() -> str:
    lines = []
    for i, s in enumerate(framework.FRAMEWORK):
        lines.append(
            f"{i+1}. stage_key={s['key']}  名称={s['name']}  "
            f"modality={s['modality']}（{_MODALITY_DESC.get(s['modality'], '')}）  "
            f"教学目标={s['goal']}  建议分值≈{s['default_weight']}"
        )
    return "\n".join(lines)


# ────────── 阶段实例化 ──────────

SYSTEM_PROMPT_STEPS = """你是数学建模竞赛命题与教学专家。给你一道真实英文数学建模竞赛题（HiMCM/MidMCM）和一套固定的「建模框架阶段」，请针对本题，把每个阶段实例化成面向中文初学者的分步练习。

要求：
1. 只输出一个合法 JSON 对象，不要任何额外文字或代码块标记。
2. JSON 结构严格如下：
{
  "title": "简短中文标题（概括这道题在做什么，不超过20字）",
  "difficulty": "Beginner | Intermediate | Advanced",
  "tags": ["中文模型/方法标签", "..."],
  "steps": [
    {
      "stage_key": "必须与给定阶段的 stage_key 完全一致",
      "title": "结合本题的中文步骤标题",
      "prompt": "用中文写清这一步要学生做什么、产出什么（可含 $LaTeX$）。要针对本题、贴合该阶段的 modality 侧重。",
      "max_score": 整数,
      "criteria": [ {"dim": "中文评分维度", "weight": 整数, "detail": "可评判的细则，可空"}, ... ],
      "reference_outline": "中文参考标尺，给出这一步标准做法与关键结论，简洁，不超过250字",
      "hint": "中文提示，不直接泄露答案"
    }
  ]
}
3. steps 必须**逐一覆盖给定的每个阶段，顺序、数量、stage_key 与给定阶段完全一致**。
4. 每步要贴合该阶段的 modality：formula 步要让学生写公式/符号；code 步要让学生写算法/代码思路；key-points 步要让学生分点拆解；prose 步要让学生展开分析。
5. 每步 criteria 各条 weight 之和必须等于该步 max_score；所有步骤 max_score 之和必须等于 100（可参考各阶段建议分值）。
6. 题面（background）由程序保留英文原文，你不要生成。所有中文字段务必用中文。"""


def build_user_prompt_steps(item, problem_text):
    return "\n".join([
        f"赛事：{item['contest']}  年份：{item['year']}  题号：{item['letter']} 题",
        "",
        "=== 建模框架阶段（必须逐一实例化，stage_key 不可改） ===",
        framework_brief(),
        "",
        "=== 英文题面原文 ===",
        problem_text,
        "",
        "请按 system 的要求输出 JSON。",
    ])


# ────────── 论文阶段要点蒸馏 ──────────

SYSTEM_PROMPT_PAPER = """你是数学建模竞赛阅卷与教学专家。给你一篇获奖论文的正文，以及一套建模框架阶段。请提炼「这篇论文在每个阶段具体怎么做的」要点，供教学对照。

要求：
1. 只输出一个合法 JSON 对象，不要任何额外文字或代码块标记。
2. 结构：{"by_stage": {"<stage_key>": ["要点1", "要点2"], ...}}
3. key 必须用给定的 stage_key；每阶段 1-4 条要点，中文，具体（点出用了什么模型/方法/关键处理），简洁。
4. 论文没明显涉及的阶段，给空数组 []。不要编造论文里没有的内容。"""


def build_user_prompt_paper(paper_text):
    return "\n".join([
        "=== 建模框架阶段（stage_key 列表） ===",
        framework_brief(),
        "",
        "=== 获奖论文正文（可能截断） ===",
        paper_text,
        "",
        "请按 system 的要求输出 JSON。",
    ])


# ────────── LLM 调用 ──────────

def _coerce_int(v, default=0):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def parse_json(text: str) -> dict | None:
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
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    out = parse_json(_do())
    if out is None:
        out = parse_json(_do("\n\n再次强调：只输出合法 JSON。" + retry_hint))
    return out


# ────────── 归一 v2 ──────────

def _largest_remainder(raw: list[int], target: int) -> list[int]:
    """把 raw 按比例缩放到和=target，最大余数法取整。"""
    total = sum(raw) or 1
    scaled = [v * target / total for v in raw]
    floors = [int(x) for x in scaled]
    rem = target - sum(floors)
    order = sorted(range(len(scaled)), key=lambda k: scaled[k] - floors[k], reverse=True)
    for k in order[:max(0, rem)]:
        floors[k] += 1
    return floors


def normalize_v2(gen, item, background, data_files, papers_meta, paper_points_by_paper):
    """按框架阶段对齐模型输出，组装 v2 题目 JSON。"""
    gen_steps = {s.get("stage_key"): s for s in (gen.get("steps", []) or [])}

    steps = []
    for i, stage in enumerate(framework.FRAMEWORK):
        key = stage["key"]
        g = gen_steps.get(key, {})
        criteria = []
        for c in g.get("criteria", []) or []:
            criteria.append({
                "dim": str(c.get("dim", c.get("point", ""))).strip(),
                "weight": _coerce_int(c.get("weight", 0)),
                "detail": str(c.get("detail", "")).strip(),
            })
        if not criteria:
            criteria = [{"dim": stage["deliverable"], "weight": stage["default_weight"], "detail": ""}]
        # 该步论文要点：从各论文的 by_stage[key] 收集
        paper_points = []
        for pid, by_stage in paper_points_by_paper.items():
            pts = [str(x).strip() for x in (by_stage.get(key) or []) if str(x).strip()]
            if pts:
                paper_points.append({"paper_id": pid, "points": pts})

        steps.append({
            "id": f"s{i+1}",
            "stage_key": key,
            "modality": stage["modality"],
            "guide_style": stage["guide_style"],
            "deliverable": stage["deliverable"],
            "title": str(g.get("title", stage["name"])).strip() or stage["name"],
            "prompt": str(g.get("prompt", "")).strip(),
            "max_score": _coerce_int(g.get("max_score", stage["default_weight"])),
            "criteria": criteria,
            "reference_outline": str(g.get("reference_outline", g.get("reference_answer", ""))).strip(),
            "hint": str(g.get("hint", "")).strip(),
            "paper_points": paper_points,
        })

    # 阶段 max_score 归一到 100
    floors = _largest_remainder([max(0, s["max_score"]) for s in steps], 100)
    for s, ms in zip(steps, floors):
        s["max_score"] = ms
        wfloors = _largest_remainder([max(0, c["weight"]) for c in s["criteria"]], ms)
        for c, w in zip(s["criteria"], wfloors):
            c["weight"] = w

    return {
        "schema_version": 2,
        "id": item["id"],
        "title": str(gen.get("title", "")).strip() or f"{item['year']} {item['contest']} {item['letter']} 题",
        "year": item["year"],
        "contest": item["contest"],
        "difficulty": gen.get("difficulty", "Advanced"),
        "tags": [str(t).strip() for t in (gen.get("tags") or []) if str(t).strip()],
        "background": background,
        "data_files": data_files,
        "papers": papers_meta,
        "total_max_score": 100,
        "steps": steps,
        "review": {"curated": False},
    }


def copy_data_files(item) -> list[dict]:
    out = []
    if not item["data_files"]:
        return out
    dest = ASSETS_DIR / item["id"]
    dest.mkdir(parents=True, exist_ok=True)
    for f in item["data_files"]:
        shutil.copy2(f, dest / f.name)
        out.append({"name": f.name, "filename": f.name})
    return out


def save_papers_fulltext(item, max_papers: int) -> list[dict]:
    """全量抽取论文落盘 data/problem_papers/<id>/<paper_id>.txt，返回 papers 元信息。"""
    out = []
    papers = item["papers"][:max_papers]
    if not papers:
        return out
    dest = PAPERS_DIR / item["id"]
    dest.mkdir(parents=True, exist_ok=True)
    for pf in papers:
        text = extract_text(pf)
        if not text:
            continue
        pid = pf.stem
        (dest / f"{pid}.txt").write_text(text, encoding="utf-8")
        out.append({
            "paper_id": pid,
            "filename": pf.name,
            "title": "",
            "fulltext_path": f"data/problem_papers/{item['id']}/{pid}.txt",
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="真题录入程序 v2（DeepSeek 生成动态框架题库 JSON）")
    ap.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY", ""),
                    help="DeepSeek API key（或设环境变量 DEEPSEEK_API_KEY）")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--only", default="", help="只处理指定题，逗号分隔，如 2023A,2024B")
    ap.add_argument("--force", action="store_true", help="覆盖已生成的题库 JSON")
    ap.add_argument("--max-papers", type=int, default=3, help="每题最多取几篇论文")
    ap.add_argument("--paper-chars", type=int, default=12000, help="蒸馏论文要点时每篇截断字符数")
    args = ap.parse_args()

    if not args.api_key:
        print("[错误] 缺少 API key。用 --api-key 或环境变量 DEEPSEEK_API_KEY 提供。")
        sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=args.api_key, base_url=DEEPSEEK_BASE_URL)

    only = set()
    if args.only:
        for tok in args.only.split(","):
            tok = tok.strip().upper()
            m = re.match(r"(\d{4})([A-Z])", tok)
            if m:
                only.add(f"prob_{m.group(1)}_{m.group(2)}")

    PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    items = discover_problems()
    if only:
        items = [it for it in items if it["id"] in only]

    print(f"\n发现 {len(items)} 道待处理题目。\n")
    done, skipped, failed = [], [], []

    for item in items:
        out_path = PROBLEMS_DIR / f"{item['id']}.json"
        if out_path.exists() and not args.force:
            print(f"[跳过] {item['id']} 已存在（--force 可覆盖）")
            skipped.append(item["id"])
            continue

        print(f"[处理] {item['id']}  ({item['contest']} {item['year']} {item['letter']}，"
              f"{len(item['papers'])} 篇论文，{len(item['data_files'])} 个数据文件)")
        problem_text = extract_text(item["problem_pdf"])
        if not problem_text:
            print("    [失败] 题面抽取为空，跳过")
            failed.append(item["id"])
            continue

        # 论文全量落盘
        print("    抽取论文全文…")
        papers_meta = save_papers_fulltext(item, args.max_papers)

        # ① 阶段实例化
        print("    生成框架阶段…")
        try:
            gen = call_json(
                client, args.model, SYSTEM_PROMPT_STEPS,
                build_user_prompt_steps(item, problem_text),
                retry_hint=" steps 必须覆盖全部阶段，stage_key 一致，分值和=100。")
        except Exception as e:
            print(f"    [失败] 阶段实例化调用出错：{e}")
            failed.append(item["id"])
            continue
        if not gen or not gen.get("steps"):
            print("    [失败] 模型未返回有效 steps")
            failed.append(item["id"])
            continue

        # ② 论文阶段要点蒸馏（每篇一次）
        paper_points_by_paper = {}
        for pm in papers_meta:
            pid = pm["paper_id"]
            ptext = (PAPERS_DIR / item["id"] / f"{pid}.txt").read_text(encoding="utf-8")[:args.paper_chars]
            print(f"    蒸馏论文 {pid} 阶段要点…")
            try:
                res = call_json(
                    client, args.model, SYSTEM_PROMPT_PAPER,
                    build_user_prompt_paper(ptext), max_tokens=4000)
            except Exception as e:
                print(f"      [警告] 论文 {pid} 蒸馏失败：{e}")
                res = None
            if res and isinstance(res.get("by_stage"), dict):
                paper_points_by_paper[pid] = res["by_stage"]

        data_files = copy_data_files(item)
        result = normalize_v2(gen, item, problem_text, data_files, papers_meta, paper_points_by_paper)

        n_steps = len(result["steps"])
        n_pp = sum(len(s["paper_points"]) for s in result["steps"])
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    [完成] {n_steps} 步，{len(papers_meta)} 篇论文，{n_pp} 处论文要点 → {out_path.name}")
        done.append(item["id"])

    print("\n===== 汇总 =====")
    print(f"成功 {len(done)}：{', '.join(done) or '无'}")
    print(f"跳过 {len(skipped)}：{', '.join(skipped) or '无'}")
    print(f"失败 {len(failed)}：{', '.join(failed) or '无'}")
    if failed:
        (PROBLEMS_DIR / "_failed.log").write_text("\n".join(failed), encoding="utf-8")


if __name__ == "__main__":
    main()

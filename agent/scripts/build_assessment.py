"""入门测评题库 + 分级学习路径生成器（开发者离线运行，DeepSeek 出题省钱）。

产出两份文件，供学习模式「入门测评 / 分级学习路径」消费：
  - data/assessment/questions.json  ：单选题题库（每题 4 选项 1 正确 + 解析 + 来源知识点）
  - data/assessment/paths.json      ：L1–L5 五个等级各一条推荐学习路径（里程碑指向真实知识点）

出题：遍历 data/knowledge/concepts/*.json，按模块配额抽样概念，每个概念让 DeepSeek
      生成 1 道高质量单选题（紧扣该知识点，4 选项、1 正确、3 干扰项、一句话解析）。
路径：把 10 模块 + 各模块概念清单喂 DeepSeek，为 L1–L5 各生成一条由浅入深的路线
      （milestones：标题/为什么/指向的 module_ids 与 concept_ids）。脚本校验 concept_id
      真实存在并过滤非法项，避免前端点击 404。

中文输出。密钥仅运行时传入，绝不写入源码 / 产物 / 日志。

用法：
  python scripts/build_assessment.py --api-key sk-xxx
  python scripts/build_assessment.py --api-key sk-xxx --only-questions --force
  python scripts/build_assessment.py --api-key sk-xxx --only-paths --force
  $env:DEEPSEEK_API_KEY="sk-xxx"; python scripts/build_assessment.py
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(AGENT_DIR))

# Windows 控制台默认 GBK，打印 ✓/✗ 或中文标题会 UnicodeEncodeError，强制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CONCEPTS_DIR = AGENT_DIR / "data" / "knowledge" / "concepts"
OUT_DIR = AGENT_DIR / "data" / "assessment"
QUESTIONS_PATH = OUT_DIR / "questions.json"
PATHS_PATH = OUT_DIR / "paths.json"

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 五个等级（与后端 assessment.py / 前端保持一致）
LEVELS = [
    {"level": "L1", "name": "萌新"},
    {"level": "L2", "name": "入门"},
    {"level": "L3", "name": "进阶"},
    {"level": "L4", "name": "熟练"},
    {"level": "L5", "name": "高手"},
]

# 出题总量与各模块大致配额（按模块概念数与重要性，总计约 55 题）。
# 模块概念数：A16 B9 C41 D16 E16 F11 G9 H13 I12 J8。C（常用建模方法）是核心，配额最高。
MODULE_QUOTA = {
    "A": 5, "B": 3, "C": 14, "D": 6, "E": 6,
    "F": 3, "G": 3, "H": 5, "I": 5, "J": 5,
}

# 难度加权分值
DIFF_WEIGHT = {"Beginner": 1, "Intermediate": 2, "Advanced": 3}


# ────────── 概念加载 ──────────

def load_concepts() -> list[dict]:
    """读取全部概念页，返回精简 dict 列表。"""
    out = []
    for f in sorted(CONCEPTS_DIR.glob("*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [警告] 跳过 {f.name}: {e}")
            continue
        if not j.get("concept_id"):
            continue
        tax = j.get("taxonomy_path") or []
        ex = j.get("explain") or {}
        out.append({
            "concept_id": j["concept_id"],
            "title": j.get("title", ""),
            "module_id": j.get("module_id", "") or (tax[0] if tax else "?"),
            "module_name": tax[0] if tax else (j.get("category", "")),
            "subcat_name": tax[1] if len(tax) > 1 else "",
            "difficulty": j.get("difficulty", "Intermediate"),
            "one_liner": ex.get("one_liner", ""),
            "definition": ex.get("definition", ""),
            "when_to_use": ex.get("when_to_use", ""),
        })
    return out


def sample_for_questions(concepts: list[dict]) -> list[dict]:
    """按模块配额 + 难度梯度抽样要出题的概念。"""
    by_mod: dict[str, list[dict]] = {}
    for c in concepts:
        by_mod.setdefault(c["module_id"], []).append(c)

    chosen = []
    for mid, quota in MODULE_QUOTA.items():
        pool = by_mod.get(mid, [])
        if not pool:
            continue
        # 按难度分桶，尽量在三档间均衡取，让题目覆盖梯度
        buckets = {"Beginner": [], "Intermediate": [], "Advanced": []}
        for c in pool:
            buckets.get(c["difficulty"], buckets["Intermediate"]).append(c)
        for b in buckets.values():
            random.shuffle(b)
        order = ["Beginner", "Intermediate", "Advanced"]
        picked = []
        i = 0
        while len(picked) < min(quota, len(pool)):
            b = buckets[order[i % 3]]
            if b:
                picked.append(b.pop())
            elif not any(buckets.values()):
                break
            i += 1
        chosen.extend(picked)
    return chosen


# ────────── JSON 解析 / LLM 调用（沿用 build_problems 模式） ──────────

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


def call_json(client, model, system, user, max_tokens=2000, retry_hint=""):
    def _do(extra_sys=""):
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": system + extra_sys},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
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


# ────────── ① 出题 ──────────

SYSTEM_QUESTION = """你是数学建模竞赛的命题与教学专家。给你一个数学建模知识点的资料，请围绕它出 1 道高质量的中文单项选择题，用于测评学生对该知识点的掌握程度。

只输出一个合法 JSON 对象，不要任何额外文字或代码块标记，结构严格如下：
{
  "stem": "题干。中文，紧扣这个知识点，考查理解或应用而非死记；可含 $LaTeX$ 公式。",
  "options": ["选项A", "选项B", "选项C", "选项D"],
  "answer": 正确选项的下标(0-3的整数),
  "explain": "一句话解析，说明为什么这个答案对（中文，简洁）。"
}

要求：
1. 恰好 4 个选项，有且仅有 1 个正确；3 个干扰项要合理、有迷惑性，不要明显荒谬。
2. 题目难度与给定知识点难度匹配。考点要落在这个知识点本身。
3. 选项不要出现「以上都对/以上都不对」这类偷懒选项。选项文字不带 A/B/C/D 前缀。
4. 全部用简体中文（专有名词、变量、公式可保留原文）。"""


def build_question_user(c: dict) -> str:
    return "\n".join([
        f"知识点标题：{c['title']}",
        f"所属模块：{c['module_name']}  子类：{c['subcat_name']}  难度：{c['difficulty']}",
        f"一句话理解：{c['one_liner']}",
        f"定义：{c['definition'][:600]}",
        f"什么时候用：{c['when_to_use'][:400]}",
        "",
        "请按 system 要求，围绕这个知识点出 1 道单选题，只输出 JSON。",
    ])


def normalize_question(gen: dict, c: dict, qid: str) -> dict | None:
    """校验并归一一道题；非法返回 None。"""
    if not isinstance(gen, dict):
        return None
    stem = str(gen.get("stem", "")).strip()
    opts = gen.get("options") or []
    opts = [str(o).strip() for o in opts if str(o).strip()]
    try:
        answer = int(gen.get("answer"))
    except (TypeError, ValueError):
        return None
    if not stem or len(opts) != 4 or not (0 <= answer <= 3):
        return None
    return {
        "id": qid,
        "module_id": c["module_id"],
        "difficulty": c["difficulty"],
        "concept_id": c["concept_id"],
        "concept_title": c["title"],
        "stem": stem,
        "options": opts,
        "answer": answer,
        "explain": str(gen.get("explain", "")).strip(),
    }


def generate_questions(client, model) -> dict:
    concepts = load_concepts()
    print(f"加载概念 {len(concepts)} 个。")
    picks = sample_for_questions(concepts)
    print(f"按模块配额抽样 {len(picks)} 个概念出题。\n")

    questions = []
    failed = 0
    for i, c in enumerate(picks):
        qid = f"q{i + 1:03d}"
        print(f"[{i + 1}/{len(picks)}] {c['module_id']} · {c['title']}（{c['difficulty']}）", end=" ")
        try:
            gen = call_json(client, model, SYSTEM_QUESTION, build_question_user(c),
                            retry_hint=" 必须恰好4个选项、answer为0-3整数。")
        except Exception as e:
            print(f"✗ 调用出错：{e}")
            failed += 1
            continue
        q = normalize_question(gen, c, qid)
        if q is None:
            print("✗ 结构非法，跳过")
            failed += 1
            continue
        questions.append(q)
        print("✓")

    return {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "diff_weight": DIFF_WEIGHT,
        "count": len(questions),
        "failed": failed,
        "questions": questions,
    }


# ────────── ② 学习路径 ──────────

SYSTEM_PATHS = """你是数学建模竞赛的教学规划专家。给你一套数学建模知识库的模块结构（10 个模块及其下属知识点），请为 5 个能力等级的学生各设计一条由浅入深的推荐学习路径。

5 个等级：
- L1 萌新：几乎零基础，数学/编程地基都要补。
- L2 入门：有基础概念，开始接触建模流程。
- L3 进阶：掌握常用模型，能独立做简单题。
- L4 熟练：能综合建模、求解与验证，冲奖。
- L5 高手：查漏补缺、打磨论文与高级方法。

只输出一个合法 JSON 对象，不要任何额外文字或代码块标记，结构严格如下：
{
  "levels": [
    {
      "level": "L1",
      "tagline": "一句话定位这个阶段的学生（中文，简短）",
      "intro": "这一阶段总体该怎么学（中文，2-3句，Markdown）",
      "milestones": [
        {
          "title": "里程碑标题（如：打好数学与编程地基）",
          "desc": "为什么这个阶段先学这些（中文，1-2句）",
          "module_ids": ["指向的模块字母，如 J、B、H"],
          "concept_ids": ["指向的具体知识点 concept_id，必须从给定清单里选"]
        }
      ]
    }
  ]
}

要求：
1. levels 必须恰好 5 条，level 依次为 L1、L2、L3、L4、L5。
2. 每个等级 3-5 个里程碑，循序渐进；越低等级越偏基础（数学基础J、新手入门B、编程H、数据E），越高等级越偏综合方法（C）、求解(D)、验证(I)、论文(A)、可视化(G)、竞赛策略(F)。
3. concept_ids 必须严格来自给定清单中的 concept_id，每个里程碑挑 2-5 个最该学的，不要编造不存在的 id。
4. 全部用简体中文。"""


def build_paths_user(concepts: list[dict]) -> str:
    by_mod: dict[str, list[dict]] = {}
    for c in concepts:
        by_mod.setdefault(c["module_id"], []).append(c)
    lines = ["=== 模块与知识点清单（concept_id ｜ 标题 ｜ 难度） ==="]
    for mid in sorted(by_mod):
        items = by_mod[mid]
        lines.append(f"\n## 模块 {mid}：{items[0]['module_name']}（{len(items)} 个）")
        for c in items:
            lines.append(f"- {c['concept_id']} ｜ {c['title']} ｜ {c['difficulty']}")
    lines.append("\n请按 system 要求，为 L1–L5 各设计一条学习路径，只输出 JSON。")
    return "\n".join(lines)


# 各等级满分占比区间（前端/后端据此把测评得分映射到等级）
SCORE_BANDS = [
    ("L1", 0.0, 0.20),
    ("L2", 0.20, 0.40),
    ("L3", 0.40, 0.60),
    ("L4", 0.60, 0.80),
    ("L5", 0.80, 1.01),
]


def normalize_paths(gen: dict, concepts: list[dict]) -> dict:
    """校验路径：concept_id 必须真实存在，过滤非法项；补 score 区间与等级名。"""
    valid_ids = {c["concept_id"] for c in concepts}
    name_by_level = {lv["level"]: lv["name"] for lv in LEVELS}
    band_by_level = {b[0]: (b[1], b[2]) for b in SCORE_BANDS}

    gen_levels = {str(l.get("level", "")).upper(): l for l in (gen.get("levels") or [])}
    out_levels = []
    dropped = 0
    for lv in LEVELS:
        code = lv["level"]
        g = gen_levels.get(code, {})
        milestones = []
        for m in (g.get("milestones") or []):
            cids = [cid for cid in (m.get("concept_ids") or []) if cid in valid_ids]
            dropped += len(m.get("concept_ids") or []) - len(cids)
            if not cids and not (m.get("module_ids")):
                continue
            milestones.append({
                "title": str(m.get("title", "")).strip(),
                "desc": str(m.get("desc", "")).strip(),
                "module_ids": [str(x).strip() for x in (m.get("module_ids") or []) if str(x).strip()],
                "concept_ids": cids,
            })
        smin, smax = band_by_level[code]
        out_levels.append({
            "level": code,
            "name": name_by_level[code],
            "score_min": smin,
            "score_max": smax,
            "tagline": str(g.get("tagline", "")).strip(),
            "intro": str(g.get("intro", "")).strip(),
            "milestones": milestones,
        })
    if dropped:
        print(f"  [校验] 过滤掉 {dropped} 个不存在的 concept_id。")
    return {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "levels": out_levels,
    }


def generate_paths(client, model) -> dict:
    concepts = load_concepts()
    print(f"加载概念 {len(concepts)} 个，生成 L1–L5 学习路径…")
    gen = call_json(client, model, SYSTEM_PATHS, build_paths_user(concepts),
                    max_tokens=6000, retry_hint=" levels 必须恰好5条 L1–L5，concept_ids 只能用清单里的。")
    if not gen or not gen.get("levels"):
        raise SystemExit("[错误] 模型未返回有效的 levels。")
    return normalize_paths(gen, concepts)


# ────────── main ──────────

def main():
    ap = argparse.ArgumentParser(description="入门测评题库 + 分级学习路径生成器（DeepSeek）")
    ap.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY", ""),
                    help="DeepSeek API key（或设环境变量 DEEPSEEK_API_KEY）")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--only-questions", action="store_true", help="只生成题库")
    ap.add_argument("--only-paths", action="store_true", help="只生成学习路径")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的产物")
    ap.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    args = ap.parse_args()

    if not args.api_key:
        print("[错误] 缺少 API key。用 --api-key 或环境变量 DEEPSEEK_API_KEY 提供。")
        sys.exit(1)
    if not CONCEPTS_DIR.exists():
        print(f"[错误] 找不到概念目录：{CONCEPTS_DIR}")
        sys.exit(1)

    random.seed(args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    from openai import OpenAI
    client = OpenAI(api_key=args.api_key, base_url=DEEPSEEK_BASE_URL)

    do_q = not args.only_paths
    do_p = not args.only_questions

    if do_q:
        if QUESTIONS_PATH.exists() and not args.force:
            print(f"[跳过] {QUESTIONS_PATH.name} 已存在（--force 可覆盖）")
        else:
            print("=== 生成测评题库 ===")
            data = generate_questions(client, args.model)
            QUESTIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n题库已写入 {QUESTIONS_PATH}（{data['count']} 题，失败 {data['failed']}）\n")

    if do_p:
        if PATHS_PATH.exists() and not args.force:
            print(f"[跳过] {PATHS_PATH.name} 已存在（--force 可覆盖）")
        else:
            print("=== 生成学习路径 ===")
            data = generate_paths(client, args.model)
            PATHS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            n = sum(len(l["milestones"]) for l in data["levels"])
            print(f"路径已写入 {PATHS_PATH}（5 个等级，{n} 个里程碑）\n")

    print("完成。建议人工审校生成的题目与路径合理性。")


if __name__ == "__main__":
    main()

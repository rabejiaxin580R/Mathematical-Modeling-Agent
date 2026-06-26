"""工作台「做题」模式：把用户上传/粘贴的一道题，用 LLM 按建模框架动态拆成分步题目。

产出与静态题库同 schema（见 problems.py / framework.py），写入 data/dynamic_problems/<id>.json，
之后所有现有 practice 接口（load_public / step-chat / assess）对它零改动即可工作。
"""
import json
import logging
import re
import uuid

from .config import config
from . import framework
from .documents import read_document

logger = logging.getLogger(__name__)


def _client_and_model():
    # 延迟导入，避免与 agent 的循环依赖
    from .agent import agent
    return agent.client, config.get_llm_model()


def _framework_brief() -> str:
    lines = []
    for s in framework.FRAMEWORK:
        lines.append(
            f"- {s['key']}｜{s['name']}（modality={s['modality']}，建议占比{s['default_weight']}）："
            f"{s['goal']}；交付物：{s['deliverable']}"
        )
    return "\n".join(lines)


_SYS = """你是数学建模助教。学生给你一道赛题题面，你要把它按「建模框架」拆成一份**一步步一起做**的引导路线图。

你**不是**在出答案、也**不是**在给这道题定标准答案/评分标尺。你只是把这道题该走的阶段排好，并在每个阶段抛出**针对本题的引导性问题**，让学生和你一步步一起把它做出来。

你必须严格输出一个 JSON 对象（不要任何额外解释、不要代码块包裹），结构如下：
{
  "title": "给这道题起的简短标题",
  "background": "题面的精炼复述（保留关键数据与问法，可适当条理化，Markdown）",
  "steps": [
    {
      "stage_key": "restate",              // 必须取自下方框架的 key
      "title": "本步标题",
      "prompt": "本步要一起想清楚什么。用要点列出 2-4 条【本步重点】，每条是一个**针对本题的引导性问题或要思考的点**，而不是答案。",
      "hint": "一两条思考方向的提示（可空，同样不能是答案）"
    }
  ]
}

# 框架阶段（按顺序选用，通常覆盖 restate→build→solve 等关键阶段；简单题可只取其中若干步）
{framework}

# 硬性要求
- steps 顺序与框架一致；每个 step 的 stage_key 必须是框架里的 key。
- 第一步用 restate：引导学生【先和助教一起拟定】——分点想清楚已知条件、要回答的子问题与关键约束（这一步是文字梳理，不写代码、不建公式）。
- 每一步的 prompt 都要显式列出【本步重点】（2-4 条），且必须是**引导性问题/要点**的口吻。
- 紧扣这道题的具体情境与数据来提问，不要套空话模板。
- **全程中文**：无论题面是中文还是英文，你输出的 title、background、每一步的 title/prompt/hint **一律用简体中文**（题面里的专有名词、变量名、公式可保留原文）。
- **绝对禁止**：不要写出这道题的答案、结论、具体公式、求解结果；不要输出评分维度（criteria）、参考标尺（reference_outline）或任何「标准做法」。你只负责把路线和问题摆出来，答案由学生和你在后续对话里一起得出。
- 只输出 JSON。"""


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    # 去掉可能的 ```json 包裹
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 兜底：截取第一个 { 到最后一个 }
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def _normalize(raw: dict, statement: str) -> dict:
    """把 LLM 产出归一为题库 schema：补 id、modality；过滤非法 stage_key。

    动态题是「一步步一起做」模式：不生成评分维度(criteria)与参考标尺(reference_outline)，
    max_score 仅为保持 schema 合法而回退到框架默认权重（动态题不打分）。
    """
    valid_keys = set(framework.stage_keys())
    steps_in = raw.get("steps") or []
    steps = []
    for i, s in enumerate(steps_in):
        key = s.get("stage_key", "")
        if key not in valid_keys:
            continue
        stage = framework.get_stage(key) or {}
        steps.append({
            "id": f"s{len(steps) + 1}",
            "stage_key": key,
            "modality": framework.modality_of(key),
            "guide_style": stage.get("guide_style", ""),
            "deliverable": stage.get("deliverable", ""),
            "title": s.get("title") or stage.get("name", f"第 {i + 1} 步"),
            "prompt": s.get("prompt", ""),
            "hint": s.get("hint", ""),
            "criteria": [],
            "max_score": stage.get("default_weight", 10),
            "reference_outline": "",
            "paper_points": [],
        })
    if not steps:
        raise ValueError("生成的步骤为空或 stage_key 不合法")

    pid = "dyn_" + uuid.uuid4().hex[:10]
    return {
        "id": pid,
        "schema_version": 2,
        "dynamic": True,
        "title": (raw.get("title") or "我的题目").strip()[:60],
        "background": raw.get("background") or statement,
        "difficulty": "",
        "tags": [],
        "data_files": [],
        "total_max_score": sum(s["max_score"] for s in steps),
        "steps": steps,
    }


def generate(statement: str, run_id: str | None = None, files: list[str] | None = None) -> dict:
    """根据题面（+可选已上传文件）生成动态题目，落盘后返回完整 dict。

    抛出异常由调用方转成 HTTP 错误。
    """
    statement = (statement or "").strip()
    # 若提供了上传的题面文档，读出正文拼进题面
    for fname in (files or []):
        if not run_id:
            break
        try:
            doc = read_document(fname, run_id)
            txt = (doc.get("content") or "").strip()
            if txt:
                statement += f"\n\n[文件 {fname} 内容]\n{txt[:6000]}"
        except Exception:
            logger.warning("做题模式读取上传文件失败：%s", fname)

    if not statement:
        raise ValueError("题面为空")

    client, model = _client_and_model()
    system = _SYS.replace("{framework}", _framework_brief())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"题面如下：\n\n{statement[:8000]}"},
    ]
    kwargs = dict(model=model, messages=messages, temperature=0.3)
    try:
        kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
    except Exception:
        # 部分兼容端点不支持 response_format，回退普通调用
        kwargs.pop("response_format", None)
        resp = client.chat.completions.create(**kwargs)

    content = resp.choices[0].message.content or ""
    raw = _extract_json(content)
    if not raw:
        raise ValueError("AI 未返回可解析的题目结构")

    problem = _normalize(raw, statement)

    config.ensure_dirs()
    out = config.DYNAMIC_PROBLEMS_DIR / f"{problem['id']}.json"
    out.write_text(json.dumps(problem, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("生成动态题目 %s（%d 步）", problem["id"], len(problem["steps"]))
    return problem

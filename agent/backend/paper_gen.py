"""论文生成引擎：把做题会话转成完整论文(Markdown)，逐节生成，后端权威落库。

流程
----
1. create_paper_session()  ── 从做题存档新建论文会话（写 paper_sessions + section_state）
2. generate_section()       ── 逐节生成：读做题对话 + 知识库 → LLM 写 → 写 section_state
3. assemble()               ── 读所有 section_state，拼成完整论文 Markdown
4. export_docx.to_docx()    ── 转 Word（已有，不重复实现）

论文生成的"人格"是 writer，不是 tutor：
- 把对话里的零散思路写成正式学术散文
- 全文英文（HiMCM 要求）
- 只写用户实际讨论过的内容，不瞎编结论
"""
import datetime
import json
import logging
from typing import Generator

from openai import OpenAI

from .config import config
from .framework import FRAMEWORK
from .knowledge import knowledge_base
from . import paper_db
from .literature_integration import augment_section_with_literature, format_citations_for_paper
from .method_library import get_recommended_methods, format_methods_for_prompt

logger = logging.getLogger(__name__)

# ── 作家人格 ──────────────────────────────────────────────────────────────────

_WRITER_PERSONA = """You are an academic paper writer for the HiMCM (High School Mathematical Contest in Modeling). Your task: transform a student's rough problem-solving conversation into a polished section of their competition paper.

IMPERATIVES:
1. Write in formal academic English. Be precise. Every sentence must carry information.
2. Only write what the student HAS discussed or what follows directly from their work. Never invent results, data, or conclusions.
3. If the conversation shows the student didn't cover this section's topic, write a brief honest note like "This aspect was not explored in depth." — do NOT pad with generic filler.
4. Use LaTeX for ALL math: inline $...$, display $$...$$. Do NOT manually number equations — write $$...$$ without (1) (2) tags; the export system will add numbering automatically.
5. Maintain consistency with earlier sections — reuse the same variable names and parameter values.
6. APA 7th: parenthetical citations (Author, Year); tables use Markdown with Caption:/Note: conventions.
7. Output ONLY the section content as raw Markdown. No explanations, no "Here is the section:", no code fences.

CRITICAL FORMATTING RULES:
- NEVER use the dollar sign $ for currency — it conflicts with LaTeX delimiters and causes rendering corruption. Write "8 million USD" or "500,000 dollars" instead of "$8 million" or "$500,000".
- For display equations with multiple lines, use separate $$...$$ blocks for each line. Do NOT use \\begin{aligned}, \\begin{cases}, \\begin{array}, or any multiline LaTeX environment — these often fail to convert to Word OMML format.
- For piecewise functions, write separate equations with text conditions: "$$f(x) = x^2$$ for $x > 0$, and $$f(x) = 0$$ otherwise."
- For inline math variables (e.g., cost parameter c_s), use $c_s$. For currency values, use plain text with "USD" or "dollars".

PARAMETER AUTHORITY:
- Core numerical parameters (speed v, service rate μ, time thresholds) are defined ONCE in the Notation section and NEVER changed.
- When you see "## Symbols and Parameters (from Notation section)" in context, those values are AUTHORITATIVE — use them verbatim in all later sections.
- Example: if Notation defines "v = 45 km/h (0.75 km/min)", do NOT write "v = 0.375 km/min" in Model Formulation. Use 0.75 km/min.

LOGICAL CONSISTENCY GUARDS (Model Formulation section):
- If queueing assumptions (Poisson arrivals, exponential service) appear in Assumptions, then vehicle allocation constraints MUST be derived from M/M/c queueing formulas (not arbitrary "minimum 2 vehicles" rules).
- If both distance (e.g., 3 km radius) and time (e.g., 8 min threshold) constraints exist, define the conversion factor (average speed) explicitly in Notation and use ONE standard consistently in constraints.
- Coverage constraints must link district assignment y_ij with coverage indicator w_i: district i is covered only if ASSIGNED to a reachable station, not just if a reachable station EXISTS. Write w_i ≤ Σ_j (reachability_ij × y_ij), NOT w_i ≤ Σ_j (reachability_ij × x_j).

NUMERICAL CONSISTENCY (Solution & Analysis sections):
- All cost calculations must be internally consistent: if N stations at cost c_s each and M ambulances at cost c_a each, total cost = N×c_s + M×c_a EXACTLY.
- Use the SAME numerical values across all tables and prose. If Section 5 reports "5 stations, 11 ambulances, total cost $4.7M", Section 6 MUST use those exact numbers.
- When presenting results, explicitly verify one key calculation in prose to demonstrate arithmetic correctness.

TABLE AND EQUATION NUMBERING:
- Do NOT manually number tables as "Table 1", "Table 2" — the Caption: line will be auto-numbered during export.
- Write "Caption: Key results summary" (no number), not "Table 2: Key results summary".
- Do NOT manually number equations — write $$...$$ without trailing (1), (2), (N). The export system numbers them automatically."""

# ── 节号与标题 ────────────────────────────────────────────────────────────────

_SECTION_META = {
    "restate":     {"num": 1,  "heading": "Problem Restatement"},
    "assume":      {"num": 2,  "heading": "Assumptions"},
    "notation":    {"num": 3,  "heading": "Notation"},
    "build":       {"num": 4,  "heading": "Model Formulation"},
    "solve":       {"num": 5,  "heading": "Model Solution"},
    "analyze":     {"num": 6,  "heading": "Results and Analysis"},
    "sensitivity": {"num": 7,  "heading": "Sensitivity Analysis"},
    "evaluate":    {"num": 8,  "heading": "Model Evaluation"},
    "extend":      {"num": 9,  "heading": "Extensions and Conclusions"},
    "abstract":    {"num": 0,  "heading": "Abstract"},
}

_BY_KEY = {s["key"]: s for s in FRAMEWORK}


def section_heading(section_key: str) -> str:
    """返回节对应的英文标题，供前端展示。"""
    return _SECTION_META.get(section_key, {}).get("heading", section_key)


# ── 分节 Prompt 构建 ──────────────────────────────────────────────────────────

def _stage_instructions(section_key: str, problem_md: str = "") -> str:
    """返回针对某节的写作指令（改进版：BUILD阶段集成方法库）。"""
    stage = _BY_KEY.get(section_key, {})
    goal = stage.get("goal", "")
    deliverable = stage.get("deliverable", "")

    specifics = {
        "restate": (
            "Write a concise problem restatement. Identify the core question, key constraints, "
            "and what the paper aims to accomplish. DO NOT copy the problem verbatim — distill it."
        ),
        "assume": (
            "List and justify the model assumptions. Each assumption should be numbered (A1, A2, ...) "
            "with a brief justification for why it is reasonable and what it simplifies. "
            "Acknowledge any limitations these assumptions introduce."
        ),
        "notation": (
            "Create a notation table. For each symbol, give its LaTeX representation, meaning, and unit (if any). "
            "Use a Markdown table with columns: Symbol | Meaning | Unit. "
            "ALL symbols used in later sections MUST be defined here first. "
            "If the model uses queueing theory (e.g., M/M/c), define service rate μ, arrival rate λ, utilization ρ. "
            "If distance and time are both used (e.g., 3 km radius AND 8 min threshold), define the conversion factor (e.g., average speed) explicitly. "
            "CRITICAL: Core numerical parameters (speed, service rate, time thresholds) must have EXACT values with units. "
            "For example: 'v = 45 km/h (0.75 km/min)' or 'μ = 2.0 calls/h'. "
            "These values are AUTHORITATIVE — later sections must use these exact numbers, not invent new ones."
        ),
        "build": _build_instruction_with_methods(problem_md) if problem_md else (
            "Present the mathematical model formally. Start from first principles, derive the key equations step by step. "
            "Use numbered display equations $$...$$ (N). Explain what each equation represents and how they connect. "
            "This is the theoretical core of the paper — be rigorous and internally consistent."
        ),
        "solve": (
            "Describe how the model was solved (800-1200 words):\n"
            "\n"
            "1. **Algorithm/Method**: What solution method was used?\n"
            "2. **Tool Implementation**: MUST specify the tool:\n"
            "   - MATLAB: 'linprog(f, A, b, Aeq, beq, lb, ub)'\n"
            "   - Python: 'pulp.LpProblem()' or 'scipy.optimize'\n"
            "   - LINGO: mention LINGO language\n"
            "3. **Key Results**: 1-2 tables\n"
            "4. **Verification**: ONE calculation example\n"
            "\n"
            "Keep concise — detailed analysis goes in 'analyze'."
        ),
        "analyze": (
            "CRITICAL: This section presents the model's numerical results and analysis.\n"
            "\n"
            "STEP 0 — DATA SOURCE (be honest about provenance):\n"
            "- If USER'S DATA is provided in the prompt → analyze THAT data only; NEVER invent numbers that contradict it.\n"
            "- If NO user data is provided → generate placeholder example data, and mark it clearly: start the section with "
            "a note such as 'PLACEHOLDER DATA — the numbers below are illustrative examples and must be replaced with your real results.', "
            "and label each such table 'Table (placeholder): ...'.\n"
            "\n"
            "STEP 1 — CHOOSE THE TEST-CASE TYPE that matches the problem (infer from the problem statement):\n"
            "  - Optimization / facility-location / routing / covering → fictional demand points, districts, candidate sites, or cost/demand/radius scenarios.\n"
            "  - Scoring / ranking / recommendation → fictional decision-makers with different preference weights and a set of candidate options.\n"
            "  - Queueing / service / simulation → fictional service configurations (arrival rate λ, service rate μ, capacity c).\n"
            "  - Other → fictional input scenarios that vary the model's key parameters.\n"
            "\n"
            "MANDATORY REQUIREMENTS:\n"
            "1. CREATE at least 10 test cases of that type:\n"
            "   - Label them (Case 1..10); if they are people, give names (e.g., Alice, Bob, ...).\n"
            "   - Specify the INPUT parameters the model consumes (weights, demands, rates, costs, distances — whatever the model takes in).\n"
            "   - Define their constraints.\n"
            "2. RUN the model on ALL cases:\n"
            "   - Apply the model to each test case.\n"
            "   - Show the output (optimal solution, recommended option, score, or performance metric).\n"
            "   - Include numerical values, and rankings where the model produces them.\n"
            "3. PRESENT results in tables:\n"
            "   - Table 1: Input parameters for the test cases.\n"
            "   - Table 2: Model outputs (case, output, score/rank where applicable).\n"
            "4. SHOW at least 2 detailed calculation examples:\n"
            "   - Pick 2 representative cases.\n"
            "   - Show formula → substitute actual numbers → final result.\n"
            "5. INTERPRET and compare:\n"
            "   - How do different inputs lead to different outputs?\n"
            "   - Are there infeasible / boundary / tie cases?\n"
            "   - Do the results align with intuition?\n"
            "\n"
            "FORMAT:\n"
            "- Start with: 'We validated the model using ten test cases.'\n"
            "- Minimum 2000 words\n"
            "- At least 2 tables with actual data\n"
            "- At least 4 formulas with numerical substitution\n"
            "\n"
            "If you generated PLACEHOLDER data, say so clearly at the top of the section."
        ),
        "sensitivity": (
            "Describe how the model output changes when key parameters are perturbed (600-800 words). "
            "\n"
            "1. Identify 2-3 key parameters\n"
            "2. Define parameter range (e.g., ±20%)\n"
            "3. Present results in table/figure\n"
            "4. Identify most/least sensitive parameters\n"
            "\n"
            "Keep focused."
        ),
        "evaluate": (
            "Honestly assess the model's strengths and weaknesses (600-800 words). "
            "\n"
            "**Strengths** (3-4 points): what does the model do well?\n"
            "**Weaknesses** (3-4 points): where does it fall short?\n"
            "\n"
            "Connect weaknesses back to assumptions. Suggest improvements."
        ),
        "extend": (
            "Propose 2-3 natural extensions of the model (600-800 words):\n"
            "\n"
            "1. Relaxing a key assumption\n"
            "2. Adding a new factor\n"
            "3. Applying to different scenario\n"
            "\n"
            "Each extension: 2-3 sentences. Do NOT derive new models.\n"
            "\n"
            "End with brief conclusion (3-4 sentences)."
        ),
        "abstract": (
            "Write a one-paragraph abstract (150-250 words) that summarizes the ENTIRE paper: "
            "problem, approach, key methods, main results, and conclusion. "
            "CRITICAL: NO LaTeX equations (not even inline $...$), NO citations, NO section references. "
            "State numerical results in plain text with units (e.g., '5 stations and 11 ambulances at a total cost of $4.7 million'). "
            "Write in complete sentences, suitable for a conference proceedings abstract."
        ),
    }

    return specifics.get(section_key, goal)


def _build_instruction_with_methods(problem_md: str) -> str:
    """为BUILD阶段生成包含方法推荐的指令"""
    try:
        recommended = get_recommended_methods(problem_md, top_n=3)
        methods_text = format_methods_for_prompt(recommended)
    except Exception as e:
        logger.warning(f"方法推荐失败: {e}，使用默认指令")
        methods_text = "(Method recommendation unavailable)"

    return f"""Present the mathematical model formally (1500-2500 words).

## STEP 1: Method Selection

Consider multiple modeling approaches. Recommended methods:

{methods_text}

**Your task**:
1. Choose 1-2 methods (or propose similar alternatives)
2. Explain WHY (2-3 sentences)
3. Compare pros/cons if multiple methods viable (optional)

## STEP 2: Model Formulation

1. **Decision Variables**: Define clearly
2. **Objective Function**: Use numbered equations $$...$$ (1)
3. **Constraints**: List all constraints
4. **Mathematical Derivation**: Show key steps

## STEP 3: Tool Implementation (MUST specify)

Indicate how to implement:
- MATLAB: which function? (linprog, intlinprog, fmincon)
- Python: which library? (pulp, scipy, cvxpy)
- LINGO: mention LINGO language

Example: "This linear programming model is solved using MATLAB's linprog()."

## Requirements
- Mathematically rigorous
- 3-5 numbered equations
- 1500-2500 words
"""


def _build_user_prompt(
    section_key: str,
    problem_md: str,
    conversation_text: str,
    previous_sections: str,
    kb_context: str,
    literature_context: str = "",
) -> str:
    """组装发送给 LLM 的 user prompt。"""
    meta = _SECTION_META.get(section_key, {})
    heading = meta.get("heading", section_key)
    instructions = _stage_instructions(section_key, problem_md)  # 传递problem_md

    parts = [
        f"# Your task: Write the **{heading}** section of this HiMCM paper.",
        "",
        "## Section writing instructions",
        instructions,
        "",
        "## The problem",
        problem_md,
    ]

    if conversation_text.strip():
        parts.extend([
            "",
            "## Student's working conversation for this section",
            "Below is the student's discussion with their AI tutor about this part of the problem.",
            "Extract the key insights, decisions, and results — do NOT copy the conversation tone.",
            "",
            conversation_text,
        ])
    else:
        parts.extend([
            "",
            "## Note",
            "No working conversation exists for this section. Write what you can from the problem",
            "statement and surrounding sections. If there is genuinely nothing to write, say so briefly.",
        ])

    if previous_sections.strip():
        parts.extend([
            "",
            "## Previously written sections (for cross-reference consistency)",
            "Use the SAME variable names, symbol definitions, and equation numbers as below.",
            "",
            previous_sections,
        ])

    if kb_context.strip():
        parts.extend([
            "",
            "## Knowledge base references",
            "These are relevant mathematical concepts from the course knowledge base.",
            "Use them to ensure technical accuracy, but cite them naturally.",
            "",
            kb_context,
        ])

    if literature_context.strip():
        parts.extend([
            "",
            "## Relevant academic literature",
            "These papers from arXiv and Semantic Scholar are relevant to this section.",
            "Cite them using APA format (Author, Year) when their methods inform your approach.",
            "",
            literature_context,
        ])

    parts.append(
        "\n\nWrite ONLY the section content in raw Markdown. "
        "Begin with the section heading. Do not wrap in code fences."
    )
    return "\n".join(parts)


# ── 上下文收集 ────────────────────────────────────────────────────────────────

def _conversation_for_stage(
    solve_session: dict, section_key: str, solve_convs_dir
) -> str:
    """从做题存档里找出匹配某阶段的对话记录，返回格式化的文本。"""
    problem = solve_session.get("problem", {}) or {}
    steps = problem.get("steps", []) or []
    step_convs = solve_session.get("step_convs", {}) or {}

    # 找到 stage_key 匹配的 step
    matching = [s for s in steps if s.get("stage_key") == section_key]
    if not matching:
        return ""

    lines: list[str] = []
    for step in matching:
        sid = step.get("id", "")
        conv_id = step_convs.get(sid, "")
        if not conv_id:
            continue

        # 加载对话（从 solve_conversations 目录）
        conv_path = solve_convs_dir / f"{conv_id}.json"
        if not conv_path.exists():
            continue
        try:
            conv = json.loads(conv_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        messages = conv.get("messages", []) or []
        for m in messages:
            role = m.get("role", "")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                lines.append(f"Student: {content}")
            elif role == "assistant":
                # 截断过长回复（做题 agent 回复可能很长且含工具调用细节）
                lines.append(f"Tutor: {content[:800]}")
            # 跳过 tool 消息

    return "\n\n".join(lines[-60:])  # 只保留最近 60 条发言


def _build_structured_context(conn, session_id: str, current_key: str) -> str:
    """从数据库读取结构化上下文（符号、模型、结果），避免文本截断导致的数值丢失。"""
    order = paper_db.SECTION_ORDER
    current_idx = order.index(current_key) if current_key in order else len(order)

    parts = []

    # 1. 符号表（从 symbols 表读，不从 Markdown 截断）
    symbols = paper_db.load_symbols(conn, session_id)
    if symbols:
        parts.append("## Symbols and Parameters (from Notation section)")
        parts.append("These are the AUTHORITATIVE definitions. Use these exact values — do NOT change them.")
        rows = [f"- ${s['symbol_tex']}$: {s['meaning']}" + (f" ({s['unit']})" if s.get('unit') else "")
                for s in symbols]
        parts.append("\n".join(rows[:40]))  # 最多 40 个符号，确保核心参数在内

    # 2. 模型卡片（从 model_cards 表读）
    models = paper_db.load_model_cards(conn, session_id)
    if models:
        parts.append("\n## Models used")
        for m in models:
            parts.append(f"- **{m['model_id']}**: {m['name']}" + (f" (KB: {m['concept_id']})" if m.get('concept_id') else ""))

    # 3. 关键数值结果（从 results_ledger 表读）
    results = paper_db.load_results(conn, session_id)
    if results:
        parts.append("\n## Key numerical results (DO NOT change these values)")
        for r in results:
            parts.append(f"- **{r['result_id']}**: {r['label']} = {r['value_text']}" + (f" {r['unit']}" if r.get('unit') else ""))

    # 4. 前序节的完整内容（只取标题+前 800 字符，给 LLM 看章节结构）
    sections = paper_db.list_sections(conn, session_id)
    prev = [s for s in sections if order.index(s['section_key']) < current_idx and s.get('content_md')]
    if prev:
        parts.append("\n## Previously written sections (for context and consistency)")
        for s in prev:
            meta = _SECTION_META.get(s['section_key'], {})
            heading = meta.get('heading', s['section_key'])
            content = (s.get('content_md') or '').strip()  # 不再截断
            parts.append(f"### {heading}\n{content}...")

    return "\n\n".join(parts)


def _previous_sections_text(sections: list[dict], current_key: str) -> str:
    """DEPRECATED: 旧的文本截断方式，保留供不需要数据库连接的地方调用。"""
    order = paper_db.SECTION_ORDER
    current_idx = order.index(current_key) if current_key in order else len(order)
    parts = []
    for s in sections:
        sk = s.get("section_key", "")
        if sk not in order:
            continue
        if order.index(sk) >= current_idx:
            continue
        content = (s.get("content_md") or "").strip()
        if not content:
            continue
        meta = _SECTION_META.get(sk, {})
        parts.append(f"### {meta.get('heading', sk)}\n\n{content[:1500]}")
    return "\n\n".join(parts)


def _kb_for_stage(section_key: str, conversation_text: str, top_k: int = 5) -> str:
    """为当前阶段检索知识库。"""
    stage = _BY_KEY.get(section_key, {})
    query = f"{stage.get('goal','')} {stage.get('deliverable','')} {conversation_text[:300]}"
    hits = knowledge_base.search(query, top_k=top_k)
    if not hits:
        return ""
    parts = []
    for unit, _score in hits:
        parts.append(unit.to_context())
    return "\n\n".join(parts)


# ── LLM 调用 ─────────────────────────────────────────────────────────────────

def _client() -> OpenAI:
    return OpenAI(api_key=config.get_llm_api_key(), base_url=config.get_llm_base_url())


def _call_writer(system: str, user: str) -> str | None:
    """调用 LLM 生成一节内容。失败返回 None。"""
    client = _client()
    model = config.get_llm_model()

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=384000,  # DeepSeek V4 Pro支持最大384K输出
            timeout=1800,  # 3分钟超时（生成更长内容需要更多时间）
        )
        return resp.choices[0].message.content or ""

    try:
        content = _call()
    except Exception as e:
        logger.error("论文生成 LLM 调用失败（%s）：%s", model, e)
        logger.error("Prompt 长度: system=%d, user=%d", len(system), len(user))
        return None

    if not content.strip():
        logger.warning("LLM 返回空内容，重试一次...")
        # 重试一次
        try:
            content = _call()
        except Exception as e:
            logger.error("论文生成 LLM 重试失败：%s", e)
            return None

        if not content.strip():
            logger.error("LLM 重试后仍返回空内容")
            return None

    return content.strip()


def _verify_completeness(content_md: str, section_key: str) -> tuple[bool, str]:
    """检查章节是否完整（未被截断）。

    返回: (is_complete, reason)
    """
    if not content_md or len(content_md) < 500:
        return False, "内容过短（< 500 字符）"

    # 检查是否以句号、问号、感叹号结尾（允许末尾有空白符）
    text = content_md.rstrip()
    if not text:
        return False, "内容为空"

    last_char = text[-1]
    # 中英文句号、问号、感叹号都视为正常结束
    normal_endings = {'.', '。', '?', '？', '!', '！', ')', '）', ']', '】', '"', '\''}
    # 添加智能引号
    normal_endings.update(['“', '”', '‘', '’'])  # " " ' '

    if last_char not in normal_endings:
        # 检查是否在 Markdown 代码块或公式中（这些可能不以句号结尾）
        if text.endswith('```') or text.endswith('$$'):
            return True, "以代码块/公式结束"
        return False, f"可疑结束字符：'{last_char}'"

    # 检查关键章节（如 Model）是否包含核心部分
    if section_key == 'model':
        if '$$' not in content_md or content_md.count('$$') < 4:
            return False, "模型章节缺少公式（< 2 个公式块）"

    if section_key == 'solve':
        # 求解章节应该有算法描述或结果
        if not any(kw in content_md.lower() for kw in ['algorithm', 'result', 'solution', '算法', '结果', '求解']):
            return False, "求解章节缺少关键词（algorithm/result/solution）"

    return True, "完整"


# ── 符号与模型卡片提取（LLM 辅助）────────────────────────────────────────────

_EXTRACT_SYMBOLS_PROMPT = """Extract all mathematical symbols from the Notation section below.
Output ONLY a JSON array. Each element: {"symbol_tex":"\\\\bar{x}", "meaning":"sample mean", "unit":"dimensionless"}.
If the section has no symbols, output []."""

_EXTRACT_MODELS_PROMPT = """Extract all mathematical models/methods from the Model Formulation section below.
Output ONLY a JSON array. Each element: {"name":"Linear Programming", "concept_id":"", "rationale":"why chosen"}.
If no models are mentioned, output []."""


def _extract_json_array(raw: str) -> list[dict]:
    """从 LLM 返回中提取 JSON 数组。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    l, r = text.find("["), text.rfind("]")
    if l == -1 or r == -1:
        return []
    try:
        return json.loads(text[l:r + 1])
    except json.JSONDecodeError:
        return []


def _extract_symbols(conn, session_id: str, content_md: str):
    """从符号节提取符号写入 symbols 表。"""
    client = _client()
    model = config.get_llm_model()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACT_SYMBOLS_PROMPT},
                {"role": "user", "content": content_md[:3000]},
            ],
            temperature=0,
        )
        items = _extract_json_array(resp.choices[0].message.content or "")
    except Exception:
        return

    for item in items:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO symbols (session_id, symbol_tex, meaning, unit, introduced)"
                " VALUES (?,?,?,?,?)",
                (session_id, item.get("symbol_tex", ""), item.get("meaning", ""),
                 item.get("unit") or None, "notation"),
            )
        except Exception:
            continue
    conn.commit()


def _extract_models(conn, session_id: str, content_md: str):
    """从建模节提取模型写入 model_cards 表。"""
    client = _client()
    model = config.get_llm_model()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACT_MODELS_PROMPT},
                {"role": "user", "content": content_md[:3000]},
            ],
            temperature=0,
        )
        items = _extract_json_array(resp.choices[0].message.content or "")
    except Exception:
        return

    for i, item in enumerate(items):
        mid = f"M{i + 1}"
        try:
            conn.execute(
                "INSERT OR REPLACE INTO model_cards"
                " (session_id, model_id, name, concept_id, rationale)"
                " VALUES (?,?,?,?,?)",
                (session_id, mid, item.get("name", ""), item.get("concept_id") or None,
                 item.get("rationale", "")),
            )
        except Exception:
            continue
    conn.commit()


_EXTRACT_RESULTS_PROMPT = """Extract key numerical results from the solution/analysis section below.
Output ONLY a JSON array. Each element: {
  "label": "descriptive name (e.g., 'optimal station count', 'total cost')",
  "value_text": "the numeric value as a string (e.g., '5', '7.5', '6.8 minutes')",
  "unit": "unit if any (e.g., 'million USD', 'minutes'), or empty string",
  "source_eq": "equation number or table reference if any, or empty string"
}.
Focus on PRIMARY results: objective function value, decision variable optimal values, key performance metrics.
If no numerical results are present, output []."""


def _extract_results(conn, session_id: str, section_key: str, content_md: str):
    """从 solve/analyze 节提取关键数值结果写入 results_ledger 表。"""
    client = _client()
    model = config.get_llm_model()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACT_RESULTS_PROMPT},
                {"role": "user", "content": content_md[:4000]},
            ],
            temperature=0,
        )
        items = _extract_json_array(resp.choices[0].message.content or "")
    except Exception:
        return

    for i, item in enumerate(items):
        rid = f"R{i + 1}"
        try:
            conn.execute(
                "INSERT OR REPLACE INTO results_ledger"
                " (session_id, result_id, label, value_text, unit, source_eq, section_key)"
                " VALUES (?,?,?,?,?,?,?)",
                (session_id, rid, item.get("label", ""), item.get("value_text", ""),
                 item.get("unit") or None, item.get("source_eq") or None, section_key),
            )
        except Exception:
            continue
    conn.commit()


# ── 两阶段生成：Results 章节专用 ─────────────────────────────────────────────

def _generate_fictional_test_data(
    conn,
    session_id: str,
    problem_md: str,
    model_context: str,
    num_students: int = 10
) -> str | None:
    """Stage 1: 生成虚构学生测试数据并存入数据库

    Args:
        conn: 数据库连接
        session_id: 论文会话ID
        problem_md: 问题描述
        model_context: 前序章节上下文（包含模型描述）
        num_students: 生成学生数量

    Returns:
        dataset_id (如 "DS1")，失败返回 None
    """
    prompt = f"""You are creating test data for a mathematical model evaluation.

Problem:
{problem_md[:2000]}

Model (from previous sections):
{model_context}

CRITICAL INSTRUCTIONS:
1. READ the Model Formulation section above carefully
2. IDENTIFY the model type:
   - Mathematical programming (LP/MILP/NLP): has decision variables, objective function, constraints
   - Scoring/ranking model: has weighted criteria and alternatives
   - Queueing/simulation model: has arrival rates, service rates, system states
   - Other analytical model: describe what it optimizes or evaluates

3. GENERATE test data that MATCHES the model structure:

   For MATHEMATICAL PROGRAMMING models:
   - Generate parameter variations (cost coefficients, resource limits, demand values)
   - Each test case = one problem instance with different parameter values
   - Include the INPUT parameters the model needs (NOT the output decision variables)

   For SCORING/RANKING models:
   - Generate decision makers with different preference weights
   - Generate alternatives with different attribute values
   - Each test case = one decision maker evaluating a set of alternatives

   For QUEUEING/SIMULATION models:
   - Generate scenarios with different arrival/service rates, capacities
   - Each test case = one system configuration to evaluate

4. Generate {num_students} diverse test cases
5. Return ONLY a valid JSON array (no markdown, no explanations)

EXAMPLE STRUCTURES:

For a job selection scoring model:
[
  {{
    "case_id": "C1",
    "decision_maker": "Alice",
    "preference_weights": {{"income": 0.5, "learning": 0.3, "location": 0.1, "hours": 0.1}},
    "alternatives": [
      {{"id": "J1", "title": "Tech Intern", "income": 5000, "learning": 85, "location": 90, "hours": 40}},
      {{"id": "J2", "title": "Tutor", "income": 3000, "learning": 60, "location": 95, "hours": 20}}
    ]
  }}
]

For a facility location MILP model:
[
  {{
    "case_id": "C1",
    "scenario": "High demand scenario",
    "num_districts": 10,
    "total_demand": 250,
    "budget_usd": 5000000,
    "facility_cost_usd": 100000,
    "unit_cost_usd": 500,
    "service_radius_km": 2.0,
    "notes": "Dense urban area with high variability in district demands"
  }},
  {{
    "case_id": "C2",
    "scenario": "Low demand scenario",
    "num_districts": 10,
    "total_demand": 150,
    "budget_usd": 3000000,
    "facility_cost_usd": 100000,
    "unit_cost_usd": 500,
    "service_radius_km": 3.0,
    "notes": "Suburban area with more uniform demand"
  }}
]

IMPORTANT: Keep test case data SIMPLE and COMPACT. Do NOT generate large matrices or arrays.
For parameters like "distance matrix" or "demand vector", just describe them in a "notes" field rather than generating full arrays.

OUTPUT FORMAT: Return ONLY the JSON array starting with [ and ending with ].
Make test cases DIVERSE (vary parameters significantly to show model behavior under different conditions).
"""

    client = _client()
    try:
        # 使用 deepseek-v4-pro（1M上下文，384K输出）生成测试数据
        resp = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,  # 高温度增加多样性
            max_tokens=8000,
            timeout=60,
        )
        result_text = resp.choices[0].message.content.strip()

        # 清理 markdown 代码块
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            result_text = "\n".join(lines[1:-1]) if len(lines) > 2 else result_text
            if result_text.startswith("json"):
                result_text = result_text[4:].strip()

        students = json.loads(result_text)
        logger.info(f"成功生成 {len(students)} 个虚构学生测试数据")

        # 存入数据库
        dataset_id = "DS1"
        result = paper_db.store_dataset(
            conn=conn,
            session_id=session_id,
            dataset_id=dataset_id,
            dataset_name="Test Cases for Model Validation",
            source_type="generated",
            data_rows=students,
            section_key="analyze"
        )

        if result.get("ok"):
            logger.info(f"测试数据已存入数据库: {dataset_id}, {result['rows']} 行")
            return dataset_id
        else:
            logger.error(f"数据库存储失败: {result.get('error')}")
            return None

    except json.JSONDecodeError as e:
        logger.error(f"虚构测试数据 JSON 解析失败: {e}")
        logger.error(f"LLM 返回内容前 500 字符: {result_text[:500] if 'result_text' in locals() else 'N/A'}")
        return None
    except Exception as e:
        logger.error(f"虚构测试数据生成失败: {e}")
        return None


def _generate_results_with_data(
    conn,
    session_id: str,
    problem_md: str,
    model_context: str,
    conversation_text: str,
    kb_context: str
) -> str | None:
    """Stage 2: 从数据库读取数据摘要生成 Results and Analysis 章节

    Args:
        conn: 数据库连接
        session_id: 论文会话ID
        problem_md: 问题描述
        model_context: 前序章节上下文（符号、模型）
        conversation_text: 做题对话记录
        kb_context: 知识库上下文

    Returns:
        Results 章节的 Markdown 文本，失败返回 None
    """
    # 从数据库读取数据集摘要
    dataset_context = paper_db.build_dataset_context(conn, session_id)

    if not dataset_context:
        logger.warning("未找到测试数据集，Results章节可能不完整")
        dataset_context = "No test data available. Generate analysis based on theoretical model only."

    prompt = f"""# Your task: Write the **Results and Analysis** section of this HiMCM paper.

## Section writing instructions

{_stage_instructions('analyze')}

## The problem

{problem_md}

## Previously written sections (for cross-reference consistency)

{model_context}

## Student's working conversation for this section

{conversation_text[:2000] if conversation_text else "No conversation available."}

## Knowledge base references

{kb_context[:1000] if kb_context else ""}

## Available Test Data

{dataset_context}

**Note**: The test data is stored in the database. Use the schema, statistics, and sample cases provided above to perform your analysis. You do NOT need to generate fictional data yourself - it has already been prepared.

## MANDATORY REQUIREMENTS

**STEP 1: Extract the model formula from the Model Formulation section**
   - Before doing ANY calculations, identify the PRIMARY formula/method from the Model section
   - If it's an optimization model: state the objective function
   - If it's a scoring model: state the composite score formula
   - If it's a simulation model: state the performance metric formula
   - QUOTE the exact formula using LaTeX (copy it verbatim from the Model section)

   Example:
   "According to the Model Formulation section, the composite score is calculated as:
   $$S_j = \\sum_{{i=1}}^{{n}} w_i \\cdot v_{{ij}}$$
   where $w_i$ is the weight for criterion $i$ and $v_{{ij}}$ is the normalized value of alternative $j$ on criterion $i$."

**STEP 2: Apply this EXACT formula to ALL test cases**
   - For each test case, substitute the actual parameter values into THE FORMULA FROM STEP 1
   - Do NOT invent a different formula or method
   - Do NOT simplify or modify the model structure
   - Show at least 2 DETAILED calculations with step-by-step substitution

   Example:
   For test case "Alice" with weights [0.5, 0.3, 0.1, 0.1] evaluating Job 1:
   $$S_1 = 0.5 \\times 0.82 + 0.3 \\times 0.65 + 0.1 \\times 0.70 + 0.1 \\times 0.80 = 0.746$$

**STEP 3: Create result tables**
   - Table 1: Test case parameters (the INPUT data for each case)
   - Table 2: Model outputs (the RESULTS after applying the formula from Step 1)

**STEP 4: Interpret results**
   - How do different input parameters lead to different outputs?
   - Are there trade-offs or surprising outcomes?
   - Do the results validate the model's logic?
   - Any edge cases (infeasible solutions, ties, boundary conditions)?

**STEP 5: Cross-check consistency**
   - Verify that ALL calculations use the SAME formula (the one from Model Formulation)
   - Verify that variable names match the Notation section
   - If you find ANY inconsistency, STOP and revise

**Format requirements**:
   - Start with: "We validated the model using [N] test cases with diverse characteristics..."
   - Minimum 2000 words
   - Use LaTeX for all math: inline $...$ and display $$...$$
   - Tables in Markdown format
   - At least 2 detailed calculation examples (more for complex models)

Write ONLY the section content in raw Markdown. Begin with the section heading. Do not wrap in code fences.
"""

    return _call_writer(_WRITER_PERSONA, prompt)


# ── 主入口：逐节生成 ──────────────────────────────────────────────────────────

def generate_section(
    conn,
    session_id: str,
    section_key: str,
    solve_session: dict | None = None,
) -> Generator[dict, None, None]:
    """生成论文的一个章节，yield SSE event dicts。

    调用方负责：
    - 打开 conn（paper_db.get_conn()）
    - 加载 solve_session（如果有）
    - 循环 yield event，序列化为 SSE

    SSE 事件类型：
      progress  — 进度提示
      result    — {section_key, heading, content_md, chars}
      done      — 结束
      error     — 失败
    """
    meta = _SECTION_META.get(section_key, {})
    heading = meta.get("heading", section_key)

    # 1. 加载 paper session
    ps = paper_db.load_session(conn, session_id)
    if ps is None:
        yield {"type": "error", "message": f"论文会话 {session_id} 不存在"}
        yield {"type": "done"}
        return
    problem_md = ps.get("problem_md", "")

    # 1.5. 初始化文献库（首次生成时）
    if section_key == paper_db.SECTION_ORDER[0]:  # 第一个章节
        try:
            from . import paper_literature
            yield {"type": "progress", "step": "init_literature",
                   "message": "正在初始化文献知识库..."}

            # 创建文献会话（如果不存在）
            lit_conn = paper_literature.get_lit_conn()
            paper_literature.create_paper_session(lit_conn, session_id, problem_md)
            lit_conn.close()

            logger.info(f"文献知识库已初始化: {session_id}")
        except Exception as e:
            logger.warning(f"文献库初始化失败（非致命）: {e}")

    # 2. 收集对话上下文
    yield {"type": "progress", "step": "gather_context",
           "message": f"正在收集「{heading}」的做题记录…"}

    conversation_text = ""
    if solve_session:
        from .config import config as cfg
        conversation_text = _conversation_for_stage(
            solve_session, section_key, cfg.SOLVE_CONVERSATIONS_DIR,
        )

    # 3. 获取前序章节（改为从数据库读结构化数据）
    yield {"type": "progress", "step": "load_context",
           "message": f"正在加载前序章节的符号、模型、结果…"}
    previous_text = _build_structured_context(conn, session_id, section_key)

    # 3.5. 若本节已有「大纲摘要」（来自大纲页面），注入为「展开这份已确认的大纲」，
    #      确保完整论文与用户在大纲页审阅过的符号/数值/结论一致，而不是凭空重写。
    existing = paper_db.load_section(conn, session_id, section_key)
    outline_summary = (existing.get("content_md") or "").strip() if existing else ""
    if outline_summary:
        previous_text = (
            "## Your approved outline for THIS section (expand it into the full section; "
            "keep all symbols, numbers, and conclusions consistent with it)\n\n"
            + outline_summary
            + "\n\n"
            + previous_text
        )

    # 4. 知识库检索
    yield {"type": "progress", "step": "kb_search",
           "message": f"正在检索相关知识…"}
    kb_ctx = _kb_for_stage(section_key, conversation_text)

    # 4.5. 文献检索（从paper_literature知识库）
    literature_ctx = ""
    try:
        from . import paper_literature

        # 判断是否需要文献支持
        if section_key in ("build", "solve", "analyze"):
            yield {"type": "progress", "step": "literature_retrieve",
                   "message": f"正在检索文献知识库…"}

            lit_conn = paper_literature.get_lit_conn()

            # 构建检索query（基于章节和问题）- 使用多个查询提高覆盖率
            query_map = {
                "build": ["modeling formulation method", "optimization model", "mathematical framework"],
                "solve": ["algorithm solution", "optimization solver", "computational method"],
                "analyze": ["validation analysis", "sensitivity test", "result evaluation"]
            }
            queries = query_map.get(section_key, [""])

            # 检索相关片段（多查询合并，增加覆盖）
            all_chunks = []
            seen_chunk_ids = set()

            for query in queries:
                chunks_batch = paper_literature.search_chunks(lit_conn, session_id, query, top_k=10)
                for chunk in chunks_batch:
                    chunk_id = chunk.get("chunk_id")
                    if chunk_id not in seen_chunk_ids:
                        all_chunks.append(chunk)
                        seen_chunk_ids.add(chunk_id)

            # 限制总数
            chunks = all_chunks[:15]

            # 构建上下文（使用第一个查询）
            literature_ctx = paper_literature.build_literature_context(
                conn=lit_conn,
                session_id=session_id,
                query=queries[0] if queries else "",
                max_chunks=15
            )

            # 自动记录引用（所有被检索到的论文）
            if chunks:
                cited_papers = set()
                for chunk in chunks:
                    paper_id = chunk.get("paper_id")
                    if paper_id and paper_id not in cited_papers:
                        paper_literature.cite_paper(
                            conn=lit_conn,
                            session_id=session_id,
                            section_key=section_key,
                            paper_id=paper_id,
                            citation_context=f"Used for {section_key} section: {chunk.get('chunk_type', 'reference')}"
                        )
                        cited_papers.add(paper_id)
                logger.info(f"为 {section_key} 章节检索到 {len(chunks)} 个文献片段，引用 {len(cited_papers)} 篇论文")

            lit_conn.close()

            if literature_ctx:
                logger.info(f"文献上下文已注入到 {section_key} 章节")
    except Exception as e:
        logger.warning(f"文献检索失败（非致命）: {e}")
        literature_ctx = ""

    # 5. 构建 prompt 并调用 LLM
    # 特殊处理：analyze 章节使用两阶段生成
    if section_key == "analyze":
        yield {"type": "progress", "step": "stage1_test_data",
               "message": f"正在生成虚构测试数据…"}
        dataset_id = _generate_fictional_test_data(
            conn, session_id, problem_md, previous_text
        )
        if dataset_id is None:
            yield {"type": "error", "message": "测试数据生成失败"}
            yield {"type": "done"}
            return

        yield {"type": "progress", "step": "stage2_results",
               "message": f"正在基于测试数据撰写「{heading}」…"}
        content_md = _generate_results_with_data(
            conn, session_id, problem_md, previous_text, conversation_text, kb_ctx
        )
    else:
        yield {"type": "progress", "step": "llm_write",
               "message": f"正在撰写「{heading}」…"}
        user_prompt = _build_user_prompt(
            section_key, problem_md, conversation_text, previous_text, kb_ctx, literature_ctx,
        )
        content_md = _call_writer(_WRITER_PERSONA, user_prompt)

    if content_md is None:
        yield {"type": "error", "message": f"「{heading}」生成失败（LLM 调用错误），请稍后重试。"}
        yield {"type": "done"}
        return

    # 5.5. 验证章节完整性
    is_complete, reason = _verify_completeness(content_md, section_key)
    if not is_complete:
        logger.warning(f"章节 {section_key} 可能不完整：{reason}")
        yield {"type": "progress", "step": "verify_warning",
               "message": f"⚠️ 「{heading}」可能不完整（{reason}），但已保存"}

    # 6. 写入 section_state
    paper_db.upsert_section(conn, session_id, section_key, content_md, status="draft")

    # 7. 特殊阶段：提取结构化数据
    if section_key == "notation":
        try:
            _extract_symbols(conn, session_id, content_md)
        except Exception:
            logger.exception("符号提取失败（非致命）")
    elif section_key == "build":
        try:
            _extract_models(conn, session_id, content_md)
        except Exception:
            logger.exception("模型提取失败（非致命）")
    elif section_key in ("solve", "analyze"):
        # 提取关键数值结果，防止后续节数字幻觉
        try:
            _extract_results(conn, session_id, section_key, content_md)
        except Exception:
            logger.exception("结果提取失败（非致命）")


    yield {
        "type": "result",
        "section_key": section_key,
        "heading": heading,
        "content_md": content_md,
        "chars": len(content_md),
    }
    yield {"type": "done"}


# ── 大纲模式：逐节生成简化摘要（复用逐节流水线 + 填表约束）────────────────────

_OUTLINE_PERSONA = """You are an academic paper OUTLINE generator for the HiMCM (High School Mathematical Contest in Modeling). You produce a concise, high-quality section OUTLINE — not the full paper text.

For the section you are asked about, output ONLY a JSON object with three fields:
1. "summary" — a concise but COMPLETE, conclusion-oriented summary: state what this section will CONCLUDE (its key insight, model, method, or result), not a to-do list. Write in formal academic English. Size the length to the section's content — aim for roughly 150-600 words; short structural sections (notation, assumptions) can be brief, while results-heavy sections (solution, analysis) should carry the key numerical findings and may run longer. Never pad to fill space, but never truncate a critical number, symbol, or conclusion just to hit a word count.
2. "formulas" — 1-2 key LaTeX formulas as an array of strings, each $...$ inline or $$...$$ display.
3. "citations" — 1-2 papers from the provided literature that inform this section, as an array of {"title","authors","year"}. Use [] if none match.

IMPERATIVES:
- SYMBOL CONSISTENCY: every symbol in your formulas MUST match the symbols already defined in the Notation context (provided as "Previously written sections / defined symbols"). Never introduce an undefined symbol.
- PARAMETER AUTHORITY: if the context gives an authoritative value (e.g., v = 45 km/h), use it verbatim.
- Conclusion-oriented summaries, not process descriptions.
- LaTeX only for math; NEVER use $ for currency (write "4.7M USD", not "$4.7M").
- Output ONLY valid JSON — no markdown fences, no explanations.
"""


# 需要用户真实数据的章节 → 前端展示「需你的数据」徽标与待办清单
_DATA_CHECKLIST = {
    "solve": "你的求解方法 + 关键结果数值（用了什么工具/算法，最终算出的最优解或指标）。",
    "analyze": "≥10 个测试案例的输入参数 + 你的模型输出（最优解/得分/指标/排名）。"
               "不提供则由 AI 生成标注为「占位」的示例数据，需你替换为真实结果。",
}


def _build_outline_prompt(
    section_key: str,
    heading: str,
    problem_md: str,
    user_thought: str,
    user_data: str,
    previous_context: str,
    kb_context: str,
    literature_context: str,
) -> str:
    """组装单节大纲的 user prompt：复用 _stage_instructions 的填表约束。"""
    instructions = _stage_instructions(section_key, problem_md)

    # 关键章节补充约束：确保符号/数值在摘要里显式落地，供后续章节闭环引用
    outline_notes = {
        "notation": (
            "SPECIAL REQUIREMENT for Notation: your summary MUST explicitly enumerate the core symbols "
            "with definitions and units (e.g., 'λ = arrival rate (calls/h), μ = service rate (calls/h), "
            "v = 45 km/h (0.75 km/min)'). These exact symbols and values are what all later sections reuse."
        ),
        "solve": (
            "SPECIAL REQUIREMENT for Solution: your summary MUST state the concrete solution method AND "
            "at least one key numerical result with units (e.g., '5 stations, 11 ambulances, total cost 4.7M USD')."
        ),
        "analyze": (
            "SPECIAL REQUIREMENT for Analysis: your summary MUST state the data source — if the user's data "
            "is provided, summarize those REAL results; otherwise state explicitly that the test cases are "
            "PLACEHOLDER examples to be replaced, and still give the primary quantitative findings (numbers with units)."
        ),
    }

    parts = [
        f"# Task: Outline the **{heading}** section of this HiMCM paper.",
        "",
        "## Section writing instructions (from the paper engine — the FULL version must satisfy these)",
        instructions,
        "",
        "## The problem",
        problem_md,
    ]

    if user_thought.strip():
        parts.extend([
            "",
            "## Student's initial approach (their own words)",
            user_thought,
        ])

    if user_data.strip():
        parts.extend([
            "",
            "## User's real data / results (authoritative — analyze THESE, never invent numbers that contradict them)",
            user_data,
        ])

    if previous_context.strip():
        parts.extend([
            "",
            "## Previously written sections / defined symbols & models",
            "Use the SAME symbols, values, and models below. Do not introduce undefined symbols.",
            previous_context,
        ])

    if kb_context.strip():
        parts.extend([
            "",
            "## Knowledge base references",
            kb_context,
        ])

    if literature_context.strip():
        parts.extend([
            "",
            "## Relevant academic literature (cite by title/authors/year)",
            literature_context,
        ])

    if section_key in outline_notes:
        parts.extend(["", outline_notes[section_key]])

    parts.extend([
        "",
        "## Output format",
        "Return ONLY a JSON object (no markdown fences, no explanations):",
        '{"summary": "...", "formulas": ["$...$"], "citations": [{"title":"...","authors":"...","year":2023}]}',
        "",
        "Requirements:",
        "- summary: concise but COMPLETE and conclusion-oriented (what this section CONCLUDES, not what it will do). "
        "Let length follow content — roughly 150-600 words; include critical numbers/symbols, do NOT truncate them.",
        "- formulas: 1-2 key LaTeX formulas; symbols MUST match the Notation above.",
        "- citations: 1-2 papers from the literature above; empty [] if none match.",
    ])
    return "\n".join(parts)


def _repair_latex_backslashes(text: str) -> str:
    """修复 LLM 在 JSON 字符串里未转义的 LaTeX 反斜杠（\\( \\) \\in \\{ \\lambda \\frac 等）。

    仅在反斜杠后跟真正的 JSON 转义序列（\\" \\\\ \\/ \\uXXXX）时保留原样；其余一律补一个
    反斜杠，使其成为合法 JSON。这样 json.loads 才能把 \\\\( 还原成 \\(、\\\\mu 还原成 \\mu。
    注意：不能把 \\f/\\n/\\r/\\t/\\b 当 JSON 转义保留——LaTeX 里 \\frac、\\neq、\\tau、
    \\rho、\\theta、\\beta 更常见，误判会把公式拆成换行/制表符。
    """
    hexd = set("0123456789abcdefABCDEF")
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in ('"', "\\", "/"):
                out.append(text[i]); out.append(nxt); i += 2
            elif nxt == "u" and i + 5 < n and all(c in hexd for c in text[i + 2:i + 6]):
                out.append(text[i:i + 6]); i += 6
            else:
                out.append("\\\\"); out.append(nxt); i += 2
        else:
            out.append(text[i]); i += 1
    return "".join(out)


def _parse_outline_json(raw: str, heading: str) -> dict:
    """从 LLM 返回中容错解析大纲 JSON 对象。失败时降级为纯文本 summary。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    l, r = text.find("{"), text.rfind("}")
    if l == -1 or r == -1:
        logger.warning(f"大纲 JSON 无法定位花括号（{heading}）：{raw[:200]}")
        return {"summary": raw[:300], "formulas": [], "citations": []}
    candidate = text[l:r + 1]

    data = None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # 常见原因：LaTeX 反斜杠未转义导致 JSON 非法。修复后重试一次。
        try:
            data = json.loads(_repair_latex_backslashes(candidate))
        except json.JSONDecodeError:
            logger.warning(f"大纲 JSON 解析失败（{heading}）：{raw[:200]}")

    if not isinstance(data, dict):
        return {"summary": raw[:300], "formulas": [], "citations": []}
    return {
        "summary": str(data.get("summary") or "").strip(),
        "formulas": data.get("formulas") or [],
        "citations": data.get("citations") or [],
    }


def generate_outline_section(
    conn,
    session_id: str,
    section_key: str,
    user_thought: str = "",
    user_data: str = "",
    literature_context: str = "",
) -> Generator[dict, None, None]:
    """生成单个章节的「大纲摘要」，复用 generate_section 的逐节流水线 + 填表约束。

    复用：
    - _stage_instructions：每章专属写作/填表指令（notation 强制符号表、build 集成方法库、analyze 领域自适应 + 数据来源诚实）
    - _build_structured_context：读前序章节的符号/模型/结果（符号闭环）
    - _kb_for_stage：知识库检索
    - _extract_symbols/_extract_models/_extract_results：结构化提取写回 DB（保证后续章节一致）

    输出降级为 JSON {summary, formulas, citations}，而非完整论文正文。

    yield SSE 事件：
      progress  — {step, message}
      outline_section — {section_key, title, summary, formulas, citations, needs_user_data, data_checklist}
      done      — 结束
      error     — 失败
    """
    meta = _SECTION_META.get(section_key, {})
    heading = meta.get("heading", section_key)

    # 1. 加载论文会话，读 problem_md
    ps = paper_db.load_session(conn, session_id)
    if ps is None:
        yield {"type": "error", "message": f"论文会话 {session_id} 不存在"}
        yield {"type": "done"}
        return
    problem_md = ps.get("problem_md", "")

    # 2. 每章专属指令 + 前序结构化上下文 + 知识库检索
    previous_context = _build_structured_context(conn, session_id, section_key)
    kb_ctx = _kb_for_stage(section_key, "")

    # 3. 组装大纲 prompt 并调用 LLM
    user_prompt = _build_outline_prompt(
        section_key, heading, problem_md, user_thought, user_data,
        previous_context, kb_ctx, literature_context,
    )
    raw = _call_writer(_OUTLINE_PERSONA, user_prompt)
    if raw is None:
        yield {"type": "error", "message": f"「{heading}」大纲生成失败（LLM 调用错误）"}
        yield {"type": "done"}
        return

    data = _parse_outline_json(raw, heading)
    summary = data["summary"]

    # 4. 写回 section_state（供后续章节 _build_structured_context 引用，形成符号/数值闭环）
    # 注意：status 必须落在 CHECK 约束的合法集合内（无 "outline"），故用 "draft"。
    # 大纲会话是独立新建的，不会与完整论文的 draft 内容混淆。
    if summary:
        paper_db.upsert_section(conn, session_id, section_key, summary, status="draft")

    # 5. 结构化提取写回 DB（符号/模型/结果闭环）
    if section_key == "notation":
        try:
            _extract_symbols(conn, session_id, summary + "\n\n" + "\n".join(data["formulas"]))
        except Exception:
            logger.exception("大纲符号提取失败（非致命）")
    elif section_key == "build":
        try:
            _extract_models(conn, session_id, summary)
        except Exception:
            logger.exception("大纲模型提取失败（非致命）")
    elif section_key in ("solve", "analyze"):
        try:
            _extract_results(conn, session_id, section_key, summary)
        except Exception:
            logger.exception("大纲结果提取失败（非致命）")

    yield {
        "type": "outline_section",
        "section_key": section_key,
        "title": heading,
        "summary": summary,
        "formulas": data["formulas"],
        "citations": data["citations"],
        "needs_user_data": section_key in _DATA_CHECKLIST,
        "data_checklist": _DATA_CHECKLIST.get(section_key, ""),
    }
    yield {"type": "done"}


# ── 拼装 ─────────────────────────────────────────────────────────────────────

def assemble(conn, session_id: str) -> str:
    """读取所有 section_state,拼装成完整论文 Markdown。"""
    import re
    sections = paper_db.load_approved_sections(conn, session_id)
    by_key = {s["section_key"]: s for s in sections}

    def _strip_heading(md: str) -> str:
        """去掉 LLM 自带的第一行 Markdown 标题（assemble 会自己加）。"""
        lines = md.strip().split("\n")
        if lines and re.match(r"^#{1,3}\s", lines[0]):
            return "\n".join(lines[1:]).strip()
        return md.strip()

    parts: list[str] = []
    # abstract 在最前（如已生成）
    abs_s = by_key.get("abstract")
    if abs_s and (abs_s.get("content_md") or "").strip():
        parts.append(f"# Abstract\n\n{_strip_heading(abs_s['content_md'])}")
        parts.append("")

    # 其余节按 order
    for sk in paper_db.SECTION_ORDER:
        if sk == "abstract":
            continue
        meta = _SECTION_META.get(sk, {})
        heading = meta.get("heading", sk)
        num = meta.get("num", 0)
        s = by_key.get(sk)
        content = (s.get("content_md") or "").strip() if s else ""
        if not content:
            continue
        parts.append(f"# {num}. {heading}\n\n{_strip_heading(content)}")
        parts.append("")

    # 参考文献：从文献知识库收集
    try:
        from . import paper_literature
        lit_conn = paper_literature.get_lit_conn()
        citations = paper_literature.load_citations(lit_conn, session_id)

        if citations:
            parts.append("# References\n")
            # 按paper_id去重并格式化
            seen_papers = set()
            for citation in citations:
                paper_id = citation["paper_id"]
                if paper_id in seen_papers:
                    continue
                seen_papers.add(paper_id)

                authors_list = json.loads(citation["authors"])
                authors_str = authors_list[0] if authors_list else "Unknown"
                if len(authors_list) > 1:
                    authors_str += " et al."

                year = citation.get("published", "")[:4] or "n.d."
                title = citation["title"]

                parts.append(f"- [{paper_id}] {authors_str} ({year}). *{title}*.")

            parts.append("")
        lit_conn.close()
    except Exception as e:
        logger.warning(f"加载文献引用失败（非致命）: {e}")

    # Fallback: 从 model_cards 收集（保留原有逻辑）
    models = paper_db.load_model_cards(conn, session_id)
    if models:
        refs = []
        for m in models:
            c = (m.get("citation") or "").strip()
            if c:
                refs.append(c)
        if refs:
            if "# References\n" not in parts:
                parts.append("# References\n")
            for r in refs:
                parts.append(f"- {r}")
            parts.append("")

    return "\n".join(parts).strip()


# ── 全篇一键生成（便利包装）──────────────────────────────────────────────────

def generate_all(
    conn,
    session_id: str,
    solve_session: dict | None = None,
) -> Generator[dict, None, None]:
    """遍历 SECTION_ORDER 逐节生成，yield 每节的 SSE 结果。"""
    for sk in paper_db.SECTION_ORDER:
        yielded_result = False
        for ev in generate_section(conn, session_id, sk, solve_session):
            if ev.get("type") == "error":
                yield ev
                break  # 这节失败，继续下一节
            if ev.get("type") == "result":
                yielded_result = True
            yield ev
        if not yielded_result:
            # generate_section 没有产出 result（可能 LLM 失败），继续
            pass
    yield {"type": "all_done", "message": "全篇生成完成"}

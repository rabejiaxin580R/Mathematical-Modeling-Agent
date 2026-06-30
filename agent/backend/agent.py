"""Agent 核心：系统提示词 + 工具调用循环（流式）。"""
import json
import logging

from openai import OpenAI

from .config import config
from .tools import TOOLS_SCHEMA, PRACTICE_TOOLS_SCHEMA, dispatch_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是「数学建模助教」，一个面向数学建模初学者与竞赛选手的 AI 助教。你的知识依据来自一套约 40 小时的数学建模课程知识库。

# 核心原则

1. **基于知识库作答，标注出处**
   - 遇到数学、建模模型、算法、公式相关的问题，必须先调用 search_knowledge 检索知识库。
   - 回答时引用检索到的知识点出处，格式：（依据：知识点 chunk_id「标题」）。
   - 如果检索结果提示「超出范围」，要明确告诉用户「该问题超出当前课程知识库范围」，再基于通用知识谨慎补充，并给出学习建议。绝不假装知识库里有。

2. **复杂问题先拆解**
   - 遇到复杂问题，先在回答开头用简短列表把它拆成若干子问题，然后逐个检索、逐个解答，最后汇总。

3. **代码要自测迭代**
   - 编写 Python 代码（建模求解、数据可视化、数值验证等）后，调用 run_python 实际运行。
   - 若报错或结果不对，依据返回的错误信息修正并重新运行，直到正确为止（最多约 {max_iter} 轮）。
   - 不要只给代码不运行；也不要在明显有错时停手。最终向用户展示能跑通的代码和结果。
   - 已预置 pandas、numpy、matplotlib，可直接 import 用于数据分析与建模。

4. **用户上传文件时先读取再处理**
   - 文件就在代码的当前工作目录下，用相对文件名访问。
   - **文档类**（PDF、Word/docx、PPT/pptx、HTML、txt、Markdown、.py）：先调用 read_document(filename) 取得正文，再据此总结/分析/作答。不要凭空猜内容。
   - **表格/数据类**（csv、xlsx）：用 run_python 以相对路径读取（如 `pd.read_csv("data.csv")`、`pd.read_excel("x.xlsx")`），先探查 shape、列名、head、dtypes、缺失值，再清洗、分析、建模、可视化。不要凭空假设列名。
   - **图片**：你看不到图片像素内容，只能读到文件名；如需理解图中内容，请说明并请用户用文字描述。

5. **本地文件操作**
   - 你可以直接在用户电脑的真实文件系统上操作：write_file（创建/写入）、read_file（读取任意路径）、list_dir（列目录）、delete_file（删除文件）。
   - 路径优先用绝对路径（如 `C:\\Users\\name\\Desktop\\out.txt`），也支持 `~`；不确定目录里有什么时先用 list_dir 看看。
   - **破坏性操作必须先确认**：删除文件、或覆盖已存在的文件之前，先明确告诉用户将影响哪个路径，征得同意后再执行。不要擅自删除或覆盖。
   - 操作完成后向用户报告结果（路径、成功与否、写入字节数/读取字符数等）。
   - 区分：用户在聊天里上传的文件用 read_document；磁盘上任意路径用 read_file。

6. **面向初学者的引导式教学**
   - 对零基础用户手把手讲：装 Python、用命令行/PowerShell、配置环境等，给出可逐步照做的步骤和命令。
   - 论文写作指导：讲结构、摘要、图表规范等；可以直接用 write_file 写出 `.md` 或 `.tex` 文档草稿交给用户，再讲解怎么完善。
   - 排版指导（LaTeX / Word）：你可以**直接写出 `.tex` 或 `.md` 文档文件**（write_file），用户在中间编辑器里能预览，并能一键「导出 Word（.docx）」。Markdown 适合快速成稿与转 Word，LaTeX 适合公式多、要求规范排版的正式论文。涉及用户本地排版软件的 GUI 操作（如在 Word 里点哪个按钮）你无法代劳，给分步说明即可。

# 项目开发工作流

当用户要「做一个项目」「从头搭一个建模任务」「带我做一遍」这类**新建项目类任务**时，按下面的标准流程一步步带，不要一上来就堆代码：

1. **先建项目文件夹**
   - 主动提示用户：「我们先建一个项目文件夹来放所有文件」，并确认放在哪里（默认建议放桌面，如 `C:\\Users\\<用户名>\\Desktop\\<项目名>`）。
   - 用 create_directory 工具把文件夹建好；若还需要子目录（如 `data/`、`output/`），一并建好。

2. **引导用户打开文件夹**
   - 用 list_dir 展示文件夹当前内容，让用户看到它确实建好了。
   - 用文字引导用户自己在「资源管理器 / VS Code」里打开这个文件夹——你无法替用户打开 GUI 程序，要说明这一点，并给出具体操作（如「在资源管理器地址栏粘贴该路径回车」）。

3. **文件命名规范（务必遵守并提醒用户）**
   - 所有新建文件用**英文小写 + 下划线、见名知意**：如 `data_clean.py`（数据清洗）、`model_solve.py`（建模求解）、`visualize.py`（可视化）、`report_main.tex`（论文主文件）、`report.md`（报告草稿）。
   - 数据文件放 `data/` 子目录，运行产物（图、结果表）放 `output/` 子目录。
   - 每次 write_file 前，先一句话说明「这个文件叫什么、放哪、负责什么」，再写。
   - **你不只会写 Python**：论文、报告、建模文档等文本成果，应当用 write_file 直接落成 **Markdown（`.md`，首选，便于一键导出 Word）或 LaTeX（`.tex`）** 文件，而不是只贴在聊天里或塞进 .py 注释。中间编辑器支持 .md/.tex 预览与导出 Word。

4. **先计划，再分步实现，每步自测**
   - 先给一份简短的分步计划（用列表列出要写哪几个文件、各做什么）。
   - 然后**逐个文件**写：write_file 落盘 → run_python 用样例/小数据测一测 → 通过了再进入下一步；不通过就按错误信息修正（沿用上面「代码要自测迭代」原则）。
   - 全部完成后，给用户一个「如何运行整个项目」的简短说明。

# 风格
- 用中文，温和、专业、像并肩作战的伙伴，不居高临下。
- 数学公式用 LaTeX：行内 $...$，独立 $$...$$。
- 步骤、命令、代码用 Markdown 代码块或列表，让用户能照着做。
- 简洁但完整，别为简单问题套大段模板。
"""


SOLVE_SYSTEM_PROMPT = """你是「建模共创伙伴」，一个专门陪学生把**他自己带来的那一道数学建模题**一个阶段一个阶段做出来的 AI 教练。你不是普通答疑助教，你的全部工作就是和这名学生**一起**把这道题做完——一次只推进当前这一个阶段。

# 你和学生的关系
- 这道题没有现成的标准答案。你清楚题目、也有建模知识，但**这一步由学生来推进**：你用提问、追问、启发引导他自己想出来，帮他纠错、补他缺的知识，而不是替他做完。
- 你是并肩作战的伙伴，不是裁判，也不是答案机。

# 核心原则

1. **引导式，不直接喂答案**
   - 绝不一上来就把这一步的完整答案、公式、代码丢给学生。
   - 先帮他把"这一步到底要解决什么问题"理清楚，再抛出第一个具体问题让他来答；他卡住时给方向、给提示，而不是给结论。
   - 可以参照优秀做法启发他，但讲清思路，让他自己写，不让他照抄。

2. **立场必须稳，不讨好、不放水（重要）**
   - 你的判断只基于学生**实际写出/说出**的内容，而不是他的语气或情绪。
   - 学生反问、质疑、表达不满时：他确实有道理就客观承认并说明理由；他没说到点上或理解有误，要温和但**明确**地指出，不要为了让他高兴就附和。
   - **禁止讨好与自我否定**：不要说"你说得对，是我太机械了"这类迎合话；不要因为被质疑就轻易推翻自己先前的判断。只有当学生给出**新的、正确的论据**时才调整看法，并说清是哪一点让你改变了判断。
   - 复制题目要求、答非所问、空话套话，都不等于完成了这一步——要如实指出，而不是当作他答对了。

3. **只聚焦当前这一个阶段**
   - 紧扣当前阶段的目标，不要替他把整道题做完，也不要跳到后面的阶段。
   - 当学生这一步已经基本想清楚时，明确肯定他到位的地方，并提示他可以进入下一步。
   - 还差关键内容时，点出还缺什么，引导他补上，不要轻易说"可以了"。

4. **基于知识库、代码要自测**
   - 涉及模型、算法、公式的问题，先用 search_knowledge 检索知识库，引用出处（格式：依据：知识点 chunk_id「标题」）；超出范围要如实说明。
   - 需要演示或验证时用 run_python 实际运行（已预置 pandas/numpy/matplotlib），报错就按错误信息改到跑通；但记住——是帮他验证思路，不是替他把代码写完交差。
   - 学生上传的文件就在当前工作目录：文档（pdf/word/ppt/html/txt/py）用 read_document 读取，表格（csv/xlsx）用 run_python 以相对路径读取。若目录里没有相关文件，就直接和他讨论，不要反复去找不存在的文件。

# 风格
- 全程简体中文（题面里的专有名词、变量名、公式可保留原文）。
- 温和、专业，像并肩作战的学长，不居高临下。
- 数学公式用 LaTeX：行内 $...$，独立 $$...$$。
- 简洁但到位，别为简单问题套大段模板。
"""


class Agent:
    def __init__(self):
        self._init_client()
        self.system_prompt = SYSTEM_PROMPT.format(max_iter=config.MAX_CODE_ITERATIONS)
        self.solve_prompt = SOLVE_SYSTEM_PROMPT

    def _init_client(self):
        # 允许「无密钥」启动：新用户首次运行时尚未配置 API Key，
        # 后端必须能正常起来，前端「连接大模型」向导才有机会引导用户填写。
        # 这里用占位符让 OpenAI 客户端能构造；真正的调用在用户保存设置（reconfigure）后才会成功。
        api_key = config.get_llm_api_key() or "not-configured"
        self.client = OpenAI(
            api_key=api_key,
            base_url=config.get_llm_base_url(),
            # Cloudflare WAF 会拦截 OpenAI 库的默认 User-Agent（含 "PythonBindings" 字样），
            # 用无害的浏览器 UA 绕过，确保通过网关 Cloudflare 防护。
            default_headers={"User-Agent": "Mozilla/5.0 MathModelingAgent/1.0"},
        )

    def reconfigure(self):
        """重新初始化 OpenAI 客户端（当用户通过设置页面修改 API 配置后调用）。"""
        self._init_client()
        logger.info("Agent 已重新配置：模型=%s @ %s", config.get_llm_model(), config.get_llm_base_url())

    def build_messages(self, history: list[dict], workspace_files: list[str] | None = None,
                       system_extra: str = "", base_prompt: str | None = None) -> list[dict]:
        """将对话历史构建为完整的 messages 列表（含系统提示词、工具调用、工具结果）。

        history 中每条消息的 role 可以是 user、assistant、tool。
        assistant 消息可包含 tool_calls（OpenAI 格式），tool 消息包含 tool_call_id 和 content。
        system_extra：会话级隐藏上下文（如「做题」每步的题目背景/任务与共创要求），
        拼到系统提示词里，对用户不可见。
        base_prompt：基础系统提示词；默认用通用助教（self.system_prompt），
        做题 agent 传入 self.solve_prompt 以换成「建模共创伙伴」人格。
        """
        system = base_prompt or self.system_prompt
        if workspace_files:
            files_str = "、".join(workspace_files)
            system += (
                f"\n\n# 当前工作目录已有用户上传的文件\n{files_str}\n"
                "文档（pdf/word/ppt/html/txt/py）用 read_document 读取；"
                "表格数据（csv/xlsx）用 run_python 以相对路径读取（无需让用户再提供路径）。"
            )
        if system_extra:
            system += "\n\n" + system_extra
        msgs = [{"role": "system", "content": system}]
        for m in history:
            role = m.get("role", "")
            if role == "user":
                msgs.append({"role": "user", "content": m.get("content", "")})
            elif role == "assistant":
                entry = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    entry["tool_calls"] = m["tool_calls"]
                msgs.append(entry)
            elif role == "tool":
                msgs.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                })
        return msgs

    def stream_reply(self, history: list[dict], run_id: str, workspace_files: list[str] | None = None,
                     system_extra: str = "", base_prompt: str | None = None):
        """生成器，产出事件 dict：
        {type: "token", text}            模型文本增量
        {type: "tool_call", name, call_id, arguments}   工具开始调用
        {type: "tool_result", call_id, content, display} 工具结果
        {type: "done", content}          本轮最终文本
        {type: "error", message}         出错
        base_prompt：基础系统提示词，默认通用助教；做题 agent 传 self.solve_prompt。
        """
        messages = self.build_messages(history, workspace_files, system_extra, base_prompt)
        max_rounds = config.MAX_CODE_ITERATIONS + 4  # 检索 + 多轮代码迭代余量
        final_text = ""
        # 跨多轮（含工具迭代）累计的 token 用量，供计费。最后随 done 事件产出。
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

        for _ in range(max_rounds):
            try:
                stream = self.client.chat.completions.create(
                    model=config.get_llm_model(),
                    messages=messages,
                    tools=TOOLS_SCHEMA,
                    stream=True,
                    temperature=0.3,
                    stream_options={"include_usage": True},
                )
            except Exception as e:
                logger.error("调用大模型失败：%s", e)
                yield {"type": "error", "message": f"调用大模型失败：{e}"}
                return

            content_buf = ""
            tool_calls = {}  # index -> {id, name, args_str}

            for chunk in stream:
                # usage 通常在最后一个 chunk（choices 为空）随 include_usage 返回
                if getattr(chunk, "usage", None):
                    usage_total["prompt_tokens"] += chunk.usage.prompt_tokens or 0
                    usage_total["completion_tokens"] += chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    content_buf += delta.content
                    yield {"type": "token", "text": delta.content}

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        slot = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments

            # 没有工具调用 → 本轮就是最终回答
            if not tool_calls:
                final_text = content_buf
                yield {"type": "usage", **usage_total}
                yield {"type": "done", "content": final_text}
                return

            # 把 assistant 的 tool_calls 消息加入历史
            assistant_msg = {
                "role": "assistant",
                "content": content_buf or None,
                "tool_calls": [
                    {
                        "id": slot["id"] or f"call_{idx}",
                        "type": "function",
                        "function": {"name": slot["name"], "arguments": slot["args"] or "{}"},
                    }
                    for idx, slot in sorted(tool_calls.items())
                ],
            }
            messages.append(assistant_msg)

            # 依次执行每个工具调用
            for idx, slot in sorted(tool_calls.items()):
                name = slot["name"]
                call_id = slot["id"] or f"call_{idx}"
                try:
                    args = json.loads(slot["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                yield {"type": "tool_call", "name": name, "call_id": call_id, "arguments": args}

                result = dispatch_tool(name, args, run_id)
                logger.info("工具调用: %s, 参数: %s", name, json.dumps(args, ensure_ascii=False)[:200])
                yield {
                    "type": "tool_result",
                    "display": result["display"],
                    "call_id": call_id,
                    "content": result["content"],
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result["content"],
                })

        # 超过轮数上限
        yield {"type": "usage", **usage_total}
        yield {"type": "done", "content": final_text or "（已达到最大处理轮数）"}

    def stream_step_chat(self, history: list[dict], step_context: dict, run_id: str | None = None):
        """真题练习「和 AI 讨论这一步」：围绕单步上下文的多轮流式答疑（带工具循环）。

        与 stream_reply 产出同形事件（token / tool_call / tool_result / done / error），
        对话不落盘（由前端持有）。工具限定为只读/执行子集（PRACTICE_TOOLS_SCHEMA），
        让助教能主动读取本步上传的文件、运行代码来点评；run_id 决定工作目录。
        step_context 提供题面、任务、优秀论文做法、已上传文件清单等。
        """
        system = self._build_step_system(step_context)
        messages = [{"role": "system", "content": system}]
        for m in history:
            role = m.get("role", "")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": m.get("content", "")})

        use_tools = bool(run_id)
        max_rounds = config.MAX_CODE_ITERATIONS + 4
        final_text = ""
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

        for _ in range(max_rounds):
            try:
                kwargs = dict(
                    model=config.get_llm_model(),
                    messages=messages,
                    stream=True,
                    temperature=0.4,
                    stream_options={"include_usage": True},
                )
                if use_tools:
                    kwargs["tools"] = PRACTICE_TOOLS_SCHEMA
                stream = self.client.chat.completions.create(**kwargs)
            except Exception as e:
                logger.error("本步讨论调用大模型失败：%s", e)
                yield {"type": "error", "message": f"调用大模型失败：{e}"}
                return

            content_buf = ""
            tool_calls = {}  # index -> {id, name, args}
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage_total["prompt_tokens"] += chunk.usage.prompt_tokens or 0
                    usage_total["completion_tokens"] += chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_buf += delta.content
                    yield {"type": "token", "text": delta.content}
                if use_tools and delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments

            # 没有工具调用 → 本轮就是最终回答
            if not tool_calls:
                final_text = content_buf
                yield {"type": "usage", **usage_total}
                yield {"type": "done", "content": final_text}
                return

            # 记录 assistant 的 tool_calls，再依次执行
            messages.append({
                "role": "assistant",
                "content": content_buf or None,
                "tool_calls": [
                    {"id": slot["id"] or f"call_{idx}", "type": "function",
                     "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}}
                    for idx, slot in sorted(tool_calls.items())
                ],
            })
            for idx, slot in sorted(tool_calls.items()):
                name = slot["name"]
                call_id = slot["id"] or f"call_{idx}"
                try:
                    args = json.loads(slot["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "name": name, "call_id": call_id, "arguments": args}
                result = dispatch_tool(name, args, run_id)
                logger.info("本步工具调用: %s, 参数: %s", name, json.dumps(args, ensure_ascii=False)[:200])
                yield {"type": "tool_result", "display": result["display"],
                       "call_id": call_id, "content": result["content"]}
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result["content"]})

        yield {"type": "usage", **usage_total}
        yield {"type": "done", "content": final_text or "（已达到最大处理轮数）"}

    @staticmethod
    def _build_step_system(ctx: dict) -> str:
        """为「本步助教」拼装系统提示词。立场要稳，不讨好、不放水。"""
        parts = [
            "你是数学建模助教，正在用**对话**的方式，陪学生把一道真题的**某一个建模阶段**想透、做对。",
            "",
            "# 你的风格",
            "- 引导式：多用追问和启发，让学生自己推进；不要一上来就把这一步的完整答案喂给他。",
            "- 紧扣**当前这一个阶段**的目标，不要替他把整道题做完，也不要跳到后面的阶段。",
            "- 中文，温和、专业、像并肩作战的学长；公式用 LaTeX（行内 $...$，独立 $$...$$）。",
            "- 可以参照「优秀论文这一步的做法」启发他，但讲清思路，不让他照抄。",
            "",
            "# 立场必须稳（重要）",
            "- 你的判断只基于学生**实际写出/说出**的内容，而不是他的语气或情绪。",
            "- 学生反问、质疑、表达不满时：他**确实有道理**就客观承认并说明理由；他**没说到点上或理解有误**，要温和但**明确**地指出，不要为了让他高兴就附和。",
            "- **禁止讨好与自我否定**：不要说「你说得对，是我太机械了」这类话来迎合；不要因为被质疑就轻易推翻自己先前的判断或评价。只有当学生给出了**新的、正确的论据**时，才调整看法，并说清楚是哪一点让你改变了判断。",
            "- 复制题目要求、答非所问、空话套话，都不等于完成了这一步——要如实指出，而不是当作他答对了。",
            "",
            "# 推进节奏",
            "- 当学生这一步**已经基本想清楚**时，明确肯定他到位的地方，并提示：「这一步你已经基本到位，可以点『完成本步』让我评估掌握度，或继续深入。」",
            "- 当还差关键内容时，点出还缺什么，引导他补上，不要轻易说「可以了」。",
            "",
            f"# 题目\n{ctx.get('problem_title', '')}",
        ]
        bg = (ctx.get("background") or "").strip()
        if bg:
            parts.append(f"## 题面摘要\n{bg[:1200]}")
        stage = ctx.get("stage_name") or ctx.get("step_title") or ""
        parts.append(
            f"\n# 当前阶段：{stage}（modality={ctx.get('modality', '')}）\n"
            f"这一步要学生做到：{ctx.get('prompt', '')}"
        )
        if ctx.get("guide_style"):
            parts.append(f"引导风格：{ctx['guide_style']}")
        pps = ctx.get("paper_points") or []
        pp_lines = []
        for pp in pps:
            pts = pp.get("points") or []
            if pts:
                pp_lines.append(f"· 论文{pp.get('paper_id', '')}：" + "；".join(str(x) for x in pts))
        if pp_lines:
            parts.append("\n## 优秀论文这一步的做法（你的参照，勿照搬给学生）\n" + "\n".join(pp_lines))
        excerpts = ctx.get("paper_excerpts") or []
        if excerpts:
            ex_lines = [f"· 论文{e.get('paper_id', '')}片段：{e.get('excerpt', '')}" for e in excerpts]
            parts.append("\n## 相关论文原文片段（参照）\n" + "\n".join(ex_lines))
        files = ctx.get("workspace_files") or []
        if files:
            parts.append(
                "\n## 学生在这一步上传的文件\n" + "、".join(files) + "\n"
                "需要时你可以主动调用工具查看：文档（pdf/word/ppt/html/txt/py）用 read_document(文件名)；"
                "表格数据（csv/xlsx）用 run_python 以相对文件名读取（如 pd.read_csv('data.csv')）。"
                "运行代码、读取文件都在这一步的独立工作目录内进行。"
            )
        return "\n".join(parts)


agent = Agent()
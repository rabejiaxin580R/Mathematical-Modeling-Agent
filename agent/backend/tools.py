"""大模型可调用的工具定义（OpenAI function-calling 格式）与执行分发。"""
import json

from .knowledge import knowledge_base
from .executor import run_python
from .documents import read_document
from . import fileops
from .config import config

# OpenAI tools schema
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "在数学建模课程知识库中检索相关知识点。当用户提出数学/建模概念、"
                "模型、算法、公式相关问题时，必须先调用此工具获取有出处的依据。"
                "返回的每个知识点都带有 chunk_id，回答时需引用它作为出处。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索关键词或子问题，使用中文，尽量具体。",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "在本地执行 Python 代码并返回 stdout/stderr 及生成的图片。"
                "用于数学建模求解、数据可视化、数值验证等。"
                "若执行出错或结果不理想，你应根据返回的错误信息修正代码并再次调用，"
                "直到得到正确结果。已预置 matplotlib（Agg 后端、中文字体），"
                "plt 生成的图会自动保存为图片返回。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的完整 Python 代码。",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": (
                "读取用户上传到当前工作目录的文档并返回其文本内容。"
                "支持 PDF、Word(docx)、PPT(pptx)、HTML、txt、Markdown、Python(.py) 等。"
                "当用户上传了这类文档并就其内容提问时，先调用此工具拿到正文再作答。"
                "注意：表格类数据（csv/xlsx）请改用 run_python + pandas；"
                "图片内容需视觉模型才能理解，本工具读不出图中文字。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "工作目录下的文件名（相对名，如 report.pdf）。",
                    }
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "在用户电脑的真实文件系统上创建/写入一个文本文件，会自动创建缺失的父目录。"
                "建议用绝对路径（如 C:\\\\Users\\\\name\\\\Desktop\\\\out.txt），也支持 ~。"
                "默认覆盖已存在文件——覆盖属破坏性操作，执行前应先向用户说明并确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径（建议绝对路径）。"},
                    "content": {"type": "string", "description": "要写入的文本内容。"},
                    "overwrite": {"type": "boolean", "description": "目标已存在时是否覆盖，默认 true。"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取用户电脑上任意路径的文件内容。富格式（PDF/Word/PPT/Excel/HTML）会自动解析为文本，"
                "其余按纯文本读取。建议用绝对路径，支持 ~。"
                "注：聊天中上传的文件可继续用 read_document。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径（建议绝对路径）。"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出用户电脑上某个目录下的文件与子目录。建议用绝对路径，支持 ~。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要列出的目录路径。"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": (
                "在用户电脑上创建一个目录（文件夹），会自动创建缺失的父目录。"
                "用于「项目开发工作流」第一步——为新项目建立项目文件夹及其子目录"
                "（如 data/、output/）。建议用绝对路径（如 C:\\\\Users\\\\name\\\\Desktop\\\\my_project），"
                "也支持 ~。目录已存在时视为成功，不会报错。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径（建议绝对路径）。"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": (
                "删除用户电脑上的单个文件（出于安全不删除目录）。"
                "删除不可恢复，属破坏性操作，执行前必须先向用户说明将删除哪个路径并取得确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件路径（建议绝对路径）。"},
                },
                "required": ["path"],
            },
        },
    },
]


# 真题练习「本步对话」用的只读/执行工具子集（不含 write/delete/create_directory，
# 助教不应在学生电脑上写删文件）。read_file/list_dir 仅供查看，run_python 限定在该步工作目录。
_PRACTICE_TOOL_NAMES = {"search_knowledge", "run_python", "read_document", "list_dir", "read_file"}
PRACTICE_TOOLS_SCHEMA = [t for t in TOOLS_SCHEMA if t["function"]["name"] in _PRACTICE_TOOL_NAMES]


def dispatch_tool(name: str, arguments: dict, run_id: str) -> dict:
    """执行一个工具调用，返回 {content: str(给模型), display: dict(给前端)}。"""
    if name == "search_knowledge":
        return _do_search(arguments.get("query", ""))
    if name == "run_python":
        return _do_run(arguments.get("code", ""), run_id)
    if name == "read_document":
        return read_document(arguments.get("filename", ""), run_id)
    if name == "write_file":
        return fileops.write_file(
            arguments.get("path", ""),
            arguments.get("content", ""),
            arguments.get("overwrite", True),
        )
    if name == "read_file":
        return fileops.read_file(arguments.get("path", ""))
    if name == "list_dir":
        return fileops.list_dir(arguments.get("path", "."))
    if name == "create_directory":
        return fileops.make_dir(arguments.get("path", ""))
    if name == "delete_file":
        return fileops.delete_file(arguments.get("path", ""))
    return {
        "content": f"未知工具：{name}",
        "display": {"type": "error", "message": f"未知工具：{name}"},
    }


def _do_search(query: str) -> dict:
    results = knowledge_base.search(query)
    citations = []

    # 相对阈值：BM25 分数不归一化，长/短 query 绝对值差异大，
    # 故用「最高分的比例」筛选，并以绝对下限判定是否真的超出知识库范围。
    top = results[0][1] if results else 0.0
    out_of_scope = top < config.RETRIEVAL_ABS_FLOOR
    keep_min = max(top * config.RETRIEVAL_RELATIVE_RATIO, config.RETRIEVAL_ABS_FLOOR)
    strong = [] if out_of_scope else [(u, s) for u, s in results if s >= keep_min]

    if not strong:
        content = (
            f"知识库中未检索到与「{query}」充分相关的内容"
            f"（最高得分 {top:.2f}，低于下限 {config.RETRIEVAL_ABS_FLOOR}）。"
            if results
            else f"知识库中未检索到与「{query}」相关的内容。"
        )
        content += "\n请明确告知用户该问题超出当前课程知识库范围，并基于通用知识谨慎作答或给出学习建议。"
        return {
            "content": content,
            "display": {"type": "search", "query": query, "citations": [], "out_of_scope": True},
        }

    context_parts = [f"检索「{query}」得到以下知识点（按相关度排序）：\n"]
    for u, score in strong:
        context_parts.append(u.to_context())
        context_parts.append("")  # 空行分隔
        citations.append({**u.to_citation(), "score": round(score, 2)})

    return {
        "content": "\n".join(context_parts),
        "display": {"type": "search", "query": query, "citations": citations, "out_of_scope": False},
    }


def _do_run(code: str, run_id: str) -> dict:
    result = run_python(code, run_id)
    return {
        "content": result.to_feedback(),
        "display": {
            "type": "code_run",
            "code": code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
            "timed_out": result.timed_out,
            "images": result.images,
        },
    }

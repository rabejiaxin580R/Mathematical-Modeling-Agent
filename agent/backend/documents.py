"""文档读取：把用户上传的 PDF / Word / PPT / HTML / 纯文本等转成 Markdown 文本，供模型读取。

底层用微软 MarkItDown：一个库统一处理多种格式，输出适合大模型消费的 Markdown。
纯文本（txt/py/md/html 等）也由它统一处理。

安全：文件名来自模型，故严格限定在 data/runs/<run_id>/ 工作目录内，防路径穿越。
"""
import logging
from pathlib import Path

from .config import config

logger = logging.getLogger(__name__)

# MarkItDown 实例懒加载（首次调用再创建，避免拖慢启动）
_md = None

# 与 MarkItDown 解析后端对应、本工具明确支持的扩展名
_DOC_EXTS = {
    ".txt", ".md", ".py", ".pdf", ".docx", ".doc",
    ".pptx", ".ppt", ".html", ".htm", ".csv", ".json",
}


def _get_md():
    global _md
    if _md is None:
        from markitdown import MarkItDown
        _md = MarkItDown()
    return _md


def _truncate(text: str, limit: int = 12000) -> str:
    if text and len(text) > limit:
        return text[:limit] + f"\n...[文档过长，已截断，共 {len(text)} 字符]"
    return text or ""


def _resolve_in_workdir(filename: str, run_id: str) -> Path | None:
    """把 filename 解析到工作目录内的真实路径；越界或不存在返回 None。"""
    workdir = (config.RUNS_DIR / run_id).resolve()
    # 仅取文件名部分，杜绝 ../ 与绝对路径
    name = (filename or "").replace("\\", "/").split("/")[-1]
    if not name:
        return None
    target = (workdir / name).resolve()
    try:
        target.relative_to(workdir)
    except ValueError:
        return None  # 越界
    return target if target.is_file() else None


def read_document(filename: str, run_id: str) -> dict:
    """读取工作目录下的文档，返回 {content: str(给模型), display: dict(给前端)}。"""
    path = _resolve_in_workdir(filename, run_id)
    if path is None:
        msg = f"未找到文件「{filename}」，请确认它已上传到当前会话。"
        return {"content": msg, "display": {"type": "document", "filename": filename, "error": msg}}

    ext = path.suffix.lower()
    if ext not in _DOC_EXTS:
        msg = (
            f"「{filename}」是 {ext or '无扩展名'} 类型，read_document 不支持。"
            "表格数据（csv/xlsx）请用 run_python 读取；图片需视觉模型才能理解内容。"
        )
        return {"content": msg, "display": {"type": "document", "filename": filename, "error": msg}}

    try:
        text = _get_md().convert(str(path)).text_content or ""
    except Exception as e:
        logger.warning("读取文档失败 %s: %s", filename, e)
        msg = f"读取「{filename}」失败：{e}"
        return {"content": msg, "display": {"type": "document", "filename": filename, "error": msg}}

    full_len = len(text)
    text = _truncate(text)
    content = f"文件「{filename}」的内容如下：\n\n{text}" if text else f"「{filename}」内容为空。"
    logger.info("读取文档: %s (%d 字符)", filename, full_len)
    return {
        "content": content,
        "display": {"type": "document", "filename": filename, "chars": full_len},
    }

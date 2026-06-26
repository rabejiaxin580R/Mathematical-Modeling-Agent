"""本地文件系统操作：read_file / write_file / list_dir / delete_file。

直接在用户电脑的真实文件系统上按任意路径操作（全盘访问，不做路径围栏），
权限与当前系统用户一致——这与现有 run_python 的信任模型相同（见 executor.py 的安全说明）。
仅限信任的本机单用户环境使用。破坏性操作（删除/覆盖）由系统提示词要求 agent 先与用户确认。

每个函数返回 {content: str(给模型), display: dict(给前端)}，display 统一为
{type:"file_op", action, ok, path, ...}，前端用单一分支渲染。
"""
import os
import logging
from pathlib import Path

from .config import config
from .documents import _get_md, _DOC_EXTS

logger = logging.getLogger(__name__)

# 这些富格式走 MarkItDown 提取；其余按纯文本读取
_RICH_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm"}


def _resolve(path: str) -> Path:
    """展开 ~ 与环境变量，转为绝对路径。相对路径按服务进程 cwd 解析。不做围栏。"""
    expanded = os.path.expandvars(os.path.expanduser((path or "").strip()))
    return Path(expanded).resolve()


def _ok(action: str, path: Path, content: str, **extra) -> dict:
    return {"content": content, "display": {"type": "file_op", "action": action, "ok": True, "path": str(path), **extra}}


def _err(action: str, path_str: str, message: str) -> dict:
    return {"content": message, "display": {"type": "file_op", "action": action, "ok": False, "path": path_str, "error": message}}


def _truncate(text: str) -> tuple[str, int]:
    """按配置上限截断，返回 (截断后文本, 原始长度)。"""
    full = len(text or "")
    limit = config.FILEOPS_READ_CHAR_LIMIT
    if text and full > limit:
        return text[:limit] + f"\n...[内容过长，已截断，共 {full} 字符]", full
    return text or "", full


def write_file(path: str, content: str, overwrite: bool = True) -> dict:
    """在任意路径创建/写入文本文件，自动创建父目录。"""
    if not path or not path.strip():
        return _err("write", path or "", "未提供文件路径。")
    target = _resolve(path)
    content = content or ""
    try:
        if target.exists():
            if target.is_dir():
                return _err("write", str(target), f"目标「{target}」是一个目录，无法写入。")
            if not overwrite:
                return _err("write", str(target), f"文件「{target}」已存在；如需覆盖请确认后再写（overwrite=true）。")
            existed = True
        else:
            existed = False
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
    except OSError as e:
        return _err("write", str(target), f"写入「{target}」失败：{e}")

    verb = "覆盖写入" if existed else "创建"
    msg = f"已{verb}文件：{target}（{len(data)} 字节）。"
    logger.info("write_file: %s (%d bytes, overwrite=%s)", target, len(data), existed)
    return _ok("write", target, msg, bytes=len(data), overwritten=existed)


def read_file(path: str) -> dict:
    """读取任意路径的文件内容。富格式经 MarkItDown 提取，其余按文本读取。"""
    if not path or not path.strip():
        return _err("read", path or "", "未提供文件路径。")
    target = _resolve(path)
    if not target.exists():
        return _err("read", str(target), f"路径不存在：{target}")
    if target.is_dir():
        return _err("read", str(target), f"「{target}」是目录，请改用 list_dir 查看其内容。")
    try:
        size = target.stat().st_size
    except OSError as e:
        return _err("read", str(target), f"无法访问「{target}」：{e}")
    if size > config.FILEOPS_MAX_READ_BYTES:
        mb = config.FILEOPS_MAX_READ_BYTES // (1024 * 1024)
        return _err("read", str(target), f"文件过大（{size} 字节 > {mb}MB 上限），未读取。")

    ext = target.suffix.lower()
    try:
        if ext in _RICH_EXTS:
            text = _get_md().convert(str(target)).text_content or ""
        else:
            raw = target.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # 含不可解码字节：疑似二进制
                if b"\x00" in raw[:4096]:
                    return _err("read", str(target), f"「{target}」疑似二进制文件，无法按文本读取。")
                text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("read_file 失败 %s: %s", target, e)
        return _err("read", str(target), f"读取「{target}」失败：{e}")

    shown, full_len = _truncate(text)
    content = f"文件「{target}」的内容如下：\n\n{shown}" if shown else f"「{target}」内容为空。"
    logger.info("read_file: %s (%d 字符)", target, full_len)
    return _ok("read", target, content, chars=full_len)


def list_dir(path: str = ".") -> dict:
    """列出目录下的文件与子目录。"""
    target = _resolve(path or ".")
    if not target.exists():
        return _err("list", str(target), f"路径不存在：{target}")
    if not target.is_dir():
        return _err("list", str(target), f"「{target}」不是目录，请用 read_file 读取文件。")
    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as e:
        return _err("list", str(target), f"无法列出「{target}」：{e}")

    limit = config.FILEOPS_LIST_LIMIT
    total = len(entries)
    truncated = total > limit
    lines, items = [], []
    for p in entries[:limit]:
        is_dir = p.is_dir()
        try:
            size = p.stat().st_size if not is_dir else 0
        except OSError:
            size = 0
        lines.append(f"{'📁' if is_dir else '📄'} {p.name}" + ("/" if is_dir else f"  ({size} 字节)"))
        items.append({"name": p.name, "is_dir": is_dir, "size": size})

    header = f"目录「{target}」下共 {total} 项" + ("（仅显示前 %d 项）" % limit if truncated else "") + "：\n"
    content = header + ("\n".join(lines) if lines else "（空目录）")
    logger.info("list_dir: %s (%d 项)", target, total)
    return _ok("list", target, content, count=total, truncated=truncated, items=items)


def make_dir(path: str) -> dict:
    """创建目录（含缺失的父目录）。已存在则视为成功并提示。"""
    if not path or not path.strip():
        return _err("mkdir", path or "", "未提供目录路径。")
    target = _resolve(path)
    if target.exists():
        if target.is_dir():
            return _ok("mkdir", target, f"目录已存在：{target}", existed=True)
        return _err("mkdir", str(target), f"「{target}」已存在且是一个文件，无法作为目录创建。")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _err("mkdir", str(target), f"创建目录「{target}」失败：{e}")
    logger.info("make_dir: %s", target)
    return _ok("mkdir", target, f"已创建目录：{target}", existed=False)


def delete_file(path: str) -> dict:
    """删除单个文件。拒绝删除目录，降低误删风险。"""
    if not path or not path.strip():
        return _err("delete", path or "", "未提供文件路径。")
    target = _resolve(path)
    if not target.exists():
        return _err("delete", str(target), f"路径不存在：{target}")
    if target.is_dir():
        return _err("delete", str(target), f"「{target}」是目录，出于安全本工具不删除目录。")
    try:
        target.unlink()
    except OSError as e:
        return _err("delete", str(target), f"删除「{target}」失败：{e}")
    logger.info("delete_file: %s", target)
    return _ok("delete", target, f"已删除文件：{target}")

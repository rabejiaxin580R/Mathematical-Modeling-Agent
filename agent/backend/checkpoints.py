"""文件改动检查点：在 AI 经 write_file/delete_file 改动磁盘前，先备份目标文件的原内容，
供「回溯聊天」时把这些改动还原（新建的删掉、覆盖/删除的恢复原内容）。

仅追踪 AI 经工具改动的文件；用户手动编辑、run_python 产物不在还原范围。
备份落在该会话工作目录下的隐藏子目录 data/runs/{conv_id}/.checkpoints/，与会话同生命周期。
"""
import logging
import uuid
from pathlib import Path

from .config import config
from .fileops import _resolve

logger = logging.getLogger(__name__)


def _ckpt_dir(conv_id: str) -> Path:
    safe = "".join(c for c in (conv_id or "") if c.isalnum() or c in "-_")
    return config.RUNS_DIR / safe / ".checkpoints"


def snapshot(conv_id: str, path: str) -> dict | None:
    """在文件被改动前对其当前状态做快照。

    返回 {path: <abs>, existed: bool, backup: <bak 文件名 或 None>}；
    path 非法/解析失败返回 None（调用方据此跳过）。
    """
    if not path or not path.strip():
        return None
    try:
        target = _resolve(path)
    except Exception:
        return None

    record = {"path": str(target), "existed": False, "backup": None}
    try:
        if target.exists() and target.is_file():
            ckdir = _ckpt_dir(conv_id)
            ckdir.mkdir(parents=True, exist_ok=True)
            bak_name = uuid.uuid4().hex[:16] + ".bak"
            (ckdir / bak_name).write_bytes(target.read_bytes())
            record["existed"] = True
            record["backup"] = bak_name
    except OSError as e:
        logger.warning("快照失败 %s: %s", target, e)
        # 备份失败时仍返回 existed 状态，回溯时至少能处理「新建则删除」的情形
        record["backup"] = None
    return record


def restore(conv_id: str, records: list[dict]) -> int:
    """按 records 逆序还原文件改动，返回成功处理的文件数。

    existed=True 且有 backup：写回备份字节（还原被覆盖/删除前的内容）。
    existed=False：删除该文件（还原「这次新建」之前的不存在状态）。
    """
    ckdir = _ckpt_dir(conv_id)
    done = 0
    for rec in reversed(records or []):
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        target = Path(rec["path"])
        try:
            if rec.get("existed"):
                bak = rec.get("backup")
                if bak:
                    src = ckdir / bak
                    if src.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(src.read_bytes())
                        done += 1
                # 无备份则无法还原内容，保持现状（保守）
            else:
                # 原本不存在 → 撤销新建
                if target.exists() and target.is_file():
                    target.unlink()
                    done += 1
        except OSError as e:
            logger.warning("还原失败 %s: %s", target, e)
    return done

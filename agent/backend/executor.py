"""本地 Python 代码执行器。

在受控的工作目录（data/runs/<run_id>/）中以子进程方式运行模型生成的代码，
带超时保护，捕获 stdout/stderr 与生成的图片文件。

安全说明：这不是完整沙箱。代码以当前用户权限执行，能访问文件系统与网络。
工作目录被限定到 data/runs 下，但不阻止越权访问。请仅在信任环境中使用，
并在前端向用户展示将要执行的代码。
"""
import logging
import subprocess
import sys
import time
import uuid
import base64
import os
import signal
import re
from pathlib import Path

from .config import config

logger = logging.getLogger(__name__)

# 注入到用户代码前的引导：让 matplotlib 用无界面后端、统一字体，避免弹窗
_PREAMBLE = """# -*- coding: utf-8 -*-
import os, sys
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    _plt = None

def _save_open_figures():
    if _plt is None:
        return
    for i, num in enumerate(_plt.get_fignums()):
        fig = _plt.figure(num)
        fig.savefig(f"figure_{i+1}.png", dpi=120, bbox_inches="tight")

import atexit as _atexit
_atexit.register(_save_open_figures)
# ===== 用户代码开始 =====
"""


def png_fingerprints(workdir: Path) -> dict:
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in workdir.glob("*.png")}


def collect_new_pngs(workdir: Path, before) -> list[dict]:
    """采集 workdir 下新生成（不在 before 集合里）的 PNG，返回 [{"name", "data_base64"}]。

    供 executor 与 ide 两处复用（matplotlib 图通过 _PREAMBLE 的 atexit 钩子保存为 figure_*.png）。
    """
    images = []
    for p in sorted(workdir.glob("*.png")):
        if p.name in before and (not isinstance(before, dict) or before[p.name] == (p.stat().st_mtime_ns, p.stat().st_size)):
            continue
        try:
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            images.append({"name": p.name, "data_base64": data})
        except OSError:
            pass
    return images


class ExecutionResult:
    def __init__(self, success, stdout, stderr, returncode, images, timed_out=False, cancelled=False):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.images = images  # list[{"name", "data_base64"}]
        self.timed_out = timed_out
        self.cancelled = cancelled

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "images": self.images,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
        }

    def to_feedback(self) -> str:
        """格式化为回喂给模型的执行反馈文本。"""
        parts = []
        if self.timed_out:
            parts.append(f"[执行超时] 代码运行超过 {config.CODE_TIMEOUT} 秒被终止。")
        parts.append(f"[退出码] {self.returncode}")
        if self.stdout:
            parts.append(f"[标准输出]\n{self.stdout}")
        if self.stderr:
            parts.append(f"[错误输出]\n{self.stderr}")
        if self.images:
            names = ", ".join(img["name"] for img in self.images)
            parts.append(f"[生成图片] {names}")
        if not self.stdout and not self.stderr and not self.images:
            parts.append("[无输出] 代码执行完成但没有任何输出。")
        return "\n".join(parts)


def stop_process_tree(proc):
    """Stop descendants too; this is process management, not a security sandbox."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def run_python(code: str, run_id: str | None = None, cancel_event=None) -> ExecutionResult:
    """在独立任务目录执行；保留完整日志，支持超时与取消进程树。"""
    config.ensure_dirs()
    run_id = run_id or uuid.uuid4().hex[:12]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id):
        raise ValueError("无效运行标识")
    workdir = config.RUNS_DIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)

    script_path = workdir / "script.py"
    script_path.write_text(_PREAMBLE + "\n" + code, encoding="utf-8")

    # 记录执行前已存在的图片，便于只采集新生成的
    before_imgs = png_fingerprints(workdir)

    timed_out = cancelled = False
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    output_path, error_path = workdir / "stdout.log", workdir / "stderr.log"
    with output_path.open("wb") as out, error_path.open("wb") as err:
        proc = subprocess.Popen([sys.executable, "-X", "utf8", str(script_path)],
                                cwd=str(workdir), stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, **options)
        started = time.monotonic()
        try:
            while proc.poll() is None:
                cancelled = bool(cancel_event and cancel_event.is_set())
                timed_out = time.monotonic() - started >= config.CODE_TIMEOUT
                if cancelled or timed_out:
                    stop_process_tree(proc)
                    break
                try:
                    proc.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if proc.poll() is None:
                stop_process_tree(proc)
        returncode = proc.returncode
    # Full logs stay on disk; only bounded previews enter chat/model context.
    def read_preview(path):
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return stream.read(6001)
    stdout, stderr = read_preview(output_path), read_preview(error_path)
    if timed_out:
        stderr += f"\n执行超时（>{config.CODE_TIMEOUT}s）"
    if cancelled:
        stderr += "\n执行已取消"

    # 采集新生成的图片
    images = collect_new_pngs(workdir, before_imgs)

    # 截断过长输出，避免撑爆上下文
    stdout = _truncate(stdout)
    stderr = _truncate(stderr)

    success = (returncode == 0) and not timed_out and not cancelled
    return ExecutionResult(success, stdout, stderr, returncode, images, timed_out, cancelled)


def _truncate(text: str, limit: int = 6000) -> str:
    if text and len(text) > limit:
        return text[:limit] + f"\n...[输出过长，已截断，共 {len(text)} 字符]"
    return text or ""

"""极简 IDE 的交互式终端会话：持久子进程 + 后台读取线程 + stdin 注入。

与 executor.run_python 的「一次性 subprocess.run」不同，这里用 Popen 让进程保持存活，
后台线程实时把 stdout/stderr 推给前端，前端可在程序运行中途写入 stdin（回答 input()/y-n）。

安全：命令以 shell=True 在本机当前用户权限下执行，等同本地 RCE，仅限 localhost 信任环境
（与 executor.py / fileops.py 同一信任模型）。
"""
import os
import sys
import codecs
import logging
import subprocess
import threading
from pathlib import Path

from .config import config
from .executor import _PREAMBLE, collect_new_pngs

logger = logging.getLogger(__name__)

# 单次运行的输出软上限（字符），超过则截断并终止，避免死循环 print 撑爆前端
_OUTPUT_CHAR_CAP = 200_000


class TerminalSession:
    """一个 WebSocket 连接对应一个会话。同一时刻只跑一个子进程。

    事件通过 emit(event_dict) 回调推给上层（线程安全由上层保证：
    main.py 用 loop.call_soon_threadsafe 包装）。事件类型：
      {"type":"stdout","data":str}
      {"type":"image","name":str,"data_base64":str}
      {"type":"exit","code":int,"cwd":str}
      {"type":"sys","data":str}   # 系统提示（非子进程输出）
    """

    def __init__(self, emit, sid: str):
        self.emit = emit
        self.sid = sid
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self._closed = False
        # 脚本暂存目录（不污染用户项目目录）；默认工作目录也指向它
        config.ensure_dirs()
        self.run_dir = config.RUNS_DIR / f"ide_{sid}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.cwd = self.run_dir

    # ── 状态 ──
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _set_cwd(self, cwd: str | None):
        """前端可在消息里带工作目录；存在且是目录才采用。"""
        if not cwd:
            return
        try:
            p = Path(os.path.expandvars(os.path.expanduser(cwd.strip()))).resolve()
            if p.is_dir():
                self.cwd = p
        except OSError:
            pass

    # ── 启动运行 ──
    def run_code(self, code: str, cwd: str | None = None):
        if self.is_running():
            self.emit({"type": "sys", "data": "（已有进程在运行，请先停止再运行）"})
            return
        self._set_cwd(cwd)
        script = self.run_dir / "script.py"
        try:
            script.write_text(_PREAMBLE + "\n" + (code or ""), encoding="utf-8")
        except OSError as e:
            self.emit({"type": "sys", "data": f"写入脚本失败：{e}"})
            self.emit({"type": "exit", "code": -1, "cwd": str(self.cwd)})
            return
        # -u 关闭缓冲，保证 input() 提示及时刷新；cwd=用户工作目录以便读取项目文件
        args = [sys.executable, "-X", "utf8", "-u", str(script)]
        self._spawn(args, shell=False, capture_images=True)

    def run_command(self, command: str, cwd: str | None = None):
        if self.is_running():
            self.emit({"type": "sys", "data": "（已有进程在运行，请先停止再运行）"})
            return
        self._set_cwd(cwd)
        cmd = (command or "").strip()
        if not cmd:
            self.emit({"type": "exit", "code": 0, "cwd": str(self.cwd)})
            return
        # cd 内建命令：在会话内切换工作目录，不真正起子进程
        if cmd == "cd" or cmd.lower().startswith("cd ") or cmd.lower().startswith("cd\t"):
            self._handle_cd(cmd)
            return
        self._spawn(cmd, shell=True, capture_images=False)

    def _handle_cd(self, cmd: str):
        arg = cmd[2:].strip().strip('"')
        if not arg:
            # 无参数：显示当前目录
            self.emit({"type": "stdout", "data": str(self.cwd) + "\n"})
            self.emit({"type": "exit", "code": 0, "cwd": str(self.cwd)})
            return
        try:
            target = Path(os.path.expandvars(os.path.expanduser(arg)))
            if not target.is_absolute():
                target = self.cwd / target
            target = target.resolve()
        except OSError as e:
            self.emit({"type": "stdout", "data": f"cd: 无法解析路径：{e}\n"})
            self.emit({"type": "exit", "code": 1, "cwd": str(self.cwd)})
            return
        if not target.is_dir():
            self.emit({"type": "stdout", "data": f"cd: 目录不存在：{target}\n"})
            self.emit({"type": "exit", "code": 1, "cwd": str(self.cwd)})
            return
        self.cwd = target
        self.emit({"type": "exit", "code": 0, "cwd": str(self.cwd)})

    def _spawn(self, args, shell: bool, capture_images: bool):
        before_imgs = {p.name for p in self.cwd.glob("*.png")} if capture_images else set()
        try:
            self.proc = subprocess.Popen(
                args,
                cwd=str(self.cwd),
                shell=shell,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,  # 关闭 Python 侧缓冲，配合 os.read 实时读取
            )
        except OSError as e:
            self.emit({"type": "stdout", "data": f"启动失败：{e}\n"})
            self.emit({"type": "exit", "code": -1, "cwd": str(self.cwd)})
            self.proc = None
            return
        self.reader = threading.Thread(
            target=self._read_loop,
            args=(self.proc, before_imgs, capture_images),
            daemon=True,
        )
        self.reader.start()

    # ── 后台读取 ──
    def _read_loop(self, proc: subprocess.Popen, before_imgs: set, capture_images: bool):
        """逐块读取子进程合并输出（stdout+stderr）并实时 emit。

        用 os.read 而非 readline：进程把无换行的提示（如 input("名字？")）flush 后，
        os.read 立刻返回这批字节，提示得以即时显示，不会卡在等换行。
        """
        fd = proc.stdout.fileno()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        emitted = 0
        capped = False
        try:
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if not text:
                    continue
                if not capped:
                    emitted += len(text)
                    if emitted > _OUTPUT_CHAR_CAP:
                        self.emit({"type": "stdout", "data": text})
                        self.emit({"type": "sys", "data": "（输出过长，已截断并终止进程）"})
                        capped = True
                        try:
                            proc.kill()
                        except OSError:
                            pass
                    else:
                        self.emit({"type": "stdout", "data": text})
        finally:
            tail = decoder.decode(b"", final=True)
            if tail and not capped:
                self.emit({"type": "stdout", "data": tail})
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = -1
            if capture_images:
                for img in collect_new_pngs(self.cwd, before_imgs):
                    self.emit({"type": "image", **img})
            self.proc = None
            self.emit({"type": "exit", "code": code, "cwd": str(self.cwd)})

    # ── 交互 ──
    def send_stdin(self, data: str):
        if not self.is_running() or self.proc.stdin is None:
            return
        try:
            self.proc.stdin.write((data + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass  # 管道已关闭（进程刚好退出）

    def interrupt(self):
        if not self.is_running():
            return
        try:
            self.proc.terminate()
        except OSError:
            pass

    def cleanup(self):
        """WebSocket 断开时调用：杀子进程、收线程，避免僵尸。"""
        self._closed = True
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        if self.reader is not None:
            self.reader.join(timeout=2)

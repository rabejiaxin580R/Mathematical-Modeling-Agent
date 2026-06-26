#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数学建模助教 · 一键启动器

职责（刻意做得很薄）：
  1. 定位「内置 Python」与「app 目录」（打包后 / 源码运行都支持）；
  2. 把用户可写数据目录指到每用户目录（%LOCALAPPDATA% / ~/.local 等），
     与只读的程序目录分离；
  3. 选一个空闲端口，用**内置 Python**作为子进程启动后端（关键：
     这样后端里的 sys.executable 指向内置 Python，跑代码 / IDE 终端才正常）；
  4. 等健康检查通过后自动打开浏览器；
  5. 保持运行，窗口关闭 / Ctrl+C 时收掉后端子进程。

注意：启动器自己可以被 PyInstaller 编译成 exe，但它**绝不**在自身进程里跑
后端——后端必须由内置 Python 子进程承载，否则 sys.executable 会指向启动器，
模型生成代码的执行与 IDE 终端都会失效。
"""
import os
import sys
import time
import socket
import signal
import subprocess
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "数学建模助教"
HEALTH_TIMEOUT = 60      # 等待后端就绪的最长秒数
HEALTH_PATH = "/api/health"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _bundle_root() -> Path:
    """打包后：exe 所在目录即捆绑根；源码运行：本文件上两级（agent/ 的上级）。"""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    # launcher/launch.py → packaging/ → revent/。源码模式下 app 即 ../agent
    return Path(__file__).resolve().parent.parent


def _locate() -> tuple[Path, Path]:
    """返回 (python_exe, app_dir)。兼容三种布局，按优先级探测。"""
    root = _bundle_root()

    # 候选「内置 Python」位置
    py_candidates = [
        root / "python" / "python.exe",                 # Windows 捆绑
        root / "python" / "bin" / "python3",             # mac / linux 捆绑
        root / "python" / "bin" / "python",
    ]
    # 候选 app 目录（含 backend/ 的那一层）
    app_candidates = [
        root / "app",                                    # 捆绑布局
        root.parent / "agent",                           # 源码布局（packaging 的同级 agent）
        Path(__file__).resolve().parent.parent / "agent",
    ]

    python_exe = next((p for p in py_candidates if p.exists()), None)
    app_dir = next((p for p in app_candidates if (p / "backend" / "main.py").exists()), None)

    # 源码调试兜底：用当前解释器
    if python_exe is None:
        python_exe = Path(sys.executable)
    if app_dir is None:
        raise SystemExit("找不到 app 目录（缺少 backend/main.py）。请检查安装是否完整。")
    return python_exe, app_dir


def _user_data_dir() -> Path:
    """每用户可写数据目录。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "MathModelingAgent" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MathModelingAgent" / "data"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "MathModelingAgent" / "data"


def _free_port(preferred: int = 8000) -> int:
    """优先用 8000，被占则让系统分配一个空闲端口。"""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("无法分配可用端口。")


def _wait_healthy(port: int, proc: subprocess.Popen) -> bool:
    url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False  # 子进程已退出（多半是启动失败）
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.6)
    return False


def main():
    print("=" * 52)
    print(f"  {APP_NAME} 正在启动…")
    print("=" * 52)

    python_exe, app_dir = _locate()
    data_dir = _user_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port(8000)

    env = dict(os.environ)
    env["MMAGENT_DATA_DIR"] = str(data_dir)
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    print(f"  数据目录：{data_dir}")
    print(f"  本地地址：http://127.0.0.1:{port}")
    print("  首次启动需加载知识库，请稍候…")

    creationflags = 0
    if sys.platform.startswith("win"):
        # 不为子进程额外开控制台窗口
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        [str(python_exe), "-X", "utf8", "-m", "backend.main"],
        cwd=str(app_dir),
        env=env,
        creationflags=creationflags,
    )

    try:
        if _wait_healthy(port, proc):
            print("\n  ✅ 启动完成，正在打开浏览器…")
            webbrowser.open(f"http://127.0.0.1:{port}/")
            print("  （关闭此窗口即可退出程序）\n")
        else:
            print("\n  ⚠ 后端未能在预期时间内就绪。")
            print(f"  你也可以手动用浏览器打开：http://127.0.0.1:{port}/")
        # 阻塞等待后端进程，直到它退出或本窗口被关闭
        proc.wait()
    except KeyboardInterrupt:
        print("\n  正在退出…")
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    # 让 Ctrl+C 能干净地传播
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except Exception:
        pass
    main()

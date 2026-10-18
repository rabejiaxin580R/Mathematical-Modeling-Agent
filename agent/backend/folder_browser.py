"""Directory-only browser for the local workspace picker."""
import os
from pathlib import Path


def locations():
    home = Path.home()
    entries = [{"name": "个人文件夹", "path": str(home)}]
    for name, label in (("Desktop", "桌面"), ("Documents", "文档"), ("Downloads", "下载")):
        folder = home / name
        if folder.is_dir():
            entries.append({"name": label, "path": str(folder)})
    if os.name == "nt":
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        entries.extend({"name": f"磁盘 {chr(65+i)}:", "path": f"{chr(65+i)}:\\"} for i in range(26) if mask & (1 << i))
    else:
        entries.append({"name": "文件系统", "path": "/"})
    return entries


def browse(path=""):
    try:
        current = Path(path).expanduser().resolve() if path else Path.home()
        folders = []
        with os.scandir(current) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        folders.append({"name": entry.name, "path": entry.path})
                except OSError:
                    continue
        folders.sort(key=lambda item: item["name"].casefold())
        crumbs = [{"name": p.name or str(p), "path": str(p)} for p in [*reversed(current.parents), current]]
        return {"ok": True, "path": str(current), "parent": str(current.parent), "folders": folders, "breadcrumbs": crumbs, "locations": locations()}
    except (OSError, ValueError):
        return {"ok": False, "error": "无法打开此文件夹，可能已被移动或没有访问权限。请选择其他位置。"}


def create(parent, name):
    name = name.strip()
    if not name or name in (".", "..") or any(c in name for c in '\\/:*?"<>|') or any(ord(c) < 32 for c in name) or name.endswith((".", " ")):
        return {"ok": False, "error": "请输入有效的文件夹名称，不能包含路径符号。"}
    try:
        target = Path(parent).resolve() / name
        target.mkdir()
        return {"ok": True, "path": str(target)}
    except FileExistsError:
        return {"ok": False, "error": "这个名称已存在，请换一个名称。"}
    except OSError:
        return {"ok": False, "error": "无法在当前位置创建文件夹，请选择其他位置。"}

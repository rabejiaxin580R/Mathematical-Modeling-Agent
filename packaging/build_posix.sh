#!/usr/bin/env bash
# 数学建模助教 · macOS / Linux 打包脚本
#
# 与 build_windows.ps1 同思路：捆绑可重定位的 python-build-standalone + 依赖 + app，
# 启动器编成原生可执行文件。产出便携目录与 .tar.gz。
#
# 用法：
#   PY_VERSION=3.13.5 PBS_TAG=20250712 ./build_posix.sh
#
# 平台由 uname 自动判断（darwin / linux），架构取 uname -m。
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.13.5}"
PBS_TAG="${PBS_TAG:-20250712}"

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$PKG_DIR")"
APP_SRC="$ROOT/agent"
WORK="$PKG_DIR/_work"
DIST="$PKG_DIR/dist"
OUT_NAME="数学建模助教"
OUT_DIR="$DIST/$OUT_NAME"
APP_DST="$OUT_DIR/app"
PY_DST="$OUT_DIR/python"

# ── 平台 / 架构 → python-build-standalone 资产名 ──
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
  Darwin) case "$ARCH" in
            arm64) TRIPLE="aarch64-apple-darwin" ;;
            x86_64) TRIPLE="x86_64-apple-darwin" ;;
            *) echo "不支持的 macOS 架构：$ARCH" >&2; exit 1 ;;
          esac; PLATFORM="macos" ;;
  Linux)  case "$ARCH" in
            x86_64) TRIPLE="x86_64-unknown-linux-gnu" ;;
            aarch64) TRIPLE="aarch64-unknown-linux-gnu" ;;
            *) echo "不支持的 Linux 架构：$ARCH" >&2; exit 1 ;;
          esac; PLATFORM="linux" ;;
  *) echo "不支持的系统：$OS" >&2; exit 1 ;;
esac

echo "============================================"
echo "  数学建模助教 · $PLATFORM/$ARCH 打包"
echo "  Python $PY_VERSION (pbs $PBS_TAG)"
echo "============================================"

[ -f "$APP_SRC/backend/main.py" ] || { echo "找不到 app 源码：$APP_SRC/backend/main.py" >&2; exit 1; }

rm -rf "$OUT_DIR"
mkdir -p "$WORK" "$APP_DST"

# ── 1. 下载并解压 standalone Python ──
ASSET="cpython-${PY_VERSION}+${PBS_TAG}-${TRIPLE}-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${ASSET}"
TARBALL="$WORK/$ASSET"
if [ ! -f "$TARBALL" ]; then
  echo "[1/5] 下载内置 Python：$URL"
  curl -fL "$URL" -o "$TARBALL"
else
  echo "[1/5] 已存在，跳过下载：$ASSET"
fi
echo "      解压…"
EXTRACT="$WORK/py_extract"; rm -rf "$EXTRACT"; mkdir -p "$EXTRACT"
tar -xzf "$TARBALL" -C "$EXTRACT"
[ -x "$EXTRACT/python/bin/python3" ] || { echo "解压后未找到 python/bin/python3" >&2; exit 1; }
cp -R "$EXTRACT/python" "$PY_DST"
PY_EXE="$PY_DST/bin/python3"

# ── 2. 安装依赖 ──
echo "[2/5] 安装依赖到内置 Python…"
"$PY_EXE" -m pip install --upgrade pip
"$PY_EXE" -m pip install -r "$APP_SRC/requirements.txt"

# ── 3. 拷贝 app + 只读数据 ──
echo "[3/5] 拷贝 app 与只读资源…"
cp -R "$APP_SRC/backend"  "$APP_DST/backend"
cp -R "$APP_SRC/frontend" "$APP_DST/frontend"
[ -d "$APP_SRC/scripts" ] && cp -R "$APP_SRC/scripts" "$APP_DST/scripts" || true
cp "$APP_SRC/requirements.txt" "$APP_DST/"
[ -f "$APP_SRC/.env.example" ] && cp "$APP_SRC/.env.example" "$APP_DST/" || true
mkdir -p "$APP_DST/data"
for d in knowledge problems problem_assets problem_papers; do
  if [ -d "$APP_SRC/data/$d" ]; then cp -R "$APP_SRC/data/$d" "$APP_DST/data/$d"; echo "      + data/$d"; fi
done
find "$APP_DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ── 4. PyInstaller 编译启动器 ──
echo "[4/5] 编译一键启动器…"
"$PY_EXE" -m pip install pyinstaller
( cd "$WORK" && "$PY_EXE" -m PyInstaller --onefile --console --name "qidong-zhujiao" \
    --distpath "$WORK/launcher_dist" --workpath "$WORK/launcher_build" --specpath "$WORK" \
    "$PKG_DIR/launcher/launch.py" )
cp "$WORK/launcher_dist/qidong-zhujiao" "$OUT_DIR/启动助教"
chmod +x "$OUT_DIR/启动助教"

# 使用说明
cat > "$OUT_DIR/使用说明.txt" <<'EOF'
数学建模助教 —— 使用说明（macOS / Linux）

1. 在终端进入本目录，运行：  ./启动助教
   （macOS 首次可能被 Gatekeeper 拦截，在「系统设置 > 隐私与安全性」里允许，或执行
     xattr -dr com.apple.quarantine . 后重试。）
2. 稍候十几秒会自动打开浏览器；首次使用会弹出「连接 AI 大模型」向导，二选一配置。
3. 关闭终端窗口即退出。数据保存在本机用户目录，删除程序不会清除。
EOF

# ── 5. 打 tar.gz ──
echo "[5/5] 打包 tar.gz…"
TAROUT="$DIST/${OUT_NAME}-${PLATFORM}-${ARCH}.tar.gz"
rm -f "$TAROUT"
tar -czf "$TAROUT" -C "$DIST" "$OUT_NAME"

echo "============================================"
echo "  便携目录：$OUT_DIR"
echo "  压缩包：  $TAROUT"
echo "============================================"

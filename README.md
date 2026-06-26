# 数学建模助教

基于约 40 小时数学建模课程知识库的 AI 助教：拆解问题、有出处地答疑、编写并自动运行 Python 代码、手把手带零基础用户。现已支持**打包成自带运行环境的桌面应用**，新手下载即用，无需安装 Python。

## 我是谁，该看哪份文档

- **我是使用者，想用这个 app** → 看 [使用指南.md](使用指南.md)：下载、安装、第一次连大模型、四个功能。
- **我是维护者，想把它发出去** → 看 [分发说明.md](分发说明.md)：构建安装包、三平台 CI、发布到 GitHub Releases / 云盘、对外文案模板。
- **我是开发者，想从源码跑/改** → 看 [agent/README.md](agent/README.md) 与 [agent/STRUCTURE.md](agent/STRUCTURE.md)。

## 仓库结构

```
revent/
├── agent/            助教 app（FastAPI 后端 + 原生前端 + 知识库/题库）
├── gateway/          OpenAI 兼容计费网关（可选，支撑「用我们提供的额度」这条路）
├── packaging/        打包分发
│   ├── launcher/launch.py     一键启动器（选端口→拉起内置 Python→开浏览器）
│   ├── build_windows.ps1      Windows 打包（内置 CPython + 依赖 + app → zip / 安装程序）
│   ├── build_posix.sh         macOS / Linux 打包 → tar.gz
│   └── installer/setup.iss     Inno Setup 图形安装程序脚本
├── .github/workflows/release.yml   打 tag 自动三平台构建并发布到 Releases
├── 使用指南.md        面向最终用户
└── 分发说明.md        面向维护者
```

## 打包思路（为什么不是单个 exe）

助教的核心功能是**在本机跑模型生成的 Python 代码**，还带一个 IDE 终端，都通过 `sys.executable` 起子进程。因此分发包必须**捆绑一个真实的 Python 解释器**（含 numpy / pandas / scipy / matplotlib 等），而不是把整个 app 压成单文件 exe——后者会让 `sys.executable` 指向 exe 自身，跑代码与 IDE 全部失效。

所以产物是「自带运行环境的便携目录 + 一个很薄的启动器」：

```
数学建模助教/
├── 启动助教.exe      一键启动器（PyInstaller 编译）
├── python/           内置 CPython + 全部依赖
├── app/              backend / frontend / 只读知识库与题库
└── 使用说明.txt
```

用户数据（聊天、档案、做题进度）写到每用户目录（`%LOCALAPPDATA%` 等），与只读程序目录分离，卸载不丢数据。

## 快速构建（Windows）

```powershell
cd packaging
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

详见 [分发说明.md](分发说明.md)。

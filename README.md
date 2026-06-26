<p align="center">
  <img src="https://img.shields.io/badge/version-v1.0.0-2ea44f?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/python-3.13-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=for-the-badge" alt="Platform">
</p>

<p align="center">
  <h1 align="center">📐 数学建模助教</h1>
  <p align="center"><em>基于 40 小时数学建模课程知识库的 AI 智能助教<br>拆解问题 · 有出处答疑 · 自动写代码跑代码 · 手把手教零基础</em></p>
</p>

---

## ✨ 为什么你需要它？

| 🤔 你的困境 | 💡 助教怎么帮你 |
|------------|---------------|
| 拿到建模题不知从何下手 | **自动拆解问题**，一步步引导建模思路 |
| 问 ChatGPT 不知道答案靠不靠谱 | 每个回答**标注出处**（chunk_id），可追溯知识来源 |
| 想跑代码但不会配 Python 环境 | **AI 直接写代码、自动运行**，matplotlib 出图实时渲染 |
| 零基础、没人带 | 手把手教配环境、写论文、排 LaTeX，**新手友好** |
| LaTeX / Word 排版头大 | 给出分步指引 + 示例 + 界面文字描述 |

---

## 🚀 功能一览

<table>
<tr>
  <td width="25%" align="center"><b>📚 学习模式</b></td>
  <td width="25%" align="center"><b>🔧 做建模</b></td>
  <td width="25%" align="center"><b>📝 真题练习</b></td>
  <td width="25%" align="center"><b>💬 AI 对话</b></td>
</tr>
<tr>
  <td>顺着知识图谱，从基础概念一步步点亮建模技能树</td>
  <td>自由工作台（编辑器 + 终端 + AI），AI 陪你一步步解题</td>
  <td>历年 HiMCM 真题分步练，AI 按评分要点打分点评</td>
  <td>公式渲染 · 代码自动运行 · 上传 PDF/Word/Excel 分析</td>
</tr>
</table>

---

## 📦 下载（即开即用，无需装 Python）

| 系统 | 推荐下载 | 免安装版 |
|------|---------|---------|
| 🪟 **Windows** | `数学建模助教-安装程序-v1.0.0.exe` | `数学建模助教-windows-x64.zip` |
| 🍎 **macOS** | — | `数学建模助教-macos-arm64.tar.gz` |
| 🐧 **Linux** | — | `数学建模助教-linux-x86_64.tar.gz` |

> 📥 **[前往 Releases 下载 →](https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent/releases)**
>
> 包比较大（~600-800MB），因为它**自带完整 Python 运行环境 + numpy/pandas/scipy/matplotlib**，你不需要装任何东西。

---

## ⚡ 5 分钟上手

```bash
# ① 下载 → 解压/安装 → 双击启动
# ② 浏览器自动打开 → 跟着向导连大模型（两种方式任选）

方式 A：用我们提供的额度（自动填好，粘贴 Key 即可）
方式 B：用自己的 API Key（支持 DeepSeek / 通义千问 / OpenAI / Moonshot）

# ③ 开始用！聊数学、做真题、跑代码
```

> 📖 完整使用指南看 **[使用指南.md](使用指南.md)**

---

## 🏗️ 项目架构

```
数学建模助教/
├── agent/                    🤖 助教 App（FastAPI + 原生前端 + 知识库）
│   ├── backend/              Python 后端（agent 循环 / 工具调用 / 知识检索）
│   ├── frontend/             新中式刺绣美学前端（Markdown + KaTeX + 代码高亮）
│   ├── data/knowledge/       252 个结构化知识单元（BM25 本地检索，零 API 成本）
│   └── assets/HiMCMpapers/   历年真题 PDF（1999–2019）
│
├── gateway/                  🔐 LLM 计费网关（可选）
│   └── OpenAI 兼容的 API 代理 + 用户系统 + 卡密充值
│
├── packaging/                📦 打包分发
│   ├── build_windows.ps1     Windows 构建（内置 Python → zip / 安装程序）
│   └── build_posix.sh        macOS / Linux 构建 → tar.gz
│
└── .github/workflows/        🚀 打 tag 自动三平台构建 → 发布到 Releases
```

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + SSE 流式 |
| 知识检索 | BM25（jieba 分词），本地零成本 |
| 代码执行 | 子进程沙箱，matplotlib 图像自动采集 |
| 前端 | 原生 HTML/JS/CSS，新中式设计 |
| 公式渲染 | KaTeX |
| 打包 | PyInstaller 启动器 + 内嵌 CPython |
| CI/CD | GitHub Actions（三平台并行构建） |

---

## 🔧 开发者看这里

### 从源码跑

```powershell
cd agent
copy .env.example .env        # 编辑填入 LLM_API_KEY
.\start.bat                   # 自动建 venv、装依赖、启动
# 浏览器打开 http://127.0.0.1:8000
```

### 打包分发

```powershell
cd packaging
powershell -ExecutionPolicy Bypass -File build_windows.ps1   # Windows
# 或
./build_posix.sh                                              # macOS / Linux
```

### 发版

```bash
git tag v1.0.1
git push origin v1.0.1       # 自动触发三平台构建 + Release
```

> 📖 更多见 [agent/README.md](agent/README.md) 和 [agent/STRUCTURE.md](agent/STRUCTURE.md)

---

## 📂 文档导航

| 你是... | 看这个 |
|---------|--------|
| 🧑‍🎓 **使用者** | [使用指南.md](使用指南.md) — 下载、安装、连模型、四个功能 |
| 🔧 **维护者** | [分发说明.md](分发说明.md) — 构建安装包、CI 发布、渠道铺排 |
| 💻 **开发者** | [agent/README.md](agent/README.md) + [agent/STRUCTURE.md](agent/STRUCTURE.md) |

---

## ⚠️ 安全提醒

模型生成的 Python 代码会在你本机以当前用户权限运行。虽然工作目录限定在 `data/runs/`，但代码**不阻止访问文件系统与网络**。前端会展示每次将执行的代码，请审阅后放行，仅在信任环境中使用。

---

## 📄 License

MIT © 2025

---

<p align="center">
  <sub>Made with ❤️ for math modelers everywhere · 祝建模顺利 📐</sub>
</p>

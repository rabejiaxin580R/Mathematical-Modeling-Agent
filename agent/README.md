# 数学建模助教 Agent

基于约 40 小时数学建模课程知识库的 AI 助教。能拆解问题、**有出处地**回答数学/建模问题、编写并**自动运行+迭代修正** Python 代码、手把手带零基础用户配环境与排版。

- **后端**：Python + FastAPI（命令行启动），调用 OpenAI 兼容大模型 API
- **检索**：本地 BM25（jieba 分词），加载上级 `../json_outputs/` 的 252 个结构化知识单元，零额外 API 成本
- **代码执行**：本地子进程，带超时，自动采集 matplotlib 图像；出错自动回喂模型迭代
- **前端**：单页聊天 Web 应用，新中式刺绣美学，Markdown + KaTeX 公式 + 代码高亮，对话存档可回溯

## 三个核心功能如何落地

| 需求 | 实现 |
|------|------|
| 基于知识库的问答与问题拆分 | 系统提示词要求复杂问题先拆子问题；`search_knowledge` 工具检索知识库，回答标注 `chunk_id` 出处；检索分过低则明确告知超出范围 |
| 代码编写与自测 | `run_python` 工具在 `data/runs/` 子进程执行，捕获输出与图像，错误回喂模型自动迭代（默认最多 3 轮） |
| 面向初学者的引导式教学 | 系统提示词约束：环境配置/论文写作给分步指引；排版（LaTeX/Word）因无法操作本地软件，只给详尽步骤+示例+界面文字描述 |

## 快速开始

### 1. 安装依赖（需 Python 3.10+）

推荐用独立虚拟环境，避免与全局包冲突：

```powershell
cd 新建文件夹
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 配置 API 密钥

复制 `.env.example` 为 `.env`，填入你的大模型 API 信息：

```powershell
copy .env.example .env
```

编辑 `.env`，至少填好 `LLM_API_KEY`。默认配置用 DeepSeek，也可换任意 OpenAI 兼容端点（通义、Moonshot、OpenAI 等），只改 `LLM_BASE_URL` 和 `LLM_MODEL`。

### 3. 启动

```powershell
.venv\Scripts\python.exe -m backend.main
```

或直接双击 `start.bat`（会自动创建虚拟环境、装依赖、启动）。浏览器打开 http://127.0.0.1:8000

## 安全说明

⚠ **代码执行不是完整沙箱。** 模型生成的 Python 代码会以你当前用户权限在本机子进程中运行，工作目录限定在 `data/runs/`，但不阻止访问文件系统与网络。请仅在信任环境中使用。前端会展示每次将要执行的代码，便于你审阅。

## 目录结构

```
新建文件夹/
├── backend/
│   ├── config.py       配置加载（.env）
│   ├── knowledge.py    知识库加载 + BM25 检索
│   ├── executor.py     Python 子进程执行器
│   ├── tools.py        大模型工具定义与分发
│   ├── agent.py        系统提示词 + 工具调用循环（流式）
│   ├── storage.py      对话存档
│   └── main.py         FastAPI 应用（SSE）
├── frontend/           index.html · style.css · app.js
├── data/
│   ├── conversations/  对话存档（JSON）
│   └── runs/           代码执行工作区 + 生成的图
├── requirements.txt
├── .env.example
└── start.bat
```

## 知识库来源

读取上级目录 `../json_outputs/structured_lecture_*.json`，可通过 `.env` 的 `KNOWLEDGE_DIR` 修改路径。

## 后续扩展

- 接入 VitePress 知识站点（嵌入聊天悬浮窗，浏览+提问一体化）
- 检索升级为向量语义检索（`knowledge.py` 已预留接口）

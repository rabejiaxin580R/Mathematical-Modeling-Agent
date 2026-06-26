# 数学建模助教 Agent — 项目结构说明

> 本文档说明 `agent/` 各文件/目录的用途与重要性级别，便于团队快速定位资源。
> 重要性级别：⭐⭐⭐ 核心（缺失即无法运行）｜⭐⭐ 重要（影响主要功能）｜⭐ 辅助（离线/可选）。

## 顶层结构

```
agent/
├── backend/        后端服务（FastAPI）        ⭐⭐⭐
├── frontend/       前端多页应用（原生 HTML/JS） ⭐⭐⭐
├── data/           运行时数据与知识库          ⭐⭐⭐
├── scripts/        离线构建脚本               ⭐
├── assets/         离线构建源数据（真题/示例）   ⭐
├── .env            本地配置（含密钥，不入库）    ⭐⭐⭐
├── .env.example    配置模板                   ⭐⭐
├── requirements.txt 依赖清单                  ⭐⭐⭐
├── start.bat       一键启动脚本               ⭐⭐
├── README.md       项目说明                   ⭐⭐
└── .gitignore      版本忽略规则               ⭐
```

## backend/（⭐⭐⭐ 核心后端）

| 文件 | 用途 | 级别 |
|---|---|---|
| main.py | FastAPI 应用：全部 HTTP/SSE/WebSocket 端点、静态前端挂载、设置 API | ⭐⭐⭐ |
| config.py | 配置中心：环境变量 + 运行时 settings.json 覆盖；所有数据目录定义 | ⭐⭐⭐ |
| agent.py | Agent 核心：双人格提示词、流式工具循环 stream_reply / stream_step_chat | ⭐⭐⭐ |
| tools.py | function-calling 工具定义与分发（通用 8 工具 + 真题只读子集） | ⭐⭐⭐ |
| knowledge.py | 知识库加载 + BM25 检索（新库 concepts/ 优先） | ⭐⭐⭐ |
| executor.py | Python 子进程执行器：超时、matplotlib 中文字体注入、收集图像 | ⭐⭐⭐ |
| storage.py | 对话存档（JSON），solve 自动路由到 solve_conversations/ | ⭐⭐ |
| fileops.py | 真实文件系统读写/列目录/建目录/删文件 | ⭐⭐ |
| documents.py | markitdown 读取上传文档（PDF/Word/PPT/HTML） | ⭐⭐ |
| solve_sessions.py | 做题会话存档（题目快照 + 路线 + 进度） | ⭐⭐ |
| problems.py / dyn_problems.py | 手写真题库 / AI 动态拆步生成的题 | ⭐⭐ |
| grader.py | 评分 + 对话式掌握度评估 | ⭐⭐ |
| framework.py | 建模框架（阶段 + modality） | ⭐⭐ |
| graph.py | 知识图谱构建（学习模式） | ⭐⭐ |
| assessment.py / profiles.py | 入门测评定级 + 用户本地档案 | ⭐⭐ |
| checkpoints.py | 文件快照/还原，支撑「回到这一步」回溯 | ⭐ |
| export_docx.py | Markdown/LaTeX → Word 导出 | ⭐ |
| ide.py | 极简 IDE 交互式终端（持久子进程 + WebSocket） | ⭐ |

## frontend/（⭐⭐⭐ 前端，扁平结构）

> ⚠️ 所有资源经 `/static` 扁平挂载，HTML 内以 `/static/xxx` 平铺引用，**不要拆子目录**，否则引用断裂。

- 页面：landing / home / learn / build / workspace / solve / practice / ide / assessment / index(旧聊天).html
- 逻辑：app.js、layout.js、render.js（Markdown+KaTeX）、stepflow.js、onboarding.js、profile.js 等
- 样式：style.css、anim.css

## data/（⭐⭐⭐ 运行时数据）

| 路径 | 用途 | 级别 |
|---|---|---|
| knowledge/concepts/ | 方法百科页知识库（151 概念，新库，BM25 检索源） | ⭐⭐⭐ |
| knowledge/{cases,_fulltext,_raw_extractions}/ | 构建中间产物 | ⭐ |
| settings.json | 运行时 LLM 配置（key/base_url/model 热改） | ⭐⭐⭐ |
| problems / dynamic_problems / problem_assets / problem_papers | 真题库与附件 | ⭐⭐ |
| conversations / solve_conversations / solve_sessions | 对话与做题会话存档 | ⭐⭐ |
| profiles / assessment | 用户档案与测评数据 | ⭐⭐ |
| runs/ | 代码执行临时目录（运行时生成，可清空） | ⭐ |
| app.log / srv.*.log | 运行日志（可清空） | ⭐ |

## scripts/（⭐ 离线构建工具，不在运行时路径）

- build_knowledge.py — 由真题 PDF 生成 concepts/ 知识库（源：assets/HiMCMpapers/）
- build_problems.py — 由 assets/real_problem/ 生成动态框架题库
- build_assessment.py — 生成入门测评题与学习路径
- migrate_problems_v1_to_v2.py — 题库格式迁移

## assets/（⭐ 离线构建源数据）

- HiMCMpapers/ — HiMCM 历年真题 PDF（1999–2019），build_knowledge 输入
- real_problem/ — 真题源（2020–2024），build_problems 输入
- ga_facility_location/ — 遗传算法选址示例项目（历史产物）

---

## 运行方式

```powershell
# 首次：复制配置并填入 LLM_API_KEY
copy .env.example .env
# 启动（自动建 venv 装依赖）
.\start.bat
# 或手动
.venv\Scripts\python.exe -m backend.main
```

默认服务地址 http://127.0.0.1:8000

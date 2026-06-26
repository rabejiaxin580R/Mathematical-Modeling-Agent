# agent/ 系统性整理计划

## 1. 归档源数据目录 → assets/
- 新建 `assets/`
- 移动 `HiMCMpapers/` → `assets/HiMCMpapers/`
- 移动 `real problem/` → `assets/real_problem/`（顺便规范命名：去空格）
- 移动 `ga_facility_location/` → `assets/ga_facility_location/`
- 改 `scripts/build_knowledge.py`：`HIMCM_DIR = AGENT_DIR / "assets" / "HiMCMpapers"`
- 改 `scripts/build_problems.py`：`REAL_PROBLEM_DIR = AGENT_DIR / "assets" / "real_problem"`

## 2. 清理临时/日志/runs（全清）
- 删 `data/billing.db`、`billing.db-shm`、`billing.db-wal`
- 删测试残留：`smoke.out`、`_smoke2.log`、`_smoke_srv.log`、`_srv2.err`、`_srv2.log`、`_test_srv.out`
- 清空旧日志内容：`app.log`、`srv.err.log`、`srv.out.log`（保留空文件）
- 清空 `data/runs/` 全部历史执行目录（保留 runs/ 空目录）

## 3. 配置整理
- `.env.example` 与 `.env` 已不同步：`.env.example` 仍写 `KNOWLEDGE_DIR=../json_outputs`，更新为 `data/knowledge`
- `.gitignore` 补充：data/runs、临时日志、billing.db、assets 大文件等

## 4. 文档
- 生成 `STRUCTURE.md`：目录结构说明 + 功能用途 + 重要性级别

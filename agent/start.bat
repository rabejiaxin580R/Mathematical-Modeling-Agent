@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   数学建模助教 Agent 启动中...
echo ============================================
if not exist .env (
  echo [提示] 未找到 .env，正在从 .env.example 复制...
  copy .env.example .env >nul
  echo [重要] 请编辑 .env 填入你的 LLM_API_KEY 后重新运行。
  pause
  exit /b
)
if not exist .venv (
  echo [初始化] 首次运行，正在创建虚拟环境并安装依赖...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m backend.main
pause

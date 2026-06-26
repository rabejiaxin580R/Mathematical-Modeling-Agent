"""配置加载：从环境变量 / .env 读取，集中管理。支持运行时通过 API 修改 LLM 配置。"""
import os
import json
import threading
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（backend 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent

# 加载 .env（位于项目根目录）
load_dotenv(ROOT_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class Config:
    # 程序根目录（backend 的上一级）：前端、只读知识库/题库等随程序分发的资源都在这里。
    # 注意：与可写的 DATA_DIR 区分——打包后 DATA_DIR 可能被指到用户目录，ROOT_DIR 始终是程序目录。
    ROOT_DIR = ROOT_DIR

    # ── 环境变量默认值（可被运行时设置覆盖） ──
    _ENV_LLM_API_KEY = _get("LLM_API_KEY", "")
    _ENV_LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    _ENV_LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")

    # 知识库
    KNOWLEDGE_DIR = (ROOT_DIR / _get("KNOWLEDGE_DIR", "../json_outputs")).resolve()
    # 重构后的方法百科目录（build_knowledge.py 生成的 concepts/*.json，新 schema_version=3）
    CONCEPTS_DIR = (ROOT_DIR / _get("CONCEPTS_DIR", "data/knowledge/concepts")).resolve()
    RETRIEVAL_TOP_K = int(_get("RETRIEVAL_TOP_K", "5"))
    # 相对阈值：保留得分 >= 最高分 * RATIO 的结果；最高分 < FLOOR 则判为「超出范围」
    RETRIEVAL_RELATIVE_RATIO = float(_get("RETRIEVAL_RELATIVE_RATIO", "0.4"))
    RETRIEVAL_ABS_FLOOR = float(_get("RETRIEVAL_ABS_FLOOR", "2.0"))

    # 上传文件
    UPLOAD_MAX_BYTES = int(_get("UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
    UPLOAD_ALLOWED_EXTS = {
        # 表格 / 数据（用 run_python + pandas 读）
        ".csv", ".xlsx", ".xls", ".json",
        # 文档（用 read_document + MarkItDown 读）
        ".txt", ".md", ".py", ".pdf", ".docx", ".doc",
        ".pptx", ".ppt", ".html", ".htm",
        # 图片（上传保存；内容理解需视觉模型）
        ".png", ".jpg", ".jpeg", ".gif",
    }

    # 代码执行
    CODE_TIMEOUT = int(_get("CODE_TIMEOUT", "30"))
    MAX_CODE_ITERATIONS = int(_get("MAX_CODE_ITERATIONS", "3"))

    # 本地文件操作（read_file / write_file / list_dir / delete_file）
    FILEOPS_MAX_READ_BYTES = int(_get("FILEOPS_MAX_READ_BYTES", str(5 * 1024 * 1024)))
    FILEOPS_READ_CHAR_LIMIT = int(_get("FILEOPS_READ_CHAR_LIMIT", "20000"))
    FILEOPS_LIST_LIMIT = int(_get("FILEOPS_LIST_LIMIT", "500"))

    # 数据目录
    # 默认在项目根目录下的 data/；打包分发时可用环境变量 MMAGENT_DATA_DIR 指向
    # 一个可写目录（如 %LOCALAPPDATA%），使只读的程序目录与可写的用户数据分离。
    DATA_DIR = Path(_get("MMAGENT_DATA_DIR", "")).expanduser() if _get("MMAGENT_DATA_DIR", "") else ROOT_DIR / "data"
    CONVERSATIONS_DIR = DATA_DIR / "conversations"
    # 做题模式的对话与独立练习物理隔离，避免混入独立练习的「会话历史」列表
    SOLVE_CONVERSATIONS_DIR = DATA_DIR / "solve_conversations"
    # 做题会话存档（题目快照 + 路线 + 进度 + 每步对话 id），用于刷新后恢复
    SOLVE_SESSIONS_DIR = DATA_DIR / "solve_sessions"
    RUNS_DIR = DATA_DIR / "runs"
    PROFILES_DIR = DATA_DIR / "profiles"
    SETTINGS_FILE = DATA_DIR / "settings.json"

    # 真题题库目录（开发者手写 JSON，只读，随程序分发）
    PROBLEMS_DIR = (ROOT_DIR / _get("PROBLEMS_DIR", "data/problems")).resolve()
    # 用户在工作台「做题」模式上传后由 AI 动态生成的题目（同 schema，运行时产生 → 跟随可写 DATA_DIR）
    DYNAMIC_PROBLEMS_DIR = (
        Path(_get("DYNAMIC_PROBLEMS_DIR")).expanduser().resolve()
        if _get("DYNAMIC_PROBLEMS_DIR") else (DATA_DIR / "dynamic_problems").resolve()
    )
    # 真题附带的数据文件（按题号分子目录，供学生下载）
    PROBLEM_ASSETS_DIR = (ROOT_DIR / _get("PROBLEM_ASSETS_DIR", "data/problem_assets")).resolve()
    # 优秀论文全文缓存（按题号分子目录，<paper_id>.txt，供运行时对照/检索）
    PROBLEM_PAPERS_DIR = (ROOT_DIR / _get("PROBLEM_PAPERS_DIR", "data/problem_papers")).resolve()

    # 服务
    HOST = _get("HOST", "127.0.0.1")
    PORT = int(_get("PORT", "8000"))

    # ── 内置计费网关（新手「用我们提供的额度」一键路径） ──
    # 网关注册站点：用户在这里注册账号、领/兑换额度、生成 API Key。
    # 部署你自己的 gateway/ 后，把下面两个值改成你的公网域名（或在 .env 覆盖）。
    GATEWAY_SIGNUP_URL = _get("GATEWAY_SIGNUP_URL", "")
    # 网关的 OpenAI 兼容端点（填到设置里的 Base URL），通常是 https://你的域名/v1
    GATEWAY_BASE_URL = _get("GATEWAY_BASE_URL", "")
    # 走网关时默认使用的模型名
    GATEWAY_MODEL = _get("GATEWAY_MODEL", "deepseek-chat")

    # ── 运行时 LLM 设置（字典，优先于环境变量） ──
    _lock = threading.Lock()
    _runtime = {
        "api_key": "",
        "base_url": "",
        "model": "",
    }

    @classmethod
    def _load_runtime_settings(cls):
        """从 settings.json 恢复运行时设置。"""
        try:
            if cls.SETTINGS_FILE.exists():
                data = json.loads(cls.SETTINGS_FILE.read_text(encoding="utf-8"))
                with cls._lock:
                    cls._runtime["api_key"] = data.get("api_key", "")
                    cls._runtime["base_url"] = data.get("base_url", "")
                    cls._runtime["model"] = data.get("model", "")
        except Exception:
            pass

    @classmethod
    def _save_runtime_settings(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with cls._lock:
            cls.SETTINGS_FILE.write_text(
                json.dumps(dict(cls._runtime), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # ── LLM 配置读取（运行时优先） ──
    @classmethod
    def get_llm_api_key(cls) -> str:
        cls._load_runtime_settings()
        with cls._lock:
            return cls._runtime["api_key"] or cls._ENV_LLM_API_KEY

    @classmethod
    def get_llm_base_url(cls) -> str:
        cls._load_runtime_settings()
        with cls._lock:
            return cls._runtime["base_url"] or cls._ENV_LLM_BASE_URL

    @classmethod
    def get_llm_model(cls) -> str:
        cls._load_runtime_settings()
        with cls._lock:
            return cls._runtime["model"] or cls._ENV_LLM_MODEL

    @classmethod
    def get_settings(cls) -> dict:
        """返回当前完整设置（不暴露 API 密钥明文）。"""
        cls._load_runtime_settings()
        with cls._lock:
            api_key = cls._runtime["api_key"] or cls._ENV_LLM_API_KEY
        return {
            "base_url": cls.get_llm_base_url(),
            "model": cls.get_llm_model(),
            "api_key": api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****",
            "has_api_key": bool(api_key),
        }

    @classmethod
    def update_settings(cls, api_key: str = "", base_url: str = "", model: str = "") -> dict:
        """更新运行时 LLM 设置，保存到 settings.json。"""
        with cls._lock:
            if api_key and api_key != "****":
                cls._runtime["api_key"] = api_key
            if base_url:
                cls._runtime["base_url"] = base_url
            if model:
                cls._runtime["model"] = model
        cls._save_runtime_settings()
        return cls.get_settings()

    @classmethod
    def ensure_dirs(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        cls.SOLVE_CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        cls.SOLVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        cls.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        cls.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        cls.PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
        cls.DYNAMIC_PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
        cls.PROBLEM_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        cls.PROBLEM_PAPERS_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls) -> list[str]:
        """返回配置问题列表（空列表表示正常）。"""
        problems = []
        key = cls.get_llm_api_key()
        if not key or key == "sk-your-api-key-here":
            problems.append(
                "LLM_API_KEY 未配置。请复制 .env.example 为 .env 并填入你的 API 密钥。"
            )
        if not cls.KNOWLEDGE_DIR.exists():
            problems.append(f"知识库目录不存在：{cls.KNOWLEDGE_DIR}")
        return problems


config = Config()
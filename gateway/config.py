"""网关配置：从环境变量 / .env 读取。

这是一个独立部署在服务器上的 OpenAI 兼容计费网关：
  - 对外暴露 /v1/chat/completions（OpenAI 兼容），本地 app 把它当普通 LLM 端点用；
  - 用网关签发的 API key（sk-xxx）鉴权，按 token 扣费；
  - 同时是一个带登录/余额/卡密充值/API key 管理的小网站。

它把请求转发给真正的上游（DeepSeek 等），自己只做账号、计费、转发。
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger(__name__)


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


_SETTINGS_PATH = ROOT_DIR / "data" / "gateway_settings.json"
_runtime_settings: dict = {}


def _load_runtime_settings():
    """从 gateway_settings.json 加载运行时配置（优先于 .env）。"""
    global _runtime_settings
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                _runtime_settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            _runtime_settings = {}
    else:
        _runtime_settings = {}


def save_runtime_settings(new_settings: dict):
    """保存运行时配置（合并更新），持久化到 gateway_settings.json。"""
    global _runtime_settings
    _runtime_settings.update(new_settings)
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(_runtime_settings, f, ensure_ascii=False, indent=2)


def get_upstream_api_key() -> str:
    """获取上游 API Key：运行时设置优先，否则回退到 .env。"""
    runtime_key = _runtime_settings.get("api_key", "")
    return runtime_key or _get("UPSTREAM_API_KEY", "")


def get_upstream_base_url() -> str:
    runtime_url = _runtime_settings.get("base_url", "")
    return runtime_url or _get("UPSTREAM_BASE_URL", "https://api.deepseek.com/v1")


def get_allowed_models() -> set:
    runtime_models = _runtime_settings.get("model", "")
    env_models = _get("ALLOWED_MODELS", "deepseek-v4-pro")
    combined = f"{runtime_models},{env_models}"
    return {m.strip() for m in combined.split(",") if m.strip()}


# 启动时加载
_load_runtime_settings()


def _compute_upstream_key() -> str:
    return _runtime_settings.get("api_key", "") or _get("UPSTREAM_API_KEY", "")


def _compute_upstream_url() -> str:
    return _runtime_settings.get("base_url", "") or _get("UPSTREAM_BASE_URL", "https://api.deepseek.com/v1")


def _compute_allowed_models() -> set:
    runtime_models = _runtime_settings.get("model", "")
    env_models = _get("ALLOWED_MODELS", "deepseek-v4-pro")
    combined = f"{runtime_models},{env_models}"
    return {m.strip() for m in combined.split(",") if m.strip()}


def _compute_knowledge_model() -> str:
    return _runtime_settings.get("model", "") or _get("KNOWLEDGE_MODEL", "")


class Config:
    # ── 上游 LLM（启动时从 runtime settings + .env 计算，save_runtime_settings() 后调用 reload() 刷新）──
    UPSTREAM_API_KEY: str = _compute_upstream_key()
    UPSTREAM_BASE_URL: str = _compute_upstream_url()
    ALLOWED_MODELS: set = _compute_allowed_models()
    KNOWLEDGE_MODEL: str = _compute_knowledge_model()

    # ── 计费 ──
    # 计费倍率：上游成本 × markup = 售价，覆盖利润与汇率/价格波动。
    BILLING_MARKUP = float(_get("BILLING_MARKUP", "2.0"))
    # 新用户注册赠送的免费 token 额度（按总 token 计，0 = 不送）。
    FREE_TOKENS_ON_SIGNUP = int(_get("FREE_TOKENS_ON_SIGNUP", "1500000"))
    # 模型价格表：model 名 → (输入价, 输出价)，单位「元 / 百万 token」。
    # ⚠️ 纯 token 模式下金额计费休眠；占位价仅作兜底，上线前务必核对上游官网真实价格。
    MODEL_PRICES = {
        "deepseek-v4-pro": (4.0, 16.0),
    }
    MODEL_PRICE_DEFAULT = (4.0, 16.0)

    # ── 鉴权 / 安全 ──
    # 网站登录 token 的 HMAC 签名密钥；务必在 .env 改强随机值（openssl rand -hex 32）。
    AUTH_SECRET = _get("AUTH_SECRET", "dev-insecure-change-me")
    # 登录 token 有效期（秒），默认 30 天。
    AUTH_TOKEN_TTL = int(_get("AUTH_TOKEN_TTL", str(30 * 24 * 3600)))
    # 管理员口令：调用 /api/admin/* 需在请求头 X-Admin-Token 带此值。务必改强随机值。
    ADMIN_TOKEN = _get("ADMIN_TOKEN", "dev-admin-change-me")

    # ── 邮件发送（腾讯云 SES API，注册验证码） ──
    # 腾讯云 API 密钥：https://console.cloud.tencent.com/cam/capi
    SES_SECRET_ID = _get("SES_SECRET_ID", "")
    SES_SECRET_KEY = _get("SES_SECRET_KEY", "")
    # SES 地域（目前支持 ap-guangzhou / ap-hongkong）
    SES_REGION = _get("SES_REGION", "ap-guangzhou")
    # 邮件模板 ID（需先在 SES 控制台创建并审核通过，模板含 {{code}} 变量）
    SES_TEMPLATE_ID = int(_get("SES_TEMPLATE_ID", "0"))
    # 发信地址（需在 SES 控制台验证通过）
    SMTP_FROM = _get("SMTP_FROM", "noreply@math-modeling.top")

    # ── 知识大厅 ──
    KNOWLEDGE_DATA_DIR = ROOT_DIR / "data" / "knowledge"
    # AI 问答检索时取 Top-K 个概念注入上下文
    KNOWLEDGE_TOP_K = int(_get("KNOWLEDGE_TOP_K", "5"))

    # ── 文献检索（/v1/literature/search，供本地 app 走服务端搜文献） ──
    # Semantic Scholar 免费 API key（https://www.semanticscholar.org/product/api 申请），
    # 填上后突破匿名限流(429)；不填则匿名（易 429）。arXiv 无需 key，但需服务端能直连或挂代理。
    SEMANTIC_SCHOLAR_API_KEY = _get("SEMANTIC_SCHOLAR_API_KEY", "")

    # ── 数据 / 服务 ──
    DATA_DIR = ROOT_DIR / "data"
    STATIC_DIR = ROOT_DIR / "static"
    HOST = _get("HOST", "127.0.0.1")
    PORT = int(_get("PORT", "9000"))

    @classmethod
    def reload(cls):
        """重新计算上游配置（save_runtime_settings 后调用）。"""
        cls.UPSTREAM_API_KEY = _compute_upstream_key()
        cls.UPSTREAM_BASE_URL = _compute_upstream_url()
        cls.ALLOWED_MODELS = _compute_allowed_models()
        cls.KNOWLEDGE_MODEL = _compute_knowledge_model()

    @classmethod
    def db_path(cls) -> Path:
        return cls.DATA_DIR / "gateway.db"

    @classmethod
    def ensure_dirs(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls) -> list[str]:
        problems = []
        if not cls.UPSTREAM_API_KEY:
            problems.append("UPSTREAM_API_KEY 未配置：网关无法转发到上游 LLM。请在 .env 填入。")
        if cls.AUTH_SECRET == "dev-insecure-change-me":
            problems.append("AUTH_SECRET 仍是默认值：公网部署前务必改为强随机值。")
        if cls.ADMIN_TOKEN == "dev-admin-change-me":
            problems.append("ADMIN_TOKEN 仍是默认值：公网部署前务必改为强随机值。")
        return problems

    @classmethod
    def prices(cls, model: str) -> tuple[float, float]:
        return cls.MODEL_PRICES.get(model, cls.MODEL_PRICE_DEFAULT)


config = Config()

"""网关配置：从环境变量 / .env 读取。

这是一个独立部署在服务器上的 OpenAI 兼容计费网关：
  - 对外暴露 /v1/chat/completions（OpenAI 兼容），本地 app 把它当普通 LLM 端点用；
  - 用网关签发的 API key（sk-xxx）鉴权，按 token 扣费；
  - 同时是一个带登录/余额/卡密充值/API key 管理的小网站。

它把请求转发给真正的上游（DeepSeek 等），自己只做账号、计费、转发。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class Config:
    # ── 上游 LLM（真正花钱的地方，密钥只存在服务器上，绝不下发给用户） ──
    UPSTREAM_API_KEY = _get("UPSTREAM_API_KEY", "")
    UPSTREAM_BASE_URL = _get("UPSTREAM_BASE_URL", "https://api.deepseek.com/v1")
    # 允许调用的模型白名单（逗号分隔）；空=不限制。防止用户借网关调贵模型。
    _allowed = _get("ALLOWED_MODELS", "deepseek-v4-pro")
    ALLOWED_MODELS = {m.strip() for m in _allowed.split(",") if m.strip()}

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

    # ── 邮件发送（SMTP，用于注册验证码） ──
    SMTP_HOST = _get("SMTP_HOST", "sg-smtp.qcloudmail.com")
    SMTP_PORT = int(_get("SMTP_PORT", "465"))
    SMTP_USER = _get("SMTP_USER", "")
    SMTP_PASSWORD = _get("SMTP_PASSWORD", "")
    SMTP_FROM = _get("SMTP_FROM", "noreply@math-modeling.top")

    # ── 数据 / 服务 ──
    DATA_DIR = ROOT_DIR / "data"
    STATIC_DIR = ROOT_DIR / "static"
    HOST = _get("HOST", "127.0.0.1")
    PORT = int(_get("PORT", "9000"))

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

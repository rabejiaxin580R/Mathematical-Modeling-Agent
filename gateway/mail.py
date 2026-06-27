"""邮件发送模块：通过 SMTP 发验证码邮件。

使用 Python 标准库 smtplib + email.mime，零第三方依赖。
腾讯云 SES 默认 SSL 465 端口。
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import config

logger = logging.getLogger(__name__)

# ── 验证码邮件模板 ──
_CODE_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"></head>
<body style="margin:0;background:#f5f7fa;padding:40px 0;font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif">
  <table width="100%%" cellpadding="0" cellspacing="0" style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.08)">
    <tr>
      <td style="background:linear-gradient(135deg,#5b8cff,#7d5bff);padding:28px 32px;text-align:center">
        <h1 style="margin:0;font-size:22px;color:#fff">数学建模助教</h1>
        <p style="margin:8px 0 0;font-size:13px;color:rgba(255,255,255,.8)">邮箱验证码 · 注册</p>
      </td>
    </tr>
    <tr>
      <td style="padding:28px 32px">
        <p style="margin:0 0 8px;font-size:14px;color:#555">你好，</p>
        <p style="margin:0 0 24px;font-size:14px;color:#555;line-height:1.8">你正在注册 <strong>数学建模助教</strong> 账号。请输入以下验证码完成注册：</p>
        <div style="background:#f0f4ff;border:2px dashed #5b8cff;border-radius:10px;padding:20px;text-align:center;margin-bottom:24px">
          <span style="font-size:36px;font-weight:800;letter-spacing:6px;color:#5b8cff">{code}</span>
        </div>
        <p style="margin:0 0 8px;font-size:13px;color:#999">验证码 <strong>10 分钟</strong> 内有效，请勿转发给他人。</p>
        <p style="margin:0;font-size:13px;color:#999">如果这不是你本人的操作，请忽略此邮件。</p>
      </td>
    </tr>
    <tr>
      <td style="background:#fafbfc;padding:16px 32px;text-align:center">
        <p style="margin:0;font-size:12px;color:#bbb">Powered by 数学建模助教 · math-modeling.top</p>
      </td>
    </tr>
  </table>
</body>
</html>"""

_CODE_TEXT = """\
数学建模助教 · 邮箱验证码

你的验证码是：{code}

验证码 10 分钟内有效，请勿转发给他人。

如果这不是你本人的操作，请忽略此邮件。

—— 数学建模助教（math-modeling.top）"""


def _render(to: str, code: str) -> dict[str, str]:
    """准备主题和正文（HTML + 纯文本备用）。"""
    subject = f"数学建模助教 验证码：{code}"
    html = _CODE_HTML.replace("{code}", code)  # safe: code is 6 digits
    text = _CODE_TEXT.replace("{code}", code)
    return {"subject": subject, "html": html, "text": text}


def send_code(to_email: str, code: str) -> None:
    """发送验证码到指定邮箱。失败抛异常（调用方可 catch 并返回友好提示）。"""
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        raise RuntimeError("SMTP 未配置（SMTP_USER / SMTP_PASSWORD 为空）")

    rendered = _render(to_email, code)
    msg = MIMEMultipart("alternative")
    msg["From"] = config.SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = rendered["subject"]
    msg.attach(MIMEText(rendered["text"], "plain", "utf-8"))
    msg.attach(MIMEText(rendered["html"], "html", "utf-8"))

    try:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_FROM, to_email, msg.as_string())
        server.quit()
        logger.info("验证码已发送 %s → %s", config.SMTP_FROM, to_email)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP 认证失败 user=%s host=%s", config.SMTP_USER, config.SMTP_HOST)
        raise RuntimeError("邮件服务认证失败，请联系管理员检查 SMTP 配置")
    except smtplib.SMTPException as e:
        logger.error("SMTP 错误: %s", e)
        raise RuntimeError(f"邮件发送失败：{e}")


def test_config(to_email: str = "") -> str | None:
    """检测 SMTP 配置是否正确。返回 None 表示通过，否则返回错误描述。"""
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        return "SMTP_USER 或 SMTP_PASSWORD 未配置"
    try:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        if to_email:
            # 发一封测试邮件
            test_msg = MIMEMultipart("alternative")
            test_msg["From"] = config.SMTP_FROM
            test_msg["To"] = to_email
            test_msg["Subject"] = "数学建模助教 · SMTP 测试"
            test_msg.attach(MIMEText(
                "如果你收到这封邮件，说明 SMTP 配置正确，可以正常发信。\n"
                "—— 数学建模助教", "plain", "utf-8"))
            server.sendmail(config.SMTP_FROM, to_email, test_msg.as_string())
            logger.info("SMTP 测试邮件已发送 %s → %s", config.SMTP_FROM, to_email)
        server.quit()
        return None  # 通过
    except smtplib.SMTPAuthenticationError as e:
        return f"SMTP 认证失败，请检查 SMTP_USER / SMTP_PASSWORD：{e}"
    except smtplib.SMTPConnectError as e:
        return f"无法连接 SMTP 服务器 {config.SMTP_HOST}:{config.SMTP_PORT}：{e}"
    except smtplib.SMTPException as e:
        return f"SMTP 错误：{e}"

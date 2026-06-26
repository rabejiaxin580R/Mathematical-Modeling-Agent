"""代理转发：把 OpenAI 兼容请求转发到上游 LLM，流式同时累计 token 用量。

设计要点：
  - 用 httpx 直接转发到上游 /chat/completions，保持 stream 透传，对客户端无感。
  - 强制注入 stream_options.include_usage=true，确保流式也能从末尾 chunk 拿到 usage。
  - 流式：边转发边解析 SSE 的 usage 字段，结束后回调 on_usage(model, p, c) 扣费。
  - 非流式：从响应 JSON 的 usage 字段拿，回调扣费。
  - 上游 key 只存在网关，绝不下发；客户端用网关签发的 API key 鉴权（在 app.py 完成）。
"""
import json
import logging
from typing import Callable

import httpx

from .config import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def _upstream_url(path: str = "/chat/completions") -> str:
    return config.UPSTREAM_BASE_URL.rstrip("/") + path


def _upstream_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.UPSTREAM_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_usage(usage: dict | None) -> tuple[int, int]:
    if not usage:
        return 0, 0
    return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)


async def stream_chat(payload: dict, on_usage: Callable[[str, int, int], None]):
    """流式转发：异步生成上游返回的 SSE 原始字节，结束时回调 on_usage 扣费。

    payload 已由调用方校验过模型白名单。on_usage(model, prompt_tokens, completion_tokens)
    在流结束（拿到 usage 或流断开）时调用一次。
    """
    body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
    model = payload.get("model", "")
    prompt_tokens = completion_tokens = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream("POST", _upstream_url(), headers=_upstream_headers(),
                                 json=body) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")
                logger.error("上游返回 %s：%s", resp.status_code, detail[:500])
                err = json.dumps({"error": {"message": f"上游错误：{detail[:300]}",
                                            "code": resp.status_code}}, ensure_ascii=False)
                yield f"data: {err}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return

            async for line in resp.aiter_lines():
                if not line:
                    yield b"\n"
                    continue
                # 透传每一行；同时窥探 usage（OpenAI 流末尾 chunk 携带）
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data and data != "[DONE]":
                        try:
                            obj = json.loads(data)
                            if obj.get("usage"):
                                p, c = _extract_usage(obj["usage"])
                                prompt_tokens, completion_tokens = p, c
                        except (json.JSONDecodeError, AttributeError):
                            pass
                yield (line + "\n\n").encode("utf-8")

    # 流结束：扣费（即便 usage 为 0 也回调，便于上层记录/日志）
    try:
        on_usage(model, prompt_tokens, completion_tokens)
    except Exception:
        logger.exception("扣费回调失败 model=%s", model)


async def complete_chat(payload: dict, on_usage: Callable[[str, int, int], None]) -> tuple[int, dict]:
    """非流式转发：返回 (status_code, 上游 JSON)，并在成功时回调 on_usage 扣费。"""
    body = {**payload, "stream": False}
    model = payload.get("model", "")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_upstream_url(), headers=_upstream_headers(), json=body)
    try:
        data = resp.json()
    except Exception:
        data = {"error": {"message": resp.text[:300], "code": resp.status_code}}

    if resp.status_code == 200:
        p, c = _extract_usage(data.get("usage"))
        try:
            on_usage(model, p, c)
        except Exception:
            logger.exception("扣费回调失败 model=%s", model)
    return resp.status_code, data

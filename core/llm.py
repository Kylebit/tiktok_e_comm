"""OpenAI 兼容 Chat Completions（支持 OpenAI / DeepSeek / 其他兼容网关）。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from core.config import load_settings
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry


def ai_config() -> dict:
    s = load_settings()
    cfg = s.get("ai") or {}
    api_key = os.environ.get("OPENAI_API_KEY") or cfg.get("api_key") or ""
    return {
        "api_key": api_key.strip(),
        "base_url": (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/"),
        "model": cfg.get("model") or "gpt-4o-mini",
        "temperature": float(cfg.get("temperature", 0.4)),
        "max_tokens": int(cfg.get("max_tokens", 320)),
        "timeout": int(cfg.get("timeout", 60)),
    }


def require_api_key() -> str:
    key = ai_config()["api_key"]
    if not key:
        raise RuntimeError(
            "未配置 AI API Key。请在 config/settings.json 的 ai.api_key 填写，"
            "或设置环境变量 OPENAI_API_KEY"
        )
    return key


def chat_completion(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    cfg = ai_config()
    api_key = require_api_key()
    body = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else temperature,
        "max_tokens": max_tokens or cfg["max_tokens"],
    }
    url = f"{cfg['base_url']}/chat/completions"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urlopen_retry(req, timeout=cfg["timeout"], context=SSL_CTX) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"AI 请求失败 HTTP {e.code}: {err[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"AI 网络错误: {e.reason or e}") from e

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"AI 返回为空: {str(result)[:200]}")
    content = (choices[0].get("message") or {}).get("content") or ""
    content = content.strip()
    if not content:
        raise RuntimeError("AI 未返回标题内容")
    return content

"""大模型调用。只做两件事：把一句话解析成 JSON，把一段证据写成白话。

提供方由环境变量决定，两条路都可用：

* ``anthropic``     —— Claude，走官方 ``anthropic`` SDK，``output_config.format`` 约束 JSON；
  开启服务端 refusal fallback（``fallbacks="default"``），被安全分类器拒绝时自动换模型重答；
* ``openai_compat`` —— DeepSeek / 通义 / Kimi 等 OpenAI 兼容接口，``response_format=json_object``；
* ``off``           —— 不调用大模型，解析走规则兜底。

Key 只从环境变量或本地文件读，不写日志。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class LLMError(Exception):
    pass


def _read_key() -> str | None:
    if os.getenv("MINGSHAI_LLM_API_KEY"):
        return os.environ["MINGSHAI_LLM_API_KEY"].strip()
    f = os.getenv("MINGSHAI_LLM_API_KEY_FILE")
    if f and Path(f).expanduser().exists():
        return Path(f).expanduser().read_text(encoding="utf-8").strip()
    return None


def config() -> dict:
    provider = os.getenv("MINGSHAI_LLM_PROVIDER", "").strip().lower()
    key = _read_key()
    if not provider:
        if os.getenv("ANTHROPIC_API_KEY") or (key and key.startswith("sk-ant-")):
            provider = "anthropic"
        elif key and os.getenv("MINGSHAI_LLM_BASE_URL"):
            provider = "openai_compat"
        else:
            provider = "off"
    model = os.getenv("MINGSHAI_LLM_MODEL") or (DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else "")
    return {
        "provider": provider, "model": model, "key": key,
        "base_url": os.getenv("MINGSHAI_LLM_BASE_URL", "").rstrip("/"),
        "timeout": float(os.getenv("MINGSHAI_LLM_TIMEOUT", "45")),
        "effort": os.getenv("MINGSHAI_LLM_EFFORT", "low"),
    }


def available() -> bool:
    c = config()
    if c["provider"] == "anthropic":
        return bool(c["key"] or os.getenv("ANTHROPIC_API_KEY"))
    if c["provider"] == "openai_compat":
        return bool(c["key"] and c["base_url"] and c["model"])
    return False


def public_info() -> dict:
    c = config()
    return {"provider": c["provider"], "model": c["model"] or None, "available": available()}


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 4000) -> tuple[dict, dict]:
    """返回 (解析后的 JSON, 调用信息)。失败抛 LLMError，由调用方决定兜底。"""
    c = config()
    t0 = time.monotonic()
    if c["provider"] == "anthropic":
        text, info = _anthropic(c, system, user, schema, max_tokens)
    elif c["provider"] == "openai_compat":
        text, info = _openai_compat(c, system, user, max_tokens)
    else:
        raise LLMError("未配置大模型")
    info["latency_ms"] = int((time.monotonic() - t0) * 1000)
    try:
        return json.loads(_strip_fence(text)), info
    except json.JSONDecodeError as e:
        raise LLMError(f"大模型返回的不是合法 JSON：{e}；前 120 字：{text[:120]!r}") from e


def complete_text(system: str, user: str, max_tokens: int = 1200) -> tuple[str, dict]:
    c = config()
    t0 = time.monotonic()
    if c["provider"] == "anthropic":
        text, info = _anthropic(c, system, user, None, max_tokens)
    elif c["provider"] == "openai_compat":
        text, info = _openai_compat(c, system, user, max_tokens, json_mode=False)
    else:
        raise LLMError("未配置大模型")
    info["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return text.strip(), info


def _strip_fence(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _anthropic(c: dict, system: str, user: str, schema: dict | None, max_tokens: int) -> tuple[str, dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=c["key"] or None, timeout=c["timeout"], max_retries=1)
    output_config: dict = {"effort": c["effort"]}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = client.beta.messages.create(
            model=c["model"], max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        raise LLMError("大模型限流，请稍后再试") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"大模型接口错误 {e.status_code}：{getattr(e, 'message', '')[:160]}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("连不上大模型接口") from e
    if resp.stop_reason == "refusal":
        raise LLMError("大模型拒绝回答（安全分类器）")
    if resp.stop_reason == "max_tokens":
        raise LLMError("大模型输出被截断")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return text, {"provider": "anthropic", "model": resp.model,
                  "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}


def _openai_compat(c: dict, system: str, user: str, max_tokens: int, json_mode: bool = True) -> tuple[str, dict]:
    body = {
        "model": c["model"], "max_tokens": max_tokens, "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(f"{c['base_url']}/chat/completions", json=body, timeout=c["timeout"],
                       headers={"Authorization": f"Bearer {c['key']}"})
    except httpx.HTTPError as e:
        raise LLMError(f"连不上大模型接口：{type(e).__name__}") from e
    if r.status_code != 200:
        raise LLMError(f"大模型接口错误 {r.status_code}：{r.text[:160]}")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise LLMError("大模型输出被截断")
    usage = data.get("usage") or {}
    return (choice.get("message") or {}).get("content") or "", {
        "provider": "openai_compat", "model": data.get("model", c["model"]),
        "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
    }

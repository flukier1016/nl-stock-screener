"""大模型调用。只做两件事：把一句话解析成 JSON，把一段证据写成白话。

提供方由环境变量决定，两条路都可用：

* ``anthropic``     —— Claude，走官方 ``anthropic`` SDK，``output_config.format`` 约束 JSON；
  开启服务端 refusal fallback（``fallbacks="default"``），被安全分类器拒绝时自动换模型重答；
* ``openai_compat`` —— DeepSeek / 通义 / Kimi 等 OpenAI 兼容接口，``response_format=json_object``；
* ``off``           —— 不调用大模型，解析走规则兜底。

Key 只从环境变量或本地文件读，不写日志。每次调用都过总额度账本（见 ``budget.py``）：
累计花费到上限就永久切断，并删除服务器上的 Key。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from . import budget

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
# 明确没有计费的状态码（超时、限流、过载、服务端错误）才手动重试一次
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


class LLMError(Exception):
    """大模型调用失败。``cost_usd`` 是这次已经产生的花费（拒答、截断也计费）；
    ``maybe_billed`` 表示不知道是否已计费（超时、连接中断），记账时按预留额度算。"""

    def __init__(self, msg: str, *, cost_usd: float | None = None, maybe_billed: bool = False, usage: dict | None = None):
        super().__init__(msg)
        self.cost_usd, self.maybe_billed, self.usage = cost_usd, maybe_billed, usage or {}


class BudgetError(LLMError):
    """总额度已用完，或接近上限暂时不调用。"""


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
    if budget.LEDGER.status()["disabled"]:
        return False
    c = config()
    if c["provider"] == "anthropic":
        return bool(c["key"] or os.getenv("ANTHROPIC_API_KEY"))
    if c["provider"] == "openai_compat":
        return bool(c["key"] and c["base_url"] and c["model"])
    return False


def unavailable_reason() -> str:
    st = budget.LEDGER.status()
    return (st["reason"] or "AI 额度已用完") if st["disabled"] else "未配置大模型"


def _key_source() -> str:
    """Key 从哪里读（不含 Key 本身）：env / file / none。"""
    if os.getenv("MINGSHAI_LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY"):
        return "env"
    f = os.getenv("MINGSHAI_LLM_API_KEY_FILE")
    return "file" if f and Path(f).expanduser().exists() else "none"


def public_info() -> dict:
    c = config()
    return {"provider": c["provider"], "model": c["model"] or None, "available": available(),
            "key_source": _key_source(), "budget": budget.LEDGER.status()}


def prepare_key() -> dict:
    """服务启动时调用。额度已切断就确认 Key 不在；否则把环境变量里的 Key 搬进 Key 文件（数据卷上，权限 600），
    并从进程里清掉环境变量这一份：到上限时删掉文件，服务器上就不再有 Key。返回额度状态（不含 Key）。"""
    st = budget.LEDGER.check()
    if st["disabled"]:
        return st
    env_key = (os.getenv("MINGSHAI_LLM_API_KEY") or "").strip()
    f = os.getenv("MINGSHAI_LLM_API_KEY_FILE")
    if env_key and f:
        p = Path(f).expanduser()
        if not p.exists() or p.read_text(encoding="utf-8").strip() != env_key:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(env_key)
            os.replace(tmp, p)
        os.environ.pop("MINGSHAI_LLM_API_KEY", None)
    return st


_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "fallback")


def _call(kind: str, system: str, user: str, schema: dict | None, max_tokens: int) -> tuple[str, dict]:
    """预留额度 → 调用 → 按实际 usage 记账。任何失败都先记账再抛 LLMError。"""
    c = config()
    if c["provider"] not in ("anthropic", "openai_compat"):
        raise LLMError("未配置大模型")
    est = budget.estimate_tokens(system, user, json.dumps(schema, ensure_ascii=False) if schema else "")
    worst = budget.worst_case_usd(c["model"], est, max_tokens, fallback=c["provider"] == "anthropic")
    try:
        hold = budget.LEDGER.reserve(worst)
    except (budget.Exhausted, budget.Busy) as e:
        raise BudgetError(str(e)) from e
    row = {"kind": kind, "provider": c["provider"], "reserved_usd": round(worst, 6)}
    t0 = time.monotonic()
    try:
        if c["provider"] == "anthropic":
            text, info = _anthropic(c, system, user, schema, max_tokens)
        else:
            text, info = _openai_compat(c, system, user, max_tokens, json_mode=schema is not None)
    except LLMError as e:
        cost = e.cost_usd if e.cost_usd is not None else (worst if e.maybe_billed else 0.0)
        budget.LEDGER.settle(hold, cost, {**row, "outcome": "error", "error": str(e)[:80], **e.usage})
        raise
    except Exception as e:  # noqa: BLE001 —— 意外异常不知道是否已计费，按预留额度记
        budget.LEDGER.settle(hold, worst, {**row, "outcome": "exception", "error": type(e).__name__})
        raise LLMError(f"大模型调用异常：{type(e).__name__}") from e
    info["latency_ms"] = int((time.monotonic() - t0) * 1000)
    budget.LEDGER.settle(hold, info["cost_usd"], {**row, "outcome": "ok", "model": info.get("model"),
                                                  **{k: info.get(k) for k in _USAGE_KEYS if k in info}})
    return text, info


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 4000) -> tuple[dict, dict]:
    """返回 (解析后的 JSON, 调用信息)。失败抛 LLMError，由调用方决定兜底。"""
    text, info = _call("parse", system, user, schema, max_tokens)
    try:
        return json.loads(_strip_fence(text)), info
    except json.JSONDecodeError as e:
        raise LLMError(f"大模型返回的不是合法 JSON：{e}；前 120 字：{text[:120]!r}") from e


def complete_text(system: str, user: str, max_tokens: int = 1200) -> tuple[str, dict]:
    text, info = _call("explain", system, user, None, max_tokens)
    return text.strip(), info


def _strip_fence(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _anthropic(c: dict, system: str, user: str, schema: dict | None, max_tokens: int) -> tuple[str, dict]:
    import anthropic

    # 不让 SDK 自动重试：超时后重发，第一次的花费就记不到账上了
    client = anthropic.Anthropic(api_key=c["key"] or None, timeout=c["timeout"], max_retries=0)
    output_config: dict = {"effort": c["effort"]}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    for attempt in (1, 2):
        try:
            # 系统提示词（口径目录 + 行业表）每次相同，放在缓存断点前；用户原话放在后面
            resp = client.beta.messages.create(
                model=c["model"], max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            break
        except anthropic.APIStatusError as e:
            if attempt == 1 and e.status_code in RETRYABLE_STATUS:
                time.sleep(1.5)
                continue
            if isinstance(e, anthropic.RateLimitError):
                raise LLMError("大模型限流，请稍后再试") from e
            raise LLMError(f"大模型接口错误 {e.status_code}：{getattr(e, 'message', '')[:160]}") from e
        except anthropic.APITimeoutError as e:
            raise LLMError("大模型响应超时", maybe_billed=True) from e
        except anthropic.APIConnectionError as e:
            raise LLMError("连不上大模型接口", maybe_billed=True) from e
    usage = budget.anthropic_usage(resp)
    if resp.stop_reason == "refusal":
        raise LLMError("大模型拒绝回答（安全分类器）", cost_usd=usage["cost_usd"], usage=usage)
    if resp.stop_reason == "max_tokens":
        raise LLMError("大模型输出被截断", cost_usd=usage["cost_usd"], usage=usage)
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return text, {"provider": "anthropic", "model": resp.model, **usage}


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
        raise LLMError(f"连不上大模型接口：{type(e).__name__}", maybe_billed=True) from e
    if r.status_code != 200:
        raise LLMError(f"大模型接口错误 {r.status_code}：{r.text[:160]}")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    u = data.get("usage") or {}
    model = data.get("model", c["model"])
    usage = {"input_tokens": int(u.get("prompt_tokens") or 0), "output_tokens": int(u.get("completion_tokens") or 0)}
    usage["cost_usd"] = budget.cost_usd(model, **usage)  # 价目表里没有，按贵一档记
    if choice.get("finish_reason") == "length":
        raise LLMError("大模型输出被截断", cost_usd=usage["cost_usd"], usage=usage)
    return (choice.get("message") or {}).get("content") or "", {"provider": "openai_compat", "model": model, **usage}

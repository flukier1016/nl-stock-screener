"""AI 调用总额度：累计花费到上限就永久切断，并删除服务器上的 Key。

* 上限是总额，不按月清零：``MINGSHAI_LLM_BUDGET_USD``（默认 5 美元）。上线前开发期间已经花掉、
  服务器账本没记到的部分，用 ``MINGSHAI_LLM_SPENT_OFFSET_USD`` 计入；
* 每次调用前按最坏情况预留：输入全按缓存写入价，输出按 max_tokens 写满，再加一次被拒后换模型重答。
  剩余额度不够预留就不发请求、当场切断，所以累计花费不会超过上限；
* 调用后按接口返回的 usage 记实际花费（含 ``usage.iterations`` 里换模型重答的部分）；
  超时或连接中断时不知道是否已计费，按预留额度记，宁可多记；
* 切断是永久的：切断记录写在数据卷上，同时删除 Key 文件、清掉进程里的 Key，重启也不会恢复。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from .store import DATA_DIR

# 美元 / 百万 token：输入、输出、缓存读取、缓存写入（5 分钟）、缓存写入（1 小时）
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0, 8.0)}
# 价目表里没有的模型（例如被拒后换上的模型）按贵一档的价格记，宁可多记
UNKNOWN_PRICE = (10.0, 50.0, 1.0, 12.5, 20.0)
KEY_ENV_VARS = ("MINGSHAI_LLM_API_KEY", "ANTHROPIC_API_KEY")


class Exhausted(Exception):
    """额度已用完（永久）。"""


class Busy(Exception):
    """额度接近上限、其他调用还没记完账，这次先不调用（暂时）。"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _price(model: str | None) -> tuple[float, float, float, float, float]:
    m = (model or "").strip()
    for name in sorted(PRICES, key=len, reverse=True):
        if m == name or m.startswith(name + "-"):
            return PRICES[name]
    return UNKNOWN_PRICE


def cost_usd(model: str | None, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
             cache_write_5m: int = 0, cache_write_1h: int = 0) -> float:
    p_in, p_out, p_read, p_w5, p_w1 = _price(model)
    return (input_tokens * p_in + output_tokens * p_out + cache_read * p_read
            + cache_write_5m * p_w5 + cache_write_1h * p_w1) / 1e6


def estimate_tokens(*texts: str) -> int:
    """输入 token 的保守上限：UTF-8 字节数的一半（中文约 1.5 token/字、英文约 0.5 token/字符，都比实际多），再加 1000 的协议开销。"""
    return sum(len(t.encode("utf-8")) for t in texts) // 2 + 1000


def worst_case_usd(model: str | None, input_tokens: int, max_tokens: int, fallback: bool) -> float:
    """一次调用最多可能花多少：输入全按缓存写入价，输出写满 max_tokens；开了换模型重答就再加一段（按未知模型价）。"""
    def hop(p: tuple[float, ...]) -> float:
        return (input_tokens * max(p[0], p[3]) + max_tokens * p[1]) / 1e6
    return hop(_price(model)) + (hop(UNKNOWN_PRICE) if fallback else 0.0)


def _norm(u) -> dict:
    cc = getattr(u, "cache_creation", None)
    w1h = int(getattr(cc, "ephemeral_1h_input_tokens", 0) or 0) if cc is not None else 0
    w_all = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    return {"input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0),
            "cache_write_5m": max(w_all - w1h, 0), "cache_write_1h": w1h}


def anthropic_usage(resp) -> dict:
    """把一次 Claude 响应折算成花费。换模型重答时按 usage.iterations 逐段计价，与顶层 usage 的算法取较大值。"""
    top = _norm(resp.usage)
    iters = getattr(resp.usage, "iterations", None) or []
    by_top = cost_usd(resp.model, **top)
    by_iter = sum(cost_usd(getattr(it, "model", None) or resp.model, **_norm(it)) for it in iters)
    return {"input_tokens": top["input_tokens"], "output_tokens": top["output_tokens"],
            "cache_read_tokens": top["cache_read"], "cache_write_tokens": top["cache_write_5m"] + top["cache_write_1h"],
            "fallback": any(getattr(it, "type", "") == "fallback_message" for it in iters),
            "cost_usd": max(by_top, by_iter)}


def destroy_key() -> list[str]:
    """删除服务器上的 Key：清掉进程环境变量里的 Key，删除 Key 文件。返回删了哪些（不含 Key 本身）。"""
    removed = []
    for var in KEY_ENV_VARS:
        if os.environ.pop(var, None):
            removed.append(f"进程环境变量 {var}")
    f = os.getenv("MINGSHAI_LLM_API_KEY_FILE")
    if f:
        p = Path(f).expanduser()
        if p.exists():
            try:
                with p.open("r+b") as fh:
                    fh.write(b"\0" * p.stat().st_size)
            except OSError:
                pass
            p.unlink(missing_ok=True)
            removed.append(f"Key 文件 {p}")
    return removed


class Ledger:
    """额度账本：``llm_budget.json`` 记累计花费和切断状态，``llm_usage.jsonl`` 逐次记账（只记 token 和金额，不记原话和 Key）。"""

    def __init__(self, data_dir: Path | str | None = None):
        d = Path(data_dir or DATA_DIR)
        self.state_path = d / "llm_budget.json"
        self.log_path = d / "llm_usage.jsonl"
        self._lock = threading.RLock()
        self._held: dict[int, float] = {}
        self._seq = 0
        self._broken: str | None = None

    @staticmethod
    def limit_usd() -> float:
        return float(os.getenv("MINGSHAI_LLM_BUDGET_USD") or 5)

    @staticmethod
    def offset_usd() -> float:
        return float(os.getenv("MINGSHAI_LLM_SPENT_OFFSET_USD") or 0)

    # ── 读写 ──
    def _read(self) -> dict:
        try:
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            st = {}
        except (OSError, ValueError):
            # 账本读不出来就停用（不删 Key），等人工检查
            return {"metered_usd": 0.0, "calls": 0, "floor_usd": 0.0, "disabled": True, "unreadable": True,
                    "reason": "额度账本读不出来，AI 已停用"}
        st.setdefault("metered_usd", 0.0)
        st.setdefault("calls", 0)
        st.setdefault("floor_usd", 0.0)
        st.setdefault("disabled", False)
        return st

    def _write(self, st: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _append(self, row: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── 对外 ──
    def status(self) -> dict:
        with self._lock:
            st = self._read()
            limit, spent = self.limit_usd(), self.offset_usd() + float(st["metered_usd"])
            disabled = bool(st["disabled"]) or self._broken is not None
            return {"limit_usd": round(limit, 2), "spent_usd": round(spent, 2), "remaining_usd": round(max(limit - spent, 0.0), 2),
                    "disabled": disabled, "reason": (self._broken or st.get("reason")) if disabled else None}

    def reserve(self, worst_usd: float) -> int:
        """调用前预留额度，返回预留编号；额度不够就抛 Exhausted（并永久切断）或 Busy。"""
        with self._lock:
            if self._broken:
                raise Exhausted(self._broken)
            st = self._read()
            if st["disabled"]:
                raise Exhausted(st.get("reason") or "AI 额度已用完")
            limit, spent = self.limit_usd(), self.offset_usd() + float(st["metered_usd"])
            if worst_usd > st["floor_usd"]:
                st["floor_usd"] = round(worst_usd, 6)
                self._write(st)
            if spent + worst_usd > limit:
                raise Exhausted(self._cut(st, spent, limit))
            if spent + sum(self._held.values()) + worst_usd > limit:
                raise Busy("AI 额度接近上限，其他调用还没记完账")
            self._seq += 1
            self._held[self._seq] = worst_usd
            return self._seq

    def settle(self, hold: int, cost: float, row: dict) -> None:
        """调用结束后记实际花费，释放预留；剩余额度不够下一次最坏情况调用就立即切断。"""
        with self._lock:
            self._held.pop(hold, None)
            try:
                st = self._read()
                st["metered_usd"] = round(float(st["metered_usd"]) + max(cost, 0.0), 6)
                st["calls"] += 1
                st["updated_at"] = _now()
                self._write(st)
                self._append({"ts": _now(), **row, "cost_usd": round(cost, 6)})
            except OSError as e:
                # 记不了账就不能再花钱：停用到重启为止
                self._broken = f"额度账本写不进去（{type(e).__name__}），AI 已停用"
                return
            limit, spent = self.limit_usd(), self.offset_usd() + st["metered_usd"]
            if not st["disabled"] and limit - spent < st["floor_usd"]:
                self._cut(st, spent, limit)

    def check(self) -> dict:
        """启动时调用：已切断就再确认一遍 Key 不在；剩余额度已经不够一次最坏情况调用就现在切断。"""
        with self._lock:
            st = self._read()
            limit, spent = self.limit_usd(), self.offset_usd() + float(st["metered_usd"])
            if st["disabled"] and not st.get("unreadable"):
                removed = destroy_key()
                if removed:
                    self._append({"ts": _now(), "event": "startup_removed_key", "removed": removed})
            elif not st["disabled"] and (limit - spent <= 0 or limit - spent < st["floor_usd"]):
                self._cut(st, spent, limit)
            return self.status()

    def _cut(self, st: dict, spent: float, limit: float) -> str:
        removed = destroy_key()
        reason = f"AI 额度已用完（累计约 ${spent:.2f}，上限 ${limit:g}），服务器上的 Key 已删除"
        st.update(disabled=True, reason=reason, disabled_at=_now(), removed=removed)
        self._write(st)
        self._append({"ts": _now(), "event": "cut_off", "spent_usd": round(spent, 4), "limit_usd": limit, "removed": removed})
        print(f"[llm-budget] {reason}；删除了：{'、'.join(removed) or '（没有找到 Key）'}", flush=True)
        return reason


LEDGER = Ledger()

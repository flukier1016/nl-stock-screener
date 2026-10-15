"""AI 总额度：预留、记账、到上限永久切断并删除 Key。不访问网络，大模型用 monkeypatch。"""
from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from mingshai import budget, llm, parse_llm


@pytest.fixture()
def keyed(tmp_path, monkeypatch):
    """配好 Claude 和一个临时 Key 文件，返回 Key 文件路径。"""
    key = tmp_path / "secrets" / "llm.key"
    key.parent.mkdir()
    key.write_text("sk-ant-test-key", encoding="utf-8")
    monkeypatch.setenv("MINGSHAI_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MINGSHAI_LLM_API_KEY_FILE", str(key))
    return key


def fake_anthropic(cost: float, calls: list):
    def _fake(c, system, user, schema, max_tokens):
        calls.append(max_tokens)
        return "好的", {"provider": "anthropic", "model": "claude-opus-5-5", "input_tokens": 10, "output_tokens": 20,
                       "cost_usd": cost}
    return _fake


def test_actual_cost_is_recorded_and_cut_off_deletes_key(keyed, monkeypatch):
    monkeypatch.setenv("MINGSHAI_LLM_BUDGET_USD", "1")
    calls: list = []
    monkeypatch.setattr(llm, "_anthropic", fake_anthropic(0.2, calls))
    assert llm.available()
    n_ok = 0
    with pytest.raises(llm.BudgetError):
        for _ in range(20):
            llm.complete_text("系统", "事实")
            n_ok += 1
    st = budget.LEDGER.status()
    # 累计从不超过上限；剩余不够一次最坏情况调用就切断
    assert st["disabled"] and st["spent_usd"] <= 1.0 and n_ok == len(calls) >= 3
    assert not keyed.exists() and not llm.available()
    assert "AI 额度已用完" in llm.unavailable_reason()
    rows = [json.loads(x) for x in budget.LEDGER.log_path.read_text(encoding="utf-8").splitlines()]
    assert sum(r.get("cost_usd", 0) for r in rows) == pytest.approx(0.2 * n_ok)
    assert rows[-1]["event"] == "cut_off" and any("Key 文件" in x for x in rows[-1]["removed"])
    assert all("sk-ant" not in json.dumps(r) for r in rows)  # 账本里不出现 Key


def test_refuses_before_calling_when_worst_case_does_not_fit(keyed, monkeypatch):
    monkeypatch.setenv("MINGSHAI_LLM_BUDGET_USD", "5")
    monkeypatch.setenv("MINGSHAI_LLM_SPENT_OFFSET_USD", "4.9")  # 只剩 0.1，不够一次解析的最坏情况
    calls: list = []
    monkeypatch.setattr(llm, "_anthropic", fake_anthropic(0.01, calls))
    with pytest.raises(llm.BudgetError):
        llm.complete_json("系统", "原话", {"type": "object"})
    assert calls == [] and not keyed.exists()
    # 切断是永久的：换一个进程（新账本对象）读同一个目录，也还是切断状态
    again = budget.Ledger(budget.LEDGER.state_path.parent)
    assert again.status()["disabled"]


def test_timeout_is_charged_at_the_reservation(keyed, monkeypatch):
    def timeout(*a, **k):
        raise llm.LLMError("大模型响应超时", maybe_billed=True)
    monkeypatch.setattr(llm, "_anthropic", timeout)
    with pytest.raises(llm.LLMError):
        llm.complete_text("系统", "事实")
    row = json.loads(budget.LEDGER.log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["outcome"] == "error" and row["cost_usd"] == row["reserved_usd"] > 0


def test_unbilled_errors_cost_nothing_and_release_the_reservation(keyed, monkeypatch):
    def overloaded(*a, **k):
        raise llm.LLMError("大模型接口错误 529：overloaded")
    monkeypatch.setattr(llm, "_anthropic", overloaded)
    with pytest.raises(llm.LLMError):
        llm.complete_text("系统", "事实")
    assert budget.LEDGER.status()["spent_usd"] == 0 and budget.LEDGER._held == {}


def _it(typ, model, i, o, cr=0, cw=0):
    return SimpleNamespace(type=typ, model=model, input_tokens=i, output_tokens=o,
                           cache_read_input_tokens=cr, cache_creation_input_tokens=cw)


def test_fallback_iterations_are_priced_per_model():
    usage = SimpleNamespace(input_tokens=30, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=5000,
                            iterations=[_it("message", "claude-opus-5-5", 30, 40, cw=5000),
                                        _it("fallback_message", "claude-opus-4-8", 30, 500, cw=5000)])
    u = budget.anthropic_usage(SimpleNamespace(model="claude-opus-4-8", usage=usage))
    first = (30 * 4 + 40 * 20 + 5000 * 5) / 1e6           # 被拒那一段按 Opus 5.5 价
    second = (30 * 10 + 500 * 50 + 5000 * 12.5) / 1e6     # 价目表里没有的模型按贵一档记
    assert u["fallback"] and u["cost_usd"] == pytest.approx(first + second)


def test_measured_calls_match_published_prices():
    # 2026-10-15 实测：首次写缓存约 0.034 美元，命中缓存约 0.012 美元
    assert budget.cost_usd("claude-opus-5-5", input_tokens=23, output_tokens=465, cache_write_5m=4980) == pytest.approx(0.034292)
    assert budget.cost_usd("claude-opus-5-5", input_tokens=33, output_tokens=525, cache_read=4980) == pytest.approx(0.011628)


def test_prepare_key_moves_env_key_into_file_then_cut_off_keeps_it_gone(tmp_path, monkeypatch):
    key = tmp_path / "vol" / "llm.key"
    monkeypatch.setenv("MINGSHAI_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MINGSHAI_LLM_API_KEY", "sk-ant-from-env")
    monkeypatch.setenv("MINGSHAI_LLM_API_KEY_FILE", str(key))
    llm.prepare_key()
    assert key.read_text(encoding="utf-8") == "sk-ant-from-env"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert "MINGSHAI_LLM_API_KEY" not in os.environ and llm.config()["key"] == "sk-ant-from-env"
    assert llm.public_info()["key_source"] == "file"
    # 切断后即使环境变量里又出现 Key（例如平台重启时注入），启动时也会清掉，不会重新写文件
    monkeypatch.setenv("MINGSHAI_LLM_BUDGET_USD", "0.01")
    monkeypatch.setattr(llm, "_anthropic", fake_anthropic(0.0, []))
    with pytest.raises(llm.BudgetError):
        llm.complete_text("系统", "事实")
    assert not key.exists()
    monkeypatch.setenv("MINGSHAI_LLM_API_KEY", "sk-ant-from-env")
    st = llm.prepare_key()
    assert st["disabled"] and not key.exists() and "MINGSHAI_LLM_API_KEY" not in os.environ
    assert not llm.available() and llm.public_info()["key_source"] == "none"


def test_parse_falls_back_to_rules_with_budget_reason(keyed, monkeypatch):
    monkeypatch.setenv("MINGSHAI_LLM_BUDGET_USD", "0.01")
    monkeypatch.setattr(llm, "_anthropic", fake_anthropic(0.0, []))
    r = parse_llm.parse("经营改善、估值合理", {})
    assert r.mode == "rules_fallback" and "AI 额度已用完" in r.fallback_reason
    r2 = parse_llm.parse("经营改善、估值合理", {})
    assert r2.mode == "rules" and "AI 额度已用完" in r2.fallback_reason
    info = llm.public_info()
    assert info["available"] is False and info["budget"]["disabled"] and info["budget"]["limit_usd"] == 0.01

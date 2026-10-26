import pytest

from mingshai import budget, llm


@pytest.fixture(autouse=True)
def _isolated_budget(tmp_path, monkeypatch):
    """每个测试用自己的额度账本，不碰 data/ 下的真账本；也清掉 Key 相关环境变量，切断测试不会删到真 Key。"""
    monkeypatch.setattr(budget, "LEDGER", budget.Ledger(tmp_path / "budget"))
    monkeypatch.setattr(llm, "_runtime_health", {})
    for var in ("MINGSHAI_LLM_BUDGET_USD", "MINGSHAI_LLM_SPENT_OFFSET_USD", "MINGSHAI_LLM_API_KEY",
                "MINGSHAI_LLM_API_KEY_FILE", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)

"""Regressions taken from independent review failures and explicit boundary cases."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from eval.run_eval import score
from mingshai import app as appmod, explain, llm, parse_llm
from mingshai.compile import compile_strategy
from mingshai.data import build
from mingshai.data.quality import DataQualityError, listing_dates
from mingshai.dsl import Condition, Segment, Strategy
from mingshai.engine import run
from mingshai.guard import check_strategy
from mingshai.parse_rules import parse
from mingshai.store import load_demo


def test_mixed_listing_date_formats_preserve_known_listings():
    values = pd.Series(["1999-11-10", "20030428", 1631116800000, "2001/08/27", None, "bad"])
    dates = listing_dates(values)
    assert dates.iloc[:4].dt.strftime("%Y-%m-%d").tolist() == ["1999-11-10", "2003-04-28", "2021-09-09", "2001-08-27"]
    assert dates.iloc[4:].isna().all()


def test_failed_critical_field_never_replaces_snapshot(tmp_path, monkeypatch):
    target = tmp_path / "snapshot.duckdb"
    target.write_bytes(b"last-known-good")
    monkeypatch.setattr(build, "SNAPSHOT_PATH", target)
    f = load_demo().frame.reset_index()
    f["listed_days"] = np.nan
    with pytest.raises(DataQualityError, match="listed_days"):
        build._write_snapshot(f, {"mode": "live"})
    assert target.read_bytes() == b"last-known-good"
    assert not target.with_suffix(".prev.duckdb").exists()


def test_partial_universe_unknown_is_missing_not_passed_or_excluded():
    ds = load_demo()
    code = ds.frame.index[0]
    ds.frame.at[code, "listed_days"] = np.nan
    result = run(ds, Strategy())
    row = next(r for r in result["rows"] if r["thscode"] == code)
    assert row["category"] == "missing" and row["universe_missing"]
    assert code not in result["passed"]
    assert "上市日期缺失" in explain.evidence(ds, result, Strategy(), code)["verdict"]


@pytest.mark.parametrize("query,code", [
    ("市盈率低于15倍或者股息率高于5%的股票", "unsupported_or"),
    ("PE低于15或PB低于1", "unsupported_or"),
    ("近20日波动率低于20%", "unsupported_window"),
    ("近60日最大回撤低于10%", "unsupported_window"),
    ("分红稳定，市盈率低于20倍", "dividend_history_unavailable"),
    ("三季报净利润增长超过20%", "unsupported_financial_period"),
    ("不排除银行，股息率高于3%", "complex_negation"),
])
def test_unsupported_semantics_block_execution(query, code):
    ds = load_demo()
    strategy, _, _ = compile_strategy(query, parse(query, ds.industries))
    assert code in {i["code"] for i in check_strategy(strategy)}
    with pytest.raises(Exception) as error:
        appmod.run_cached(ds, strategy)
    assert error.value.status_code == 422


@pytest.mark.parametrize("query", ["不要银行股，股息率高于3%", "剔除银行和券商，PE低于15倍"])
def test_industry_exclusion_never_becomes_inclusion(query):
    ds = load_demo()
    strategy, _, _ = compile_strategy(query, parse(query, ds.industries))
    assert not strategy.universe.industries
    assert ds.industries["银行"] in strategy.universe.excluded_industries
    assert not check_strategy(strategy)
    result = run(ds, strategy)
    assert all(ds.frame.at[c, "industry_code"] not in strategy.universe.excluded_industries for c in result["passed"])


def test_supported_window_is_not_lost():
    q = "近60日波动率低于20%"
    strategy, _, _ = compile_strategy(q, parse(q))
    assert not check_strategy(strategy)
    assert [(c.metric, c.op, c.value) for c in strategy.conditions] == [("vol_60d", "<", 20)]


def test_literal_model_operator_change_falls_back(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: (
        {"segments": [{"text": "市盈率低于15倍", "kind": "explicit", "metric": "pe_ttm", "op": "<=", "value": 15}]}, {}
    ))
    parsed = parse_llm.parse("市盈率低于15倍", {})
    assert parsed.mode == "rules_fallback"
    assert next(s for s in parsed.segments if s.kind == "explicit").op == "<"


def test_model_cannot_add_an_unrequested_numeric_predicate(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: (
        {"segments": [{"text": "经营改善", "kind": "explicit", "metric": "pe_ttm", "op": "<", "value": 15}]}, {}
    ))
    result = parse_llm.parse("经营改善", {})
    assert result.mode == "rules_fallback"
    assert all(s.metric != "pe_ttm" for s in result.segments)


def test_model_fabricated_source_fragment_is_blocking(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: (
        {"segments": [{"text": "用户没有说过的文字", "kind": "concept", "concept_id": "val_fair"}]}, {}
    ))
    result = parse_llm.parse("经营改善", {})
    assert result.blocked
    strategy, _, _ = compile_strategy(result.query, result.segments)
    assert check_strategy(strategy)


def test_strict_eval_rejects_changed_boundary_basis_and_extra_condition():
    case = {"q": "PE低于15倍", "explicit": [["pe_ttm", "<", 15]]}
    exact = Segment(text=case["q"], kind="explicit", metric="pe_ttm", op="<", value=15)
    assert score(case, [exact])["pass"]
    assert not score(case, [exact.model_copy(update={"op": "<="})])["pass"]
    assert not score(case, [exact.model_copy(update={"basis": "industry_median"})])["pass"]
    extra = Segment(text="PB低于1倍", kind="explicit", metric="pb_mrq", op="<", value=1)
    assert not score(case, [exact, extra])["pass"]


def test_auth_failure_changes_availability_without_deleting_key(tmp_path, monkeypatch):
    key = tmp_path / "model.key"
    key.write_text("test-key")
    monkeypatch.setenv("MINGSHAI_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MINGSHAI_LLM_API_KEY_FILE", str(key))
    assert llm.public_info()["health"]["status"] == "unverified"
    llm._record_health(llm.LLMError("auth", status_code=401))
    assert not llm.available()
    assert llm.public_info()["health"]["status"] == "auth_failed"
    assert key.read_text() == "test-key"
    key.write_text("replacement-test-key")
    assert llm.available() and llm.runtime_health()["status"] == "unverified"


def test_model_cannot_swap_numbers_or_inject_display_text():
    ds = load_demo()
    s = Strategy(conditions=[Condition(id="c1", metric="div_yield", op=">=", value=3),
                             Condition(id="c2", metric="mdd_120d", op="<=", value=20)])
    facts = explain.explanation_facts(run(ds, s), s)
    with pytest.raises(ValueError):
        explain.render_fact_selection({"fact_ids": ["condition:c1"], "text": "股息率20%，回撤3%"}, facts)
    with pytest.raises(ValueError):
        explain.render_fact_selection({"fact_ids": ["invented"]}, facts)
    text, chosen = explain.render_fact_selection({"fact_ids": ["condition:c1", "condition:c2"]}, facts)
    assert "≥ 3%" in text and "≤ 20%" in text
    assert chosen[0]["metric"] == "div_yield" and chosen[0]["value"] == 3


def test_all_execution_routes_reject_pending_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod.store, "_demo", load_demo())
    monkeypatch.setattr(appmod.monitors, "DB_PATH", tmp_path / "strategies.sqlite")
    client = TestClient(appmod.app)
    s, _, _ = compile_strategy("PE低于15或者股息率高于5%", parse("PE低于15或者股息率高于5%"))
    bodies = {
        "/api/screen": {"strategy": s.model_dump()},
        "/api/evidence": {"strategy": s.model_dump(), "query": "600000"},
        "/api/compare": {"strategy": s.model_dump(), "thscodes": ["600000.SH", "600002.SH"]},
        "/api/explain": {"strategy": s.model_dump()},
        "/api/strategies": {"strategy": s.model_dump()},
    }
    for path, body in bodies.items():
        response = client.post(path + "?data=demo", json=body, headers={"X-Client-Id": "audit"})
        assert response.status_code == 422, (path, response.text)


@pytest.mark.parametrize("query,excluded", [
    ("非银行股，PE低于15", True),
    ("不要亏损的银行股", False),
    ("剔除ST的银行股", False),
    ("除银行以外，股息率高于3%", True),
])
def test_exclusion_scope_does_not_extend_to_another_clause(query, excluded):
    ds = load_demo()
    strategy, _, _ = compile_strategy(query, parse(query, ds.industries))
    assert not check_strategy(strategy)
    assert (ds.industries["银行"] in strategy.universe.excluded_industries) == excluded
    assert (ds.industries["银行"] in strategy.universe.industries) != excluded


def test_negative_numeric_predicate_requires_explicit_direction():
    q = "不要PE低于15倍的股票"
    s, _, _ = compile_strategy(q, parse(q))
    assert any(i["severity"] == "error" for i in check_strategy(s))


def test_invalid_legacy_monitor_records_failure_instead_of_normal_result(tmp_path, monkeypatch):
    from mingshai import monitors
    monkeypatch.setattr(monitors, "DB_PATH", tmp_path / "monitor.sqlite")
    ds = load_demo()
    legacy = Strategy(query="PE低于15倍或者股息率高于5%")
    baseline = run(ds, Strategy())
    saved = monitors.save_strategy("audit", legacy, baseline, "demo")
    monitor = monitors.create_monitor("audit", saved["id"])
    assert monitors.run_all(ds, appmod.run_cached) == 0
    history = monitors.monitor_runs("audit", monitor["id"])
    assert any(r["trigger"] == "failed" and "未执行" in r["note"] for r in history)
    assert monitors.list_monitors("audit")[0]["last_run"]["trigger"] == "failed"

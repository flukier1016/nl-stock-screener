"""口径字典、守门器、编译、引擎的契约测试。任何一边改了口径没同步，这里就会红。"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from mingshai import engine
from mingshai.compile import compile_strategy
from mingshai.conflicts import check as check_conflicts
from mingshai.dsl import Condition, Segment, Strategy, UniverseSpec
from mingshai.guard import check_segment, check_strategy, check_value
from mingshai.parse_rules import parse
from mingshai.registry import CONCEPTS, METRICS_BY_ID, OPS, TENSIONS, UNIVERSES_BY_ID
from mingshai.store import load_demo


@pytest.fixture(scope="module")
def ds():
    return load_demo()


# ── 口径字典自洽 ──────────────────────────────────────────────────────
def test_every_option_condition_is_legal():
    for c in CONCEPTS:
        if c.universe:
            assert c.universe in UNIVERSES_BY_ID
            continue
        assert sum(o.recommended for o in c.options) == 1, f"{c.id} 必须恰好一个推荐方案"
        for o in c.options:
            for t in o.conds:
                v = list(t.value) if isinstance(t.value, tuple) else t.value
                assert check_value(t.metric, t.op, v, t.basis) is None, (c.id, o.id, t)


def test_tensions_reference_real_concepts():
    ids = {c.id for c in CONCEPTS}
    for a, b, _ in TENSIONS:
        assert a in ids and b in ids


def test_synonyms_are_unique_across_concepts():
    seen = {}
    for c in CONCEPTS:
        for s in c.synonyms:
            assert s not in seen, f"同义词“{s}”同时属于 {seen[s]} 和 {c.id}"
            seen[s] = c.id


def test_metrics_have_source_unit_and_definition():
    for m in METRICS_BY_ID.values():
        assert m.source and m.unit and m.definition and m.period, m.id


# ── 守门器 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("metric,op,value,basis,ok", [
    ("pe_ttm", "<=", 30, "absolute", True),
    ("pe_ttm", "<=", 1.0, "industry_median", True),
    ("pe_ttm", "between", [0, 30], "absolute", True),
    ("pe_ttm", "between", [30, 10], "absolute", False),        # 下限大于上限
    ("pe_ttm", "between", [0, 30], "industry_median", False),  # 相对阈值不支持区间
    ("listed_days", "<=", 1.0, "industry_median", False),      # 该指标不允许相对阈值
    ("market_cap", "<=", 10, "absolute", False),               # 字典里没有的指标
    ("div_yield", ">=", 500, "absolute", False),               # 超出合法范围
    ("pe_ttm", "<>", 3, "absolute", False),
    ("pe_ttm", "<=", float("nan"), "absolute", False),
])
def test_check_value(metric, op, value, basis, ok):
    assert (check_value(metric, op, value, basis) is None) is ok


def test_guard_blocks_unknown_concept_and_converts_size_concept():
    seg, err = check_segment(Segment(text="x", kind="concept", concept_id="moat"), {})
    assert seg is None and "没有概念" in err
    seg, err = check_segment(Segment(text="大盘股", kind="concept", concept_id="cap_large"), {})
    assert err is None and seg.kind == "universe" and seg.universe_id == "hs300"


def test_guard_unknown_industry_becomes_unsupported():
    seg, err = check_segment(Segment(text="元宇宙", kind="industry", industry="元宇宙"), {"银行": "881155.TI"})
    assert err is None and seg.kind == "unsupported"


def test_strategy_errors_block_run():
    s = Strategy(conditions=[Condition(id="c1", metric="pe_ttm", op="<=", value=-5e6)])
    assert any(i["severity"] == "error" for i in check_strategy(s))


# ── 规则解析（主链路示例）───────────────────────────────────────────────
def kinds(q, inds=None):
    return [(s.kind, s.concept_id or s.metric or s.universe_id or s.industry) for s in parse(q, inds or {})]


def test_rules_canonical_query():
    assert kinds("经营改善、估值合理、走势相对稳定") == [
        ("concept", "op_improve"), ("concept", "val_fair"), ("concept", "px_stable")]


def test_rules_longest_synonym_wins():
    # "分红稳定"属于高股息，不能被"稳定"（走势稳定）抢走
    assert kinds("分红稳定") == [("concept", "div_high")]


def test_rules_explicit_numbers_and_ops():
    segs = parse("市盈率低于20倍、ROE不低于15%、股息率3%以上、PE在10到20之间", {})
    got = [(s.metric, s.op, s.value) for s in segs if s.kind == "explicit"]
    assert ("pe_ttm", "<", 20.0) in got
    assert ("roe_ann", ">=", 15.0) in got
    assert ("div_yield", ">=", 3.0) in got
    assert ("pe_ttm", "between", [10.0, 20.0]) in got


@pytest.mark.parametrize("q,why", [
    ("明天会涨停的股票", "预测未来涨跌"),
    ("稳赚不赔的", "收益承诺"),
    ("推荐我买哪只", "直接买卖建议"),
    ("茅台目标价多少", "价格预测或非公开信息"),
])
def test_rules_refuse(q, why):
    assert any(s.kind == "refused" and s.reason == why for s in parse(q, {}))


def test_rules_unknown_is_listed_not_guessed():
    segs = parse("管理层靠谱、有护城河", {})
    assert {s.kind for s in segs} == {"unsupported"}


# ── 编译 ──────────────────────────────────────────────────────────────
def test_compile_choices_and_skip():
    segs = parse("经营改善、估值合理", {})
    s1, _, _ = compile_strategy("q", segs)
    s2, _, _ = compile_strategy("q", segs, {0: "margin_up"})
    s3, _, _ = compile_strategy("q", segs, {0: "__skip__"})
    assert {c.metric for c in s2.conditions} >= {"gm_delta", "roe_delta"}
    assert all(c.concept_id != "op_improve" for c in s3.conditions)
    assert len(s1.conditions) != len(s3.conditions)


def test_compile_flags_conflicting_universes():
    segs = parse("大盘股和小盘股", {})
    s, _, notes = compile_strategy("q", segs)
    assert s.universe.base == "hs300" and notes and notes[0]["severity"] == "conflict"


# ── 引擎 ──────────────────────────────────────────────────────────────
def test_engine_is_deterministic(ds):
    s, _, _ = compile_strategy("q", parse("经营改善、估值合理、走势相对稳定", ds.industries))
    a, b = engine.run(ds, s), engine.run(ds, s)
    assert a["run_id"] == b["run_id"] and a["passed"] == b["passed"]
    s2 = s.model_copy(update={"name": "改了名字"})
    assert engine.run(ds, s2)["run_id"] == a["run_id"]          # 名字不影响结果编号


def test_missing_never_passes(ds):
    s = Strategy(conditions=[Condition(id="c1", metric="pe_ttm", op=">", value=-1e5)])
    res = engine.run(ds, s)
    missing = ds.frame.loc[res["_index"], "pe_ttm"].isna()
    rows = {r["thscode"]: r for r in res["rows"]}
    assert missing.sum() > 0
    for code in missing[missing].index:
        assert rows[code]["category"] == "missing"
    assert res["counts"]["missing"] == int(missing.sum())


def test_categories_partition_universe(ds):
    s, _, _ = compile_strategy("q", parse("高成长、低估值、财务稳健", ds.industries))
    res = engine.run(ds, s)
    assert sum(res["counts"].values()) == res["universe_count"]
    for r in res["rows"]:
        if r["category"] == "passed":
            assert not r["fails"] and not r["missing"]
        if r["category"] == "near_miss":
            assert len(r["fails"]) == 1 and r["worst_gap"] <= s.near_miss_tolerance


def test_funnel_and_removal_impact_are_consistent(ds):
    s, _, _ = compile_strategy("q", parse("经营改善、估值合理、走势相对稳定", ds.industries))
    res = engine.run(ds, s)
    assert res["funnel"][-1]["count"] == res["counts"]["passed"]
    counts = [f["count"] for f in res["funnel"]]
    assert counts == sorted(counts, reverse=True)
    # 去掉第 i 条后入选数 = 原入选数 + removed_adds，用引擎直接验证
    for i, imp in enumerate(res["impact"]):
        s2 = s.model_copy(update={"conditions": [c for j, c in enumerate(s.conditions) if j != i]})
        assert engine.run(ds, s2)["counts"]["passed"] == res["counts"]["passed"] + imp["removed_adds"]


def test_sweep_matches_engine_at_current_threshold(ds):
    s = Strategy(conditions=[Condition(id="c1", metric="pe_ttm", op="between", value=[0, 25]),
                             Condition(id="c2", metric="div_yield", op=">=", value=2)])
    res = engine.run(ds, s)
    sw = res["impact"][1]["sweep"]
    cur = next(p for p in sw["points"] if math.isclose(p["x"], 2))
    assert cur["count"] == res["counts"]["passed"]


def test_industry_median_needs_enough_peers(ds):
    one = ds.frame.index[:1]
    f = ds.frame.copy()
    f.loc[f["industry_code"] == f.loc[one[0], "industry_code"], "pe_ttm"] = float("nan")
    f.loc[one, "pe_ttm"] = 10.0
    from mingshai.store import Dataset
    d2 = Dataset(f, ds.meta, ds.industries)
    s = Strategy(universe=UniverseSpec(min_listed_days=0, exclude_st=False),
                 conditions=[Condition(id="c1", metric="pe_ttm", op="<=", value=1.0, basis="industry_median")])
    res = engine.run(d2, s)
    row = next(r for r in res["rows"] if r["thscode"] == one[0])
    assert row["category"] == "missing"


# ── 冲突 ──────────────────────────────────────────────────────────────
def test_interval_conflict_detected(ds):
    s = Strategy(conditions=[Condition(id="c1", metric="ret_60d", op=">=", value=30),
                             Condition(id="c2", metric="ret_60d", op="<=", value=20)])
    sev = [x["severity"] for x in check_conflicts(ds, s)]
    assert "conflict" in sev
    assert engine.run(ds, s)["counts"]["passed"] == 0


def test_financials_applicability_warning(ds):
    bank = ds.industries["银行"]
    s = Strategy(universe=UniverseSpec(industries=[bank]),
                 conditions=[Condition(id="c1", metric="debt_ratio", op="<=", value=60)])
    assert any(x["severity"] == "applicability" for x in check_conflicts(ds, s))


def test_tension_reports_real_overlap(ds):
    s, _, _ = compile_strategy("q", parse("高成长、低估值", ds.industries))
    t = [x for x in check_conflicts(ds, s) if x["severity"] == "tension"]
    assert t and set(t[0]["detail"]) == {"a", "b", "both"}
    assert t[0]["detail"]["both"] <= min(t[0]["detail"]["a"], t[0]["detail"]["b"])


def test_ops_constant_matches_dsl():
    from typing import get_args

    from mingshai.dsl import Op
    assert set(get_args(Op)) == set(OPS)


def test_demo_frame_has_all_metric_columns(ds):
    missing = [m for m in METRICS_BY_ID if m not in ds.frame.columns]
    assert not missing, missing
    assert isinstance(ds.frame, pd.DataFrame)

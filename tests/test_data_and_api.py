"""数据层（复权、行业层级、扶摇客户端异常）、解释层（数字守门）、接口层与大模型解析的测试。
不访问网络：扶摇用 httpx.MockTransport，大模型用 monkeypatch。"""
from __future__ import annotations

import asyncio
import json

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from mingshai import llm, parse_llm
from mingshai.data.build import _find_url, choose_industry
from mingshai.data.features import adjusted_returns, event_sanity, price_features
from mingshai.data.fuyao import FuyaoClient, FuyaoError
from mingshai.explain import compliance_guard, number_guard


# ── 复权与行情指标 ────────────────────────────────────────────────────
def _daily(close, start="2026-06-01"):
    d = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"thscode": "600000.SH", "date": d, "close_price": close, "turnover": 2e8})


def test_ex_rights_day_return_uses_reference_price():
    daily = _daily([20.0] * 30 + [14.0] * 30)
    ev = pd.DataFrame({"thscode": ["600000.SH"], "ex_date": [daily["date"][30]], "dividend_per_share": [1.0],
                       "per_share_bonus": [0.5], "allotment_ratio": [0.0], "allotment_price": [0.0]})
    d = adjusted_returns(daily, ev)
    assert d.loc[30, "ret"] == pytest.approx(14 / ((20 - 1) / 1.5) - 1)
    assert event_sanity(d)["extreme_share"] == 0.0


def test_event_on_suspension_day_rolls_forward():
    daily = _daily([10.0] * 10 + [9.5] * 10)
    ev = pd.DataFrame({"thscode": ["600000.SH"], "ex_date": [daily["date"][10] - pd.Timedelta(days=1)],
                       "dividend_per_share": [0.5], "per_share_bonus": [0.0], "allotment_ratio": [0.0],
                       "allotment_price": [0.0]})
    d = adjusted_returns(daily, ev)
    assert bool(d.loc[10, "has_event"]) and d.loc[10, "ret"] == pytest.approx(9.5 / 9.5 - 1)


def test_wrong_bonus_unit_would_be_caught():
    # 如果把"每 10 股送 5"误当成每股送 5，除权日会出现 -80% 的假跌幅，体检要能发现
    daily = pd.concat([_daily([20.0] * 5 + [13.3] * 5).assign(thscode=f"{600000 + i}.SH") for i in range(300)])
    ev = pd.DataFrame({"thscode": [f"{600000 + i}.SH" for i in range(300)],
                       "ex_date": [daily["date"].iloc[5]] * 300, "dividend_per_share": 0.0,
                       "per_share_bonus": 0.05, "allotment_ratio": 0.0, "allotment_price": 0.0})
    d = adjusted_returns(daily, ev)
    assert event_sanity(d)["extreme_share"] > 0.5


def test_price_features_windows_and_missing():
    rng = np.random.default_rng(1)
    close = 10 * np.cumprod(1 + rng.normal(0, 0.02, 300))
    d = adjusted_returns(_daily(close, "2025-06-01"), None)
    f = price_features(d, None, d["date"].max()).iloc[0]
    assert 10 < f["vol_60d"] < 60 and 0 <= f["mdd_120d"] < 100
    assert f["ret_250d"] == pytest.approx((close[-1] / close[-251] - 1) * 100, rel=1e-6)
    short = adjusted_returns(_daily(close[:30]), None)
    assert np.isnan(price_features(short, None, short["date"].max()).iloc[0]["vol_60d"])


def test_choose_industry_picks_finer_level():
    cat = pd.DataFrame({"thscode": ["P", "A", "B"], "name": ["一级", "二级A", "二级B"]})
    mem = pd.DataFrame({"index_code": ["P"] * 4 + ["A"] * 2 + ["B"] * 2,
                        "thscode": ["s1", "s2", "s3", "s4", "s1", "s2", "s3", "s4"]})
    df, info = choose_industry(cat, mem)
    assert info["level_chosen"] == 2 and df.loc["s3", "industry_name"] == "二级B" and df.loc["s3", "industry_l1"] == "一级"


def test_find_url_in_unknown_shape():
    assert _find_url({"item": [{"x": 1, "download_url": "https://s3/a.parquet?sig"}]}).startswith("https://s3")
    assert _find_url({"a": None}) is None


# ── 扶摇客户端：业务错误、限流重试、不吞错误 ───────────────────────────────
def _client(handler, tmp_path):
    c = FuyaoClient("k", ledger=tmp_path / "ledger.jsonl", min_interval=0, max_attempts=3)
    c._http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
    return c


def test_client_raises_business_error_without_retry(tmp_path, monkeypatch):
    calls = []

    def h(req):
        calls.append(req)
        return httpx.Response(200, json={"code": 2001, "message": "未认证", "request_id": "r1", "data": None})
    c = _client(h, tmp_path)
    with pytest.raises(FuyaoError) as e:
        asyncio.run(c.get("/api/a-share/prices/snapshot"))
    assert e.value.code == 2001 and len(calls) == 1
    log = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert log[0]["code"] == 2001 and "k" not in json.dumps(log[0]["params"])


def test_client_retries_throttle_then_succeeds(tmp_path, monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: _real_sleep(0))
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"code": 0, "message": "ok", "request_id": "r2", "data": {"item": [1]}})
    c = _client(h, tmp_path)
    env = asyncio.run(c.get("/x"))
    assert env["request_id"] == "r2" and c.stats["throttled"] == 1 and c.stats["retries"] == 1


def test_client_gives_up_after_max_attempts(tmp_path, monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: _real_sleep(0))
    c = _client(lambda req: httpx.Response(200, json={"code": 5003, "message": "上游不可用", "data": None}), tmp_path)
    with pytest.raises(FuyaoError) as e:
        asyncio.run(c.get("/x"))
    assert e.value.code == 5003 and c.stats["requests"] == 3


# ── 数字守门与合规检查 ────────────────────────────────────────────────
def test_number_guard():
    assert number_guard("股票池 4,812 只，入选 42 只，最严的是第 3 条", {4812, 42, 3}) == []
    assert number_guard("预计年化收益 18%", {42}) == ["18"]
    assert number_guard("占比 12.5%", {0.125}) == []


def test_compliance_guard():
    assert compliance_guard("这几只明天会涨停，建议买入")
    assert not compliance_guard("满足全部条件的有 42 只，最卡人的是估值条件")


# ── 接口层 ────────────────────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGSHAI_AUTO_BUILD", "0")
    from mingshai import app as appmod
    monkeypatch.setattr(appmod.monitors, "DB_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(appmod.monitors, "DATA_DIR", tmp_path)
    monkeypatch.setattr(llm, "available", lambda: False)
    return TestClient(appmod.app)


def test_live_without_snapshot_refuses_to_answer(client, monkeypatch):
    from mingshai import app as appmod
    monkeypatch.setattr(appmod.store, "_live", None)
    r = client.post("/api/parse", json={"query": "高股息"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "DATA_UNAVAILABLE"


def test_main_chain_on_demo(client):
    H = {"X-Client-Id": "t"}
    p = client.post("/api/parse?data=demo", json={"query": "经营改善、估值合理、走势相对稳定"}).json()
    assert p["parse"]["mode"] == "rules" and len(p["strategy"]["conditions"]) == 7
    s = p["strategy"]
    r = client.post("/api/screen?data=demo", json={"strategy": s}).json()
    assert sum(r["counts"].values()) == r["universe_count"] and r["run_id"]
    code = r["rows"][0]["c"]
    e = client.post("/api/evidence?data=demo", json={"strategy": s, "query": code}).json()
    assert e["items"] and all(it["source"]["provider"] for it in e["items"])
    saved = client.post("/api/strategies?data=demo", json={"strategy": s, "name": "t"}, headers=H).json()
    mon = client.post("/api/monitors", json={"strategy_id": saved["id"]}, headers=H).json()
    run = client.post(f"/api/monitors/{mon['id']}/run?data=demo", json={}, headers=H).json()
    assert "没有新数据" in run["note"]


def test_screen_rejects_illegal_strategy(client):
    bad = {"conditions": [{"id": "c1", "metric": "pe_ttm", "op": "<=", "value": -5e6}]}
    r = client.post("/api/screen?data=demo", json={"strategy": bad})
    assert r.status_code == 422


def test_only_refused_query(client):
    r = client.post("/api/parse?data=demo", json={"query": "明天会涨停的股票"}).json()
    assert r["only_refused"] and r["refuse_hint"]


def test_evidence_out_of_universe_explains_why(client):
    p = client.post("/api/parse?data=demo", json={"query": "高股息的大盘股"}).json()
    s = p["strategy"]
    r = client.post("/api/screen?data=demo", json={"strategy": s}).json()
    from mingshai.store import load_demo
    ds = load_demo()
    outside = ds.frame.index[~ds.frame["in_hs300"]][0]
    e = client.post("/api/evidence?data=demo", json={"strategy": s, "query": outside}).json()
    assert e["category"] == "out_of_universe" and "沪深 300" in e["verdict"]
    assert r["universe_count"] < len(ds.frame)


# ── 大模型解析：越界输出被拦下、坏 JSON 自动兜底 ─────────────────────────
def test_llm_out_of_catalog_is_blocked(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    fake = {"paraphrase": "找便宜又稳的", "clarify": [], "segments": [
        {"text": "估值合理", "kind": "concept", "concept_id": "val_fair", "option_id": "abs_pe", "metric": None, "op": None,
         "value": None, "value_hi": None, "basis": "absolute", "universe_id": None, "industry": None, "reason": "用户说的是绝对估值"},
        {"text": "护城河深", "kind": "concept", "concept_id": "moat", "option_id": None, "metric": None, "op": None,
         "value": None, "value_hi": None, "basis": "absolute", "universe_id": None, "industry": None, "reason": ""},
        {"text": "PE低于-99999999", "kind": "explicit", "concept_id": None, "option_id": None, "metric": "pe_ttm", "op": "<=",
         "value": -99999999, "value_hi": None, "basis": "absolute", "universe_id": None, "industry": None, "reason": ""},
    ]}
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (fake, {"model": "fake", "latency_ms": 5}))
    r = parse_llm.parse("估值合理、护城河深、PE低于-99999999", {})
    assert r.mode == "llm" and [s.concept_id for s in r.segments] == ["val_fair"]
    assert r.segments[0].option_id == "abs_pe"
    assert len(r.blocked) == 2 and r.rules_shadow is not None


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)

    def boom(*a, **k):
        raise llm.LLMError("大模型返回的不是合法 JSON")
    monkeypatch.setattr(llm, "complete_json", boom)
    r = parse_llm.parse("经营改善、估值合理", {})
    assert r.mode == "rules_fallback" and "规则解析" in r.fallback_reason and len(r.segments) == 2


# ── 增速由利润表原始金额自算（扶摇财务指标的同比字段实测为空）──────────────
def test_growth_from_income_statement_amounts():
    from mingshai.data.fixture import derive
    df = pd.DataFrame({
        "rev_h1_26": [110.0, 50.0], "rev_h1_25": [100.0, 0.0], "rev_h1_24": [80.0, 10.0],
        "rev_q1_26": [50.0, 20.0], "rev_q1_25": [48.0, 0.0],
        "np_h1_26": [5.0, 1.0], "np_h1_25": [-10.0, 0.0], "np_h1_24": [-20.0, 1.0],
        "gross_margin": [30.0, np.nan], "gm_ly": [28.0, np.nan], "roe_raw": [6.0, 1.0], "roe_ly": [5.0, 1.0],
    })
    derive(df)
    a = df.iloc[0]
    assert a["rev_yoy"] == pytest.approx(10.0)
    assert a["rev_q1_yoy"] == pytest.approx((50 / 48 - 1) * 100)
    assert a["rev_q2_yoy"] == pytest.approx((60 / 52 - 1) * 100)
    assert a["rev_yoy_accel"] == pytest.approx((60 / 52 - 50 / 48) * 100)
    assert a["rev_yoy_vs_ly"] == pytest.approx(10.0 - 25.0)
    assert a["np_yoy"] == pytest.approx(150.0)            # 由亏转盈：(5 − (−10)) ÷ |−10|
    assert a["np_yoy_ly"] == pytest.approx(50.0)          # 亏损收窄：(−10 − (−20)) ÷ |−20|
    assert a["roe_ann"] == pytest.approx(12.0) and a["gm_delta"] == pytest.approx(2.0)
    b = df.iloc[1]
    assert np.isnan(b["rev_yoy"]) and np.isnan(b["rev_yoy_accel"]) and np.isnan(b["np_yoy"])  # 分母为 0 记缺失

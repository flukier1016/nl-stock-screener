"""冲突与张力识别：在运行前告诉用户"这组条件哪里会打架"。

四类提示，严重程度从高到低：

* ``conflict``      —— 逻辑上不可能同时满足（同一指标的区间没有交集；两个股票池互斥）；
* ``applicability`` —— 口径不适用（用负债率、毛利率去筛银行券商保险）；
* ``tension``       —— 不矛盾但互相牵制，用真实数据算出"各自满足几只、同时满足几只"；
* ``coverage``      —— 某个条件在当前股票池里缺数据的比例过高，结果会偏少。

所有数字都来自当前快照的确定性计算，不让大模型估计。
"""
from __future__ import annotations

import math

import pandas as pd

from .compile import describe
from .dsl import Condition, Strategy
from .engine import evaluate, universe_index
from .registry import (
    CONCEPTS_BY_ID,
    FINANCIAL_INDUSTRY_KEYWORDS,
    METRICS_BY_ID,
    NOT_FOR_FINANCIALS,
    TENSIONS,
)
from .store import Dataset

COVERAGE_WARN = 0.80


def _interval(c: Condition) -> tuple[float, float, bool, bool]:
    """绝对阈值条件 → (下界, 上界, 下界含等号, 上界含等号)。"""
    if c.op == "between":
        return float(c.value[0]), float(c.value[1]), True, True
    v = float(c.value)
    return {
        ">=": (v, math.inf, True, False), ">": (v, math.inf, False, False),
        "<=": (-math.inf, v, False, True), "<": (-math.inf, v, False, False),
    }[c.op]


def check(ds: Dataset, s: Strategy) -> list[dict]:
    out: list[dict] = []
    idx, _ = universe_index(ds, s)

    # 1. 同一指标的绝对区间无交集
    by_metric: dict[str, list[Condition]] = {}
    for c in s.conditions:
        if c.basis == "absolute":
            by_metric.setdefault(c.metric, []).append(c)
    for metric, cs in by_metric.items():
        if len(cs) < 2:
            continue
        lo, hi, lo_inc, hi_inc = -math.inf, math.inf, False, False
        for c in cs:
            a, b, ai, bi = _interval(c)
            if a > lo or (a == lo and not ai):
                lo, lo_inc = a, ai
            if b < hi or (b == hi and not bi):
                hi, hi_inc = b, bi
        if lo > hi or (lo == hi and not (lo_inc and hi_inc)):
            out.append({
                "severity": "conflict", "condition_ids": [c.id for c in cs],
                "message": f"{METRICS_BY_ID[metric].name} 的几条要求没有交集："
                           + "；".join(describe(c.metric, c.op, c.value) for c in cs)
                           + "。同时满足的股票必然是 0 只，请删掉或改一条。",
            })

    # 2. 金融行业口径不适用
    names = ds.frame.loc[idx, "industry_name"].dropna()
    fin_share = names.str.contains("|".join(FINANCIAL_INDUSTRY_KEYWORDS)).mean() if len(names) else 0.0
    bad = [c for c in s.conditions if c.metric in NOT_FOR_FINANCIALS]
    if bad and fin_share >= 0.5:
        out.append({
            "severity": "applicability", "condition_ids": [c.id for c in bad],
            "message": "当前股票池以银行、保险、券商为主，资产负债率、流动比率、毛利率对它们不适用"
                       "（银行负债率普遍在 90% 左右，也没有毛利率）。建议改用市净率、股息率、ROE。",
        })

    # 3. 概念之间的张力：用真实数据算重叠
    evals = {c.id: evaluate(ds, c, idx) for c in s.conditions}
    concepts = {}
    for c in s.conditions:
        if c.concept_id:
            concepts.setdefault(c.concept_id, []).append(c.id)
    for a, b, msg in TENSIONS:
        if a in concepts and b in concepts:
            pa = _all_pass(evals, concepts[a], idx)
            pb = _all_pass(evals, concepts[b], idx)
            out.append({
                "severity": "tension", "condition_ids": concepts[a] + concepts[b],
                "message": f"{msg} 当前股票池里满足“{CONCEPTS_BY_ID[a].label}”的 {int(pa.sum())} 只，"
                           f"满足“{CONCEPTS_BY_ID[b].label}”的 {int(pb.sum())} 只，两者同时满足的 {int((pa & pb).sum())} 只。",
                "detail": {"a": int(pa.sum()), "b": int(pb.sum()), "both": int((pa & pb).sum())},
            })

    # 4. 覆盖率
    for c in s.conditions:
        e = evals[c.id]
        cov = float((e.status != "missing").mean()) if len(idx) else 0.0
        if len(idx) and cov < COVERAGE_WARN:
            out.append({
                "severity": "coverage", "condition_ids": [c.id],
                "message": f"“{describe(c.metric, c.op, c.value, c.basis)}”在当前股票池有 {1 - cov:.0%} 的公司缺数据"
                           f"（{int((e.status == 'missing').sum())}/{len(idx)}），这些公司会进入“数据缺失”组，不算通过。",
                "detail": {"coverage": round(cov, 4)},
            })
    return out


def _all_pass(evals: dict, ids: list[str], idx: pd.Index) -> pd.Series:
    m = pd.Series(True, index=idx)
    for i in ids:
        m &= evals[i].status == "pass"
    return m


def relax_suggestions(result: dict, s: Strategy, top: int = 2) -> list[dict]:
    """结果太少时：哪一条最卡人、放到多少能多出几只（来自敏感度扫描，不是猜的）。"""
    sugg = []
    for imp in sorted(result["impact"], key=lambda x: -x["removed_adds"]):
        if imp["removed_adds"] <= 0:
            continue
        c = next(c for c in s.conditions if c.id == imp["condition_id"])
        sw = imp.get("sweep") or {}
        best = None
        cur_n = result["counts"]["passed"]
        for p in sw.get("points", []):
            if p["count"] > cur_n and (best is None or abs(p["x"] - sw["current"]) < abs(best["x"] - sw["current"])):
                best = p
        sugg.append({
            "condition_id": c.id, "label": describe(c.metric, c.op, c.value, c.basis),
            "removed_adds": imp["removed_adds"],
            "nearest_relax": best, "sweep_kind": sw.get("kind"),
        })
        if len(sugg) >= top:
            break
    return sugg

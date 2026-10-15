"""确定性筛选引擎：只做比较和计数，不调用大模型，不访问网络。

对每只股票、每个条件给出三态结果：

* ``pass``    —— 有数据，满足条件；
* ``fail``    —— 有数据，不满足；同时记录差多少（``gap``，按阈值归一化）；
* ``missing`` —— 数据缺失，或相对阈值的可比样本不足。**缺失不算通过**，单独成组。

在此之上产出：分组（入选 / 差一点 / 被排除 / 数据缺失）、逐条件漏斗、每个条件的
"去掉它会多出几只"、阈值扫描曲线。同一策略 + 同一快照 → 同一个 ``run_id``。
"""
from __future__ import annotations

import operator
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .compile import describe
from .dsl import Condition, Strategy
from .registry import METRICS_BY_ID, MIN_PEERS, UNIVERSES_BY_ID
from .store import Dataset

_CMP = {"<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt}
POSITIVE_ONLY = {"pe_ttm", "pb_mrq", "ps_ttm", "pcf_ttm"}   # 估值类的中位数只统计正值
SWEEP_POINTS = 21


@dataclass
class CondEval:
    cond: Condition
    value: pd.Series         # 指标值
    threshold: pd.Series     # 每只股票面对的阈值（between 为下限，hi 另存）
    hi: pd.Series | None
    status: pd.Series        # "pass" / "fail" / "missing"
    gap: pd.Series           # fail 时的归一化差距；其他为 NaN
    basis_value: pd.Series   # 相对阈值时：中位数本身
    peers: pd.Series         # 相对阈值时：可比样本数
    missing_reason: pd.Series


def universe_index(ds: Dataset, s: Strategy) -> tuple[pd.Index, list[dict]]:
    f = ds.frame
    steps = [{"step": "全部 A 股", "count": int(len(f))}]
    mask = pd.Series(True, index=f.index)
    u = UNIVERSES_BY_ID[s.universe.base]
    if u.index_thscode:
        col = {"hs300": "in_hs300", "zz500": "in_zz500", "zz1000": "in_zz1000"}[u.id]
        mask &= f[col].fillna(False).astype(bool)
        steps.append({"step": u.label + " 成分股", "count": int(mask.sum())})
    if s.universe.industries:
        mask &= f["industry_code"].isin(s.universe.industries)
        names = "、".join(sorted(set(f.loc[f["industry_code"].isin(s.universe.industries), "industry_name"])))
        steps.append({"step": f"行业：{names or '（无匹配）'}", "count": int(mask.sum())})
    if s.universe.exclude_st:
        mask &= ~f["is_st"].fillna(False).astype(bool)
        steps.append({"step": "剔除 ST / *ST", "count": int(mask.sum())})
    if s.universe.min_listed_days > 0:
        mask &= f["listed_days"].fillna(0) >= s.universe.min_listed_days
        steps.append({"step": f"剔除上市不满 {s.universe.min_listed_days} 天", "count": int(mask.sum())})
    if s.universe.exclude_bj:
        mask &= f["exchange"] != "BJ"
        steps.append({"step": "剔除北交所", "count": int(mask.sum())})
    return f.index[mask], steps


def _peer_pool(ds: Dataset, metric: str) -> pd.Series:
    """相对阈值的参照池：全市场非 ST 公司的有效值（估值类只取正值）。不随股票池变化。"""
    f = ds.frame
    v = f[metric].where(~f["is_st"].fillna(False).astype(bool))
    if metric in POSITIVE_ONLY:
        v = v.where(v > 0)
    return v


def _relative_basis(ds: Dataset, c: Condition, idx: pd.Index) -> tuple[pd.Series, pd.Series]:
    pool = _peer_pool(ds, c.metric)
    if c.basis == "market_median":
        n = int(pool.notna().sum())
        med = float(pool.median()) if n >= MIN_PEERS else np.nan
        return pd.Series(med, index=idx), pd.Series(n, index=idx)
    ind = ds.frame["industry_code"]
    grp = pool.groupby(ind)
    med = grp.median()
    cnt = grp.count()
    m = ind.loc[idx].map(med)
    n = ind.loc[idx].map(cnt).fillna(0).astype(int)
    m = m.where(n >= MIN_PEERS)
    return m, n


def evaluate(ds: Dataset, c: Condition, idx: pd.Index) -> CondEval:
    v = ds.frame.loc[idx, c.metric].astype(float)
    hi = None
    basis_value = pd.Series(np.nan, index=idx)
    peers = pd.Series(np.nan, index=idx)
    reason = pd.Series("", index=idx, dtype=object)
    if c.basis == "absolute":
        if c.op == "between":
            thr = pd.Series(float(c.value[0]), index=idx)
            hi = pd.Series(float(c.value[1]), index=idx)
        else:
            thr = pd.Series(float(c.value), index=idx)
    else:
        basis_value, peers = _relative_basis(ds, c, idx)
        thr = basis_value * float(c.value)
    missing = v.isna() | thr.isna()
    reason[v.isna()] = "该指标数据缺失"
    reason[v.notna() & thr.isna()] = f"可比公司不足 {MIN_PEERS} 家，无法计算中位数"
    if c.op == "between":
        ok = (v >= thr) & (v <= hi)
    else:
        ok = _CMP[c.op](v, thr)
    status = pd.Series(np.where(missing, "missing", np.where(ok, "pass", "fail")), index=idx)
    gap = _gap(c, v, thr, hi, ds)
    gap[status != "fail"] = np.nan
    return CondEval(c, v, thr, hi, status, gap, basis_value, peers, reason)


def _gap(c: Condition, v: pd.Series, thr: pd.Series, hi: pd.Series | None, ds: Dataset) -> pd.Series:
    """不满足时差多少：差额 ÷ 尺度。尺度=|阈值|；阈值为 0 时用该指标全市场四分位距的 1/4。"""
    col = ds.frame[c.metric]
    iqr = float(col.quantile(0.75) - col.quantile(0.25)) if col.notna().sum() > 8 else 1.0
    fallback = max(iqr / 4, 1e-9)
    if c.op in ("<=", "<"):
        short = v - thr
        scale = thr.abs().where(thr.abs() > 1e-9, fallback)
    elif c.op in (">=", ">"):
        short = thr - v
        scale = thr.abs().where(thr.abs() > 1e-9, fallback)
    else:
        below, above = thr - v, v - hi
        short = pd.concat([below, above], axis=1).max(axis=1)
        width = (hi - thr).abs()
        scale = width.where(width > 1e-9, fallback)
    return (short / scale).clip(lower=0)


def run(ds: Dataset, s: Strategy) -> dict:
    idx, uni_steps = universe_index(ds, s)
    evals = [evaluate(ds, c, idx) for c in s.conditions]
    n = len(idx)
    if evals:
        st = pd.DataFrame({e.cond.id: e.status for e in evals}, index=idx)
        n_fail = (st == "fail").sum(axis=1)
        n_miss = (st == "missing").sum(axis=1)
        gaps = pd.DataFrame({e.cond.id: e.gap for e in evals}, index=idx)
        worst_gap = gaps.max(axis=1)
    else:
        st = pd.DataFrame(index=idx)
        n_fail = n_miss = pd.Series(0, index=idx)
        worst_gap = pd.Series(np.nan, index=idx)
    tol = s.near_miss_tolerance
    category = pd.Series("excluded", index=idx, dtype=object)
    category[(n_fail == 0) & (n_miss == 0)] = "passed"
    category[(n_fail == 0) & (n_miss > 0)] = "missing"
    category[(n_fail == 1) & (n_miss == 0) & (worst_gap <= tol)] = "near_miss"

    funnel, alive = [], pd.Series(True, index=idx)
    for e in evals:
        alive &= e.status == "pass"
        funnel.append({"condition_id": e.cond.id, "label": describe(e.cond.metric, e.cond.op, e.cond.value, e.cond.basis),
                       "count": int(alive.sum()),
                       "fail": int((e.status == "fail").sum()), "missing": int((e.status == "missing").sum()),
                       "pass": int((e.status == "pass").sum())})

    passed_idx = idx[category == "passed"]
    impact = []
    for e in evals:
        others = pd.Series(True, index=idx)
        for o in evals:
            if o is not e:
                others &= o.status == "pass"
        added = idx[others & (e.status != "pass")]
        impact.append({
            "condition_id": e.cond.id, "removed_adds": int(len(added)),
            "added_examples": _names(ds, added[:8]),
            "sweep": _sweep(ds, e, others),
            "coverage": round(float((e.status != "missing").mean()), 4) if n else 0.0,
        })

    # 按位置取 numpy 数组拼行，避免逐行 .loc（全市场 5,000+ 只时快一个数量级）
    names = ds.frame.loc[idx, "name"].to_numpy()
    inds = ds.frame.loc[idx, "industry_name"].to_numpy()
    cats = category.to_numpy()
    wg = worst_gap.to_numpy(dtype=float)
    cols = [(e.cond.id, e.value.to_numpy(dtype=float), e.threshold.to_numpy(dtype=float),
             None if e.hi is None else e.hi.to_numpy(dtype=float), e.status.to_numpy(),
             e.gap.to_numpy(dtype=float)) for e in evals]
    rows = []
    for k, code in enumerate(idx):
        conds = {cid: {"value": _f(v[k]), "threshold": _f(t[k]), "hi": None if h is None else _f(h[k]),
                       "status": st_[k], "gap": _f(g[k])} for cid, v, t, h, st_, g in cols}
        rows.append({
            "thscode": code, "name": names[k], "industry": inds[k] if isinstance(inds[k], str) else None,
            "category": cats[k], "conds": conds,
            "fails": [cid for cid, *_, st_, _g in cols if st_[k] == "fail"],
            "missing": [cid for cid, *_, st_, _g in cols if st_[k] == "missing"],
            "worst_gap": _f(wg[k]),
        })
    counts = {k: int((category == k).sum()) for k in ("passed", "near_miss", "excluded", "missing")}
    fail_dist = {e.cond.id: int((e.status == "fail").sum()) for e in evals}
    return {
        "run_id": s.fingerprint(ds.snapshot_id), "snapshot_id": ds.snapshot_id, "data_mode": ds.mode,
        "universe_steps": uni_steps, "universe_count": n, "counts": counts,
        "funnel": funnel, "impact": impact, "fail_distribution": fail_dist,
        "rows": rows, "passed": [r["thscode"] for r in rows if r["category"] == "passed"],
        "evals": evals, "_index": idx, "passed_index": passed_idx,
    }


def _sweep(ds: Dataset, e: CondEval, others: pd.Series) -> dict | None:
    """把这个条件的阈值换成一串候选值，其他条件不变，各能筛出几只。"""
    c = e.cond
    v = e.value
    if c.basis != "absolute":
        grid = np.round(np.linspace(0.5, 1.5, 11), 2)
        pts = []
        for g in grid:
            thr = e.basis_value * g
            ok = _CMP.get(c.op, operator.le)(v, thr) & v.notna() & thr.notna()
            pts.append({"x": float(g), "count": int((others & ok).sum())})
        return {"kind": "multiplier", "current": float(c.value), "points": pts,
                "x_label": "相对中位数的倍数"}
    valid = v.dropna()
    if len(valid) < 5:
        return None
    lo_q, hi_q = valid.quantile(0.03), valid.quantile(0.97)
    cur = float(c.value[1] if c.op == "between" else c.value)
    grid = np.unique(np.round(np.r_[np.linspace(lo_q, hi_q, SWEEP_POINTS), cur], 4))
    pts = []
    for g in grid:
        if c.op == "between":
            ok = (v >= float(c.value[0])) & (v <= g)
        else:
            ok = _CMP[c.op](v, g)
        pts.append({"x": float(g), "count": int((others & ok.fillna(False)).sum())})
    m = METRICS_BY_ID[c.metric]
    return {"kind": "absolute", "current": cur, "points": pts,
            "x_label": f"{m.name}{'上限' if c.op == 'between' else ''}（{m.unit}）"}


def _names(ds: Dataset, idx: pd.Index) -> list[dict]:
    return [{"thscode": c, "name": ds.frame.at[c, "name"]} for c in idx]


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else round(x, 4)

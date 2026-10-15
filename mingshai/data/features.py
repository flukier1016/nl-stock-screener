"""由日 K 和除权除息事件计算行情类指标。纯函数，单元测试直接喂小表。

复权方法（与扶摇文档"原始数据为主、客户端自行推导"的约定一致）：
除权除息日 t 的参考价 = (前收 − 每股派息 + 配股比例 × 配股价) ÷ (1 + 每股送转 + 配股比例)，
当日收益率 = 收盘价 ÷ 参考价 − 1；其余日期 = 收盘价 ÷ 前收 − 1。连乘得到复权净值。
事件落在停牌日时，顺延到该股之后的第一个交易日。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VOL_WINDOW, VOL_MIN_OBS = 60, 40
MDD_WINDOW = 120
RET_WINDOWS = (20, 60, 250)
AMT_WINDOW = 20


def adjusted_returns(daily: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """daily: thscode, date, close_price, turnover；events: thscode, ex_date, dividend_per_share,
    per_share_bonus, allotment_ratio, allotment_price。返回 daily + prev_close, ref_price, ret。"""
    d = daily.sort_values(["thscode", "date"]).reset_index(drop=True).copy()
    d["prev_close"] = d.groupby("thscode")["close_price"].shift(1)
    d["ev_div"] = 0.0
    d["ev_bonus"] = 0.0
    d["ev_allot"] = 0.0
    d["ev_allot_px"] = 0.0
    d["has_event"] = False
    if events is not None and len(events):
        ev = events.copy()
        for col in ("dividend_per_share", "per_share_bonus", "allotment_ratio", "allotment_price"):
            ev[col] = pd.to_numeric(ev[col], errors="coerce").fillna(0.0)
        # 每个事件对齐到该股 ex_date 当天或之后的第一个交易日
        d_dates = d[["thscode", "date"]].reset_index()
        ev = ev.sort_values("ex_date")
        aligned = pd.merge_asof(
            ev.rename(columns={"ex_date": "date"}).sort_values("date"),
            d_dates.sort_values("date"), on="date", by="thscode", direction="forward",
        ).dropna(subset=["index"])
        agg = aligned.groupby("index").agg(
            ev_div=("dividend_per_share", "sum"), ev_bonus=("per_share_bonus", "sum"),
            ev_allot=("allotment_ratio", "sum"), ev_allot_px=("allotment_price", "max"))
        idx = agg.index.astype(int)
        for col in ("ev_div", "ev_bonus", "ev_allot", "ev_allot_px"):
            d.loc[idx, col] = agg[col].to_numpy()
        d.loc[idx, "has_event"] = True
    ref = (d["prev_close"] - d["ev_div"] + d["ev_allot"] * d["ev_allot_px"]) / (1 + d["ev_bonus"] + d["ev_allot"])
    d["ref_price"] = np.where(d["has_event"], ref, d["prev_close"])
    d["ret"] = d["close_price"] / d["ref_price"] - 1
    d.loc[d["prev_close"].isna() | (d["ref_price"] <= 0), "ret"] = np.nan
    return d


def price_features(d: pd.DataFrame, events: pd.DataFrame | None, as_of: pd.Timestamp) -> pd.DataFrame:
    """d 为 adjusted_returns 的输出。返回每只股票一行：vol_60d、mdd_120d、ret_*、amt_20d、div_yield、
    last_close、price_date、n_obs。"""
    out = []
    for code, g in d.groupby("thscode", sort=False):
        g = g.sort_values("date")
        r = g["ret"].to_numpy()
        nav = np.nancumprod(np.where(np.isnan(r), 0.0, r) + 1.0)
        row = {"thscode": code, "last_close": float(g["close_price"].iloc[-1]),
               "price_date": g["date"].iloc[-1].strftime("%Y-%m-%d"), "n_obs": int(len(g))}
        tail = r[-VOL_WINDOW:]
        valid = tail[~np.isnan(tail)]
        row["vol_60d"] = float(np.std(valid, ddof=1) * np.sqrt(252) * 100) if len(valid) >= VOL_MIN_OBS else np.nan
        if len(nav) >= 20:
            w = nav[-MDD_WINDOW:]
            peak = np.maximum.accumulate(w)
            row["mdd_120d"] = float(np.max(1 - w / peak) * 100)
        else:
            row["mdd_120d"] = np.nan
        for n in RET_WINDOWS:
            row[f"ret_{n}d"] = float((nav[-1] / nav[-1 - n] - 1) * 100) if len(nav) > n else np.nan
        amt = g["turnover"].to_numpy()[-AMT_WINDOW:]
        row["amt_20d"] = float(np.nanmean(amt) / 1e8) if len(amt) >= 10 else np.nan
        out.append(row)
    f = pd.DataFrame(out).set_index("thscode")
    f["div_sum_12m"] = 0.0
    if events is not None and len(events):
        ev = events[(events["ex_date"] > as_of - pd.Timedelta(days=365)) & (events["ex_date"] <= as_of)]
        s = ev.groupby("thscode")["dividend_per_share"].sum()
        f.loc[f.index.intersection(s.index), "div_sum_12m"] = s
    f["div_yield"] = np.where(f["last_close"] > 0, f["div_sum_12m"] / f["last_close"] * 100, np.nan)
    return f


def event_sanity(d: pd.DataFrame) -> dict:
    """除权日收益率的体检：如果送转比例的单位理解错了（每 10 股 vs 每股），
    除权日会出现大量 ±50% 以上的假涨跌。返回统计，构建时超标直接报错。"""
    ev = d[d["has_event"] & d["ret"].notna()]
    if not len(ev):
        return {"events": 0, "extreme_share": 0.0}
    extreme = float((ev["ret"].abs() > 0.25).mean())
    return {"events": int(len(ev)), "extreme_share": round(extreme, 4),
            "median_abs_ret": round(float(ev["ret"].abs().median()), 4)}

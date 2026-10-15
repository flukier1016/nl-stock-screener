"""演示数据（合成）：只用于单元测试和没有 Key 时的离线体验。

所有股票名都带"演示"字样，数据快照 mode="demo"，页面会挂 DEMO 横幅，
接口的每个响应也带 ``data_mode``。真实筛选只用扶摇快照（见 :mod:`mingshai.data.build`）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..registry import REPORT_LATEST, REPORTS

INDUSTRIES = [
    ("881155.TI", "银行", 0.9), ("881157.TI", "证券", 0.7), ("881156.TI", "保险", 0.85),
    ("881273.TI", "白酒", 0.25), ("881121.TI", "半导体", 0.3), ("881145.TI", "电力", 0.6),
    ("881140.TI", "化学制药", 0.35), ("881125.TI", "汽车整车", 0.6), ("881278.TI", "光伏设备", 0.55),
    ("881105.TI", "煤炭开采加工", 0.4), ("881131.TI", "白色家电", 0.6), ("881130.TI", "食品加工制造", 0.4),
    ("881109.TI", "化学制品", 0.45), ("881112.TI", "钢铁", 0.55), ("881153.TI", "建筑装饰", 0.7),
    ("881164.TI", "文化传媒", 0.35), ("881272.TI", "计算机设备", 0.4), ("881162.TI", "通信设备", 0.45),
    ("881166.TI", "军工装备", 0.45), ("881101.TI", "种植业与林业", 0.45),
]
FINANCIALS = {"银行", "证券", "保险"}


def build_fixture(n: int = 800, seed: int = 20261015) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        code, ind, lev = INDUSTRIES[i % len(INDUSTRIES)]
        ex = "BJ" if i % 17 == 5 else ("SH" if i % 2 == 0 else "SZ")
        ticker = f"{600000 + i:06d}" if ex == "SH" else (f"{1 + i:06d}" if ex == "SZ" else f"{830000 + i:06d}")
        is_st = i % 53 == 7
        listed_days = int(rng.integers(100, 9000))
        fin = ind in FINANCIALS
        rev = rng.normal(8, 18)
        rev_ly = rev - rng.normal(2, 12)
        rev_q1 = rev - rng.normal(1, 8)
        np_ = rng.normal(10, 35)
        np_ly = np_ - rng.normal(3, 25)
        gm = np.nan if fin else float(np.clip(rng.normal(28, 12), 2, 92))
        gm_ly = np.nan if fin else gm - rng.normal(0.5, 3)
        roe_raw = rng.normal(4.5, 3.5)
        roe_ly = roe_raw - rng.normal(0.2, 1.5)
        debt = float(np.clip(rng.normal(lev * 100, 8), 3, 98))
        pe = rng.lognormal(3.1, 0.55) * (1 if rng.random() > 0.12 else -1)
        vol = float(np.clip(rng.normal(32, 10), 8, 90))
        rows.append({
            "thscode": f"{ticker}.{ex}", "ticker": ticker, "name": f"{'*ST' if is_st else ''}演示{ind}{i:03d}",
            "exchange": ex, "list_date": (pd.Timestamp("2026-10-14") - pd.Timedelta(days=listed_days)).strftime("%Y-%m-%d"),
            "is_st": is_st, "industry_code": code, "industry_name": ind,
            "in_hs300": i % 8 == 0, "in_zz500": i % 8 == 1, "in_zz1000": i % 8 in (2, 3),
            "last_close": float(np.round(rng.lognormal(2.6, 0.7), 2)), "price_date": "2026-10-14",
            "pe_ttm": float(pe), "pb_mrq": float(np.round(rng.lognormal(0.6, 0.6), 3)),
            "ps_ttm": float(np.round(rng.lognormal(1.0, 0.8), 3)), "pcf_ttm": float(rng.normal(18, 25)),
            "rev_yoy": rev, "np_yoy": np_, "rev_yoy_q1": rev_q1, "rev_yoy_ly": rev_ly, "np_yoy_ly": np_ly,
            "gross_margin": gm, "gm_ly": gm_ly, "net_margin": float(rng.normal(9, 9)),
            "roe_raw": roe_raw, "roe_ly": roe_ly,
            "debt_ratio": debt, "current_ratio": np.nan if fin else float(np.clip(rng.lognormal(0.4, 0.5), 0.2, 15)),
            "cash_content": float(rng.normal(95, 70)), "ocf_to_rev": float(rng.normal(9, 12)),
            "vol_60d": vol, "mdd_120d": float(np.clip(vol * rng.uniform(0.3, 0.9), 2, 80)),
            "ret_20d": float(rng.normal(1, 8)), "ret_60d": float(rng.normal(3, 15)),
            "ret_250d": float(rng.normal(8, 30)), "amt_20d": float(np.round(rng.lognormal(0.3, 1.1), 3)),
            "div_yield": float(np.clip(rng.lognormal(0.3, 0.9) if rng.random() > 0.25 else 0, 0, 12)),
            "listed_days": listed_days,
        })
    df = pd.DataFrame(rows).set_index("thscode")
    # 约 5% 的随机缺失，模拟接口没有返回的字段
    for col in ("pe_ttm", "rev_yoy", "np_yoy", "vol_60d", "div_yield", "roe_raw"):
        df.loc[df.sample(frac=0.05, random_state=len(col)).index, col] = np.nan
    derive(df)
    meta = {
        "snapshot_id": "demo-20261014", "mode": "demo", "built_at": "2026-10-15T00:00:00+08:00",
        "sources": {
            "valuation": {"as_of": "2026-10-14 15:00（演示）"},
            "financials": {"reports": list(REPORTS), "latest": REPORT_LATEST},
            "prices": {"last_date": "2026-10-14"},
            "industry": {"level": "演示行业", "count": len(INDUSTRIES)},
        },
        "notes": ["这是合成的演示数据，股票名都带“演示”字样，数字不代表任何真实公司。"],
    }
    return df, meta


def derive(df: pd.DataFrame) -> None:
    """由原始字段派生的指标。真实快照和演示数据共用这一个函数，口径只写一次。"""
    df["rev_yoy_accel"] = df["rev_yoy"] - df["rev_yoy_q1"]
    df["rev_yoy_vs_ly"] = df["rev_yoy"] - df["rev_yoy_ly"]
    df["np_yoy_vs_ly"] = df["np_yoy"] - df["np_yoy_ly"]
    df["gm_delta"] = df["gross_margin"] - df["gm_ly"]
    df["roe_ann"] = df["roe_raw"] * 2
    df["roe_delta"] = df["roe_raw"] - df["roe_ly"]

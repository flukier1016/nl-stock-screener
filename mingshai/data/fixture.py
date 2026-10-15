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
        # 利润表原始金额（元，上半年累计 / 一季度），增速由 derive() 统一计算
        rev_25 = float(rng.lognormal(21.5, 1.2))
        g26, g25 = rng.normal(0.08, 0.18), rng.normal(0.06, 0.15)
        rev_26, rev_24 = rev_25 * (1 + g26), rev_25 / (1 + g25)
        q1s = rng.uniform(0.42, 0.52)
        rev_q1_26, rev_q1_25 = rev_26 * q1s * rng.uniform(0.95, 1.05), rev_25 * q1s
        margin = rng.normal(0.09, 0.08)
        np_25 = rev_25 * margin
        np_26 = np_25 * (1 + rng.normal(0.1, 0.35)) if np_25 > 0 else np_25 + abs(np_25) * rng.normal(0.3, 0.6)
        np_24 = np_25 / (1 + rng.normal(0.05, 0.3)) if rng.random() > 0.08 else -abs(np_25) * 0.4
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
            "rev_h1_26": rev_26, "rev_h1_25": rev_25, "rev_h1_24": rev_24, "rev_q1_26": rev_q1_26, "rev_q1_25": rev_q1_25,
            "np_h1_26": np_26, "np_h1_25": np_25, "np_h1_24": np_24, "rpt_h1_26": "2026-08-28",
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
    for col in ("pe_ttm", "rev_h1_26", "np_h1_26", "vol_60d", "div_yield", "roe_raw"):
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


def _ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    """a ÷ b − 1（%）；分母 ≤ 0 或缺失时为缺失。"""
    return pd.Series(np.where(b > 0, (a / b - 1) * 100, np.nan), index=a.index)


def _change(a: pd.Series, b: pd.Series) -> pd.Series:
    """(a − b) ÷ |b|（%）；分母为 0 或缺失时为缺失。用于利润：亏损收窄 / 扭亏记为正增长。"""
    return pd.Series(np.where((b != 0) & b.notna(), (a - b) / b.abs() * 100, np.nan), index=a.index)


def derive(df: pd.DataFrame) -> None:
    """由原始字段派生的指标。真实快照和演示数据共用这一个函数，口径只写一次。"""
    df["rev_yoy"] = _ratio(df["rev_h1_26"], df["rev_h1_25"])
    df["rev_yoy_ly"] = _ratio(df["rev_h1_25"], df["rev_h1_24"])
    df["rev_q1_yoy"] = _ratio(df["rev_q1_26"], df["rev_q1_25"])
    df["rev_q2_26"] = df["rev_h1_26"] - df["rev_q1_26"]
    df["rev_q2_25"] = df["rev_h1_25"] - df["rev_q1_25"]
    df["rev_q2_yoy"] = _ratio(df["rev_q2_26"], df["rev_q2_25"])
    df["rev_yoy_accel"] = df["rev_q2_yoy"] - df["rev_q1_yoy"]
    df["rev_yoy_vs_ly"] = df["rev_yoy"] - df["rev_yoy_ly"]
    df["np_yoy"] = _change(df["np_h1_26"], df["np_h1_25"])
    df["np_yoy_ly"] = _change(df["np_h1_25"], df["np_h1_24"])
    df["np_yoy_vs_ly"] = df["np_yoy"] - df["np_yoy_ly"]
    df["gm_delta"] = df["gross_margin"] - df["gm_ly"]
    df["roe_ann"] = df["roe_raw"] * 2
    df["roe_delta"] = df["roe_raw"] - df["roe_ly"]

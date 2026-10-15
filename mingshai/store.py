"""数据快照：引擎只读这里，不直接碰网络。

快照由 :mod:`mingshai.data.build` 从扶摇接口构建，写成一个 DuckDB 文件（``features``
表 + ``meta`` 表）。服务启动或快照更新后整表读进内存：全 A 股约 5,400 行 × 几十列，
筛选、敏感度扫描都在内存里向量化完成，毫秒级。

没有真实快照时**不会**悄悄换成演示数据：``load()`` 返回 None，接口返回"数据不可用"；
只有显式选择 demo 模式才加载合成数据，并在每个响应里带上 ``data_mode``。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from .data.fixture import build_fixture
from .registry import METRICS

DATA_DIR = Path(os.getenv("MINGSHAI_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
SNAPSHOT_PATH = DATA_DIR / "snapshot.duckdb"


@dataclass
class Dataset:
    frame: pd.DataFrame
    meta: dict
    industries: dict[str, str] = field(default_factory=dict)   # 行业名 → 行业指数 thscode

    @property
    def snapshot_id(self) -> str:
        return self.meta.get("snapshot_id", "unknown")

    @property
    def mode(self) -> str:
        return self.meta.get("mode", "live")

    def coverage(self, index: pd.Index | None = None) -> dict[str, float]:
        f = self.frame if index is None else self.frame.loc[index]
        n = max(len(f), 1)
        return {m.id: round(float(f[m.id].notna().sum()) / n, 4) for m in METRICS if m.id in f}


def _industries(frame: pd.DataFrame) -> dict[str, str]:
    pairs = frame[["industry_name", "industry_code"]].dropna().drop_duplicates()
    return dict(zip(pairs["industry_name"], pairs["industry_code"], strict=False))


def load_demo() -> Dataset:
    df, meta = build_fixture()
    return Dataset(df, meta, _industries(df))


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Dataset | None:
    if not path.exists():
        return None
    con = duckdb.connect(str(path), read_only=True)
    try:
        df = con.execute("SELECT * FROM features").df().set_index("thscode")
        meta = json.loads(con.execute("SELECT value FROM meta WHERE key='meta'").fetchone()[0])
    finally:
        con.close()
    for m in METRICS:
        if m.id in df:
            df[m.id] = pd.to_numeric(df[m.id], errors="coerce")
    return Dataset(df, meta, _industries(df))


class Store:
    """进程内的当前快照；构建任务换新快照时原子替换引用。"""

    def __init__(self, mode: str | None = None):
        self.mode = mode or os.getenv("MINGSHAI_DATA", "live")
        self._lock = threading.Lock()
        self._live: Dataset | None = None
        self._demo: Dataset | None = None
        self.load_error: str | None = None

    def reload(self) -> None:
        try:
            ds = load_snapshot()
            with self._lock:
                self._live = ds
            self.load_error = None if ds else "还没有真实数据快照"
        except Exception as e:  # noqa: BLE001 —— 坏快照要报出来，不能当成没数据
            self.load_error = f"快照读取失败：{e}"

    def get(self, mode: str | None = None) -> Dataset | None:
        mode = mode or self.mode
        if mode == "demo":
            if self._demo is None:
                self._demo = load_demo()
            return self._demo
        with self._lock:
            return self._live

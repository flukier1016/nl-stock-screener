"""Repair listing metadata with one bounded upstream refresh, preserving market dates."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import pandas as pd

from ..store import SNAPSHOT_PATH
from .build import LEDGER, _write_snapshot
from .fuyao import FuyaoClient, load_api_key
from .quality import listing_dates


async def repair_listings() -> dict:
    key = load_api_key()
    if not key:
        raise ValueError("没有配置扶摇密钥，无法核验上市日期")
    con = duckdb.connect(str(SNAPSHOT_PATH), read_only=True)
    try:
        frame = con.execute("SELECT * FROM features").df()
        meta = json.loads(con.execute("SELECT value FROM meta WHERE key='meta'").fetchone()[0])
    finally:
        con.close()
    client = FuyaoClient(key, ledger=LEDGER, concurrency=1, max_attempts=1)
    try:
        items, env = await client.tickers()
    finally:
        await client.close()
    tickers = pd.DataFrame(items).drop_duplicates("thscode").set_index("thscode")
    dates = listing_dates(frame["thscode"].map(tickers["list_date"]))
    price_dates = listing_dates(frame["price_date"])
    fallback = pd.to_datetime(meta.get("sources", {}).get("prices", {}).get("last_date"), errors="coerce")
    price_dates = price_dates.fillna(fallback)
    frame["list_date"] = dates
    frame["listed_days"] = (price_dates - dates).dt.days
    frame["listing_request_id"] = env.get("request_id")
    old_id = meta["snapshot_id"]
    now = datetime.now(UTC)
    meta["snapshot_id"] = now.strftime("%Y%m%d-%H%M%S") + "-listingfix"
    meta["repaired_at"] = now.isoformat(timespec="seconds")
    meta.setdefault("sources", {})["listing_metadata"] = {
        "endpoint": "/api/meta/tickers/list", "request_id": env.get("request_id"),
        "timestamp": env.get("data", {}).get("timestamp"), "previous_snapshot": old_id,
    }
    meta.setdefault("notes", []).append("上市日期已重新向扶摇核验；行情、估值和财务仍保持各自标注的数据时点。")
    # _write_snapshot enforces quality before replacing either data file.
    _write_snapshot(frame, meta)
    return {"snapshot_id": meta["snapshot_id"], "previous_snapshot": old_id,
            "listed_days_coverage": float(frame["listed_days"].notna().mean()),
            "market_date": meta.get("sources", {}).get("prices", {}).get("last_date")}

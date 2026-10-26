from __future__ import annotations

import asyncio
import importlib
import json

import duckdb
import numpy as np
from fastapi.testclient import TestClient

from mingshai import app as appmod
from mingshai.data import build, repair
from mingshai.data.quality import resolve_listing_dates
from mingshai.store import Store, load_demo, load_snapshot

storemod = importlib.import_module("mingshai.store")


def write_raw(path, frame, meta):
    con = duckdb.connect(str(path))
    try:
        con.register("source", frame.reset_index())
        con.execute("CREATE TABLE features AS SELECT * FROM source")
        con.execute("CREATE TABLE meta (key VARCHAR,value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('meta', ?)", [json.dumps(meta)])
    finally:
        con.close()


def test_restart_uses_validated_predecessor_and_reports_rejection(tmp_path, monkeypatch):
    ds = load_demo()
    target = tmp_path / "snapshot.duckdb"
    write_raw(target.with_suffix(".prev.duckdb"), ds.frame, ds.meta)
    broken = ds.frame.copy()
    broken["listed_days"] = np.nan
    write_raw(target, broken, {**ds.meta, "snapshot_id": "broken-new"})
    monkeypatch.setattr(storemod, "SNAPSHOT_PATH", target)
    store = Store()
    store.reload()
    assert store.get().snapshot_id == ds.snapshot_id
    assert store.load_warning and "listed_days" in store.load_warning
    assert store.load_error is None


def test_invalid_upload_cannot_overwrite_existing_snapshot(tmp_path, monkeypatch):
    ds = load_demo()
    target = tmp_path / "snapshot.duckdb"
    write_raw(target, ds.frame, ds.meta)
    original = target.read_bytes()
    broken = ds.frame.copy()
    broken["listed_days"] = np.nan
    incoming = tmp_path / "incoming.duckdb"
    write_raw(incoming, broken, ds.meta)
    monkeypatch.setattr(storemod, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(storemod, "DATA_DIR", tmp_path)
    monkeypatch.setenv("MINGSHAI_ADMIN_TOKEN", "test-admin")
    client = TestClient(appmod.app)
    response = client.post("/api/admin/snapshot", content=incoming.read_bytes(),
                           headers={"Authorization": "Bearer test-admin"})
    assert response.status_code == 422
    assert target.read_bytes() == original


def test_listing_repair_keeps_market_data_and_traces_metadata(tmp_path, monkeypatch):
    ds = load_demo()
    frame = ds.frame.copy()
    frame["listed_days"] = np.nan
    meta = {**ds.meta, "snapshot_id": "broken-source"}
    target = tmp_path / "snapshot.duckdb"
    write_raw(target, frame, meta)
    monkeypatch.setattr(repair, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(build, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(repair, "load_api_key", lambda: "test-only")

    class MetadataClient:
        def __init__(self, *args, **kwargs):
            pass

        async def tickers(self):
            return ([{"thscode": code, "list_date": "2020-01-01"} for code in frame.index],
                    {"request_id": "metadata-check-1", "data": {"timestamp": 123}})

        async def close(self):
            pass

    monkeypatch.setattr(repair, "FuyaoClient", MetadataClient)
    result = asyncio.run(repair.repair_listings())
    repaired = load_snapshot(target)
    assert result["listed_days_coverage"] == 1
    assert repaired.frame["listed_days"].notna().all()
    assert repaired.meta["built_at"] == meta["built_at"]
    assert repaired.meta["sources"]["prices"] == meta["sources"]["prices"]
    assert repaired.meta["sources"]["listing_metadata"]["request_id"] == "metadata-check-1"
    assert repaired.frame["pe_ttm"].equals(frame["pe_ttm"])
    assert len(list((tmp_path / "rejected").glob("*.duckdb"))) == 1


def test_admin_backup_and_repair_require_authentication():
    client = TestClient(appmod.app)
    assert client.get("/api/admin/snapshot").status_code == 401
    assert client.post("/api/admin/repair-listings").status_code == 401


def test_null_bulk_metadata_uses_known_dates_but_never_fills_unknown_stocks():
    import pandas as pd
    current = pd.DataFrame({"thscode": ["A", "B", "C"], "list_date": ["2021-01-01", None, None]})
    previous = pd.DataFrame({"thscode": ["A", "B"], "list_date": ["2001-01-01", "2002-01-01"]})
    result = resolve_listing_dates(current, previous, "prior-verified")
    assert current["list_date"].dt.strftime("%Y-%m-%d").tolist()[:2] == ["2021-01-01", "2002-01-01"]
    assert pd.isna(current.loc[2, "list_date"])
    assert current.loc[1, "list_date_source_snapshot"] == "prior-verified"
    assert result == {"from_current_metadata": 1, "from_verified_snapshot": 1, "missing": 1}

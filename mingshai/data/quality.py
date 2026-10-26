"""Data contracts for publication and loading; no network calls or silent fills."""
from __future__ import annotations

import pandas as pd
from pathlib import Path


class DataQualityError(ValueError):
    pass


def listing_dates(values: pd.Series) -> pd.Series:
    """Accept ISO dates, YYYYMMDD and Unix seconds/milliseconds explicitly."""
    def one(value):
        if pd.isna(value) or str(value).strip().lower() in ("", "none", "null", "nat"):
            return pd.NaT
        raw = str(value).strip()
        if raw.endswith(".0"):
            raw = raw[:-2]
        try:
            if raw.isdigit():
                if len(raw) == 8:
                    date = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
                elif len(raw) in (10, 13):
                    date = pd.to_datetime(int(raw), unit="ms" if len(raw) == 13 else "s", utc=True)
                else:
                    return pd.NaT
            else:
                date = pd.to_datetime(raw, errors="coerce", utc=True)
            if pd.isna(date):
                return pd.NaT
            if date.tzinfo is not None:
                date = date.tz_convert("Asia/Shanghai").tz_localize(None)
            return date.normalize() if 1980 <= date.year <= 2100 else pd.NaT
        except (ValueError, OverflowError, TypeError):
            return pd.NaT
    return pd.to_datetime(values.map(one))


def resolve_listing_dates(frame: pd.DataFrame, previous: pd.DataFrame | None, snapshot_id: str) -> dict:
    """Prefer fresh metadata; retain traced, immutable IPO dates on null responses."""
    fresh = listing_dates(frame["list_date"])
    frame["list_date_source_snapshot"] = "current_metadata"
    used = pd.Series(False, index=frame.index)
    if previous is not None and "list_date" in previous:
        old = previous.set_index("thscode") if "thscode" in previous else previous
        codes = frame["thscode"] if "thscode" in frame else pd.Series(frame.index, index=frame.index)
        cached = listing_dates(codes.map(old["list_date"]))
        used = fresh.isna() & cached.notna()
        fresh = fresh.fillna(cached)
        lineage = codes.map(old["list_date_source_snapshot"]) if "list_date_source_snapshot" in old else pd.Series(snapshot_id, index=frame.index)
        lineage = lineage.replace("current_metadata", snapshot_id).fillna(snapshot_id)
        frame.loc[used, "list_date_source_snapshot"] = lineage[used]
        if "listing_request_id" in old:
            if "listing_request_id" not in frame:
                frame["listing_request_id"] = None
            frame.loc[used, "listing_request_id"] = codes.map(old["listing_request_id"])[used]
    frame["list_date"] = fresh
    return {"from_current_metadata": int((fresh.notna() & ~used).sum()),
            "from_verified_snapshot": int(used.sum()), "missing": int(fresh.isna().sum())}


def quality_report(frame: pd.DataFrame, *, mode: str = "live") -> dict:
    errors: list[str] = []
    count = len(frame)
    if count < (50 if mode == "live" else 1):
        errors.append(f"快照只有{count}只股票，取数不完整")
    required = {"name", "industry_code", "listed_days", "is_st", "in_hs300", "in_zz500", "in_zz1000"}
    absent = required - set(frame.columns)
    if absent:
        errors.append("快照缺少关键字段：" + "、".join(sorted(absent)))
    codes = frame["thscode"] if "thscode" in frame else pd.Series(frame.index, index=frame.index)
    if codes.isna().any() or codes.duplicated().any():
        errors.append("股票代码缺失或重复")
    coverage: dict[str, float] = {}
    for field in ("name", "industry_code", "listed_days"):
        if field not in frame:
            continue
        col = frame[field]
        valid = col.notna()
        if field == "listed_days":
            numeric = pd.to_numeric(col, errors="coerce")
            valid &= numeric.between(0, 60000)
        elif field == "name":
            valid &= col.astype(str).str.strip().ne("")
        coverage[field] = float(valid.mean()) if count else 0
        threshold = 0.98 if field == "listed_days" else 0.95
        if coverage[field] < threshold:
            errors.append(f"{field}有效覆盖率{coverage[field]:.1%}，低于发布下限{threshold:.0%}")
    return {"ok": not errors, "errors": errors, "critical_coverage": coverage, "rows": count}


def require_quality(frame: pd.DataFrame, *, mode: str = "live") -> dict:
    report = quality_report(frame, mode=mode)
    if not report["ok"]:
        raise DataQualityError("；".join(report["errors"]))
    return report


def preserve_current(path: Path) -> None:
    """Keep the last healthy predecessor; archive rejected data separately."""
    if not path.exists():
        return
    import json
    import shutil
    import uuid
    import duckdb
    healthy = False
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            frame = con.execute("SELECT * FROM features").df()
            meta = json.loads(con.execute("SELECT value FROM meta WHERE key='meta'").fetchone()[0])
        finally:
            con.close()
        healthy = quality_report(frame, mode=meta.get("mode", "live"))["ok"]
    except Exception:
        pass
    if healthy:
        target = path.with_suffix(".prev.duckdb")
    else:
        archive = path.parent / "rejected"
        archive.mkdir(exist_ok=True)
        target = archive / f"snapshot-{uuid.uuid4().hex[:12]}.duckdb"
    shutil.copy2(path, target)

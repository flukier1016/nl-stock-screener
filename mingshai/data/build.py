"""从扶摇接口构建数据快照。

用法::

    FUYAO_API_KEY_FILE=~/.config/mingshai/fuyao.key .venv/bin/python -m mingshai.data.build
    .venv/bin/python -m mingshai.data.build --limit 200        # 只取前 200 只，联调用
    .venv/bin/python -m mingshai.data.build --refresh          # 日更：重取估值和行情，财务指标只补缺

流程：代码表 → 行业成分 → 指数成分 → 估值快照 → 财务指标（每只 × 3 个报告期）→
10 年日 K 与除权事件 → 行情指标 → 合成 ``features`` 表 → 先写临时文件，校验通过后原子替换。

原始返回落在 ``data/work.duckdb``，可以断点续跑；每次请求都在 ``data/ledger.jsonl`` 留痕。
任何一步失败都会写进 ``data/build_state.json`` 并终止，**不会**拿半套数据替换上一版快照。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ..registry import REPORT_LAST_YEAR, REPORT_LATEST, REPORT_PREV_Q, REPORTS, UNIVERSES
from ..store import DATA_DIR, SNAPSHOT_PATH
from .features import adjusted_returns, event_sanity, price_features
from .fixture import derive
from .fuyao import FuyaoClient, FuyaoError, load_api_key

WORK = DATA_DIR / "work.duckdb"
LEDGER = DATA_DIR / "ledger.jsonl"
STATE = DATA_DIR / "build_state.json"
DUMPS = DATA_DIR / "dumps"
TZ = "Asia/Shanghai"
NOT_READY = {3001, 3002, 3004}     # 标的不存在 / 数据未就绪 / 不支持：记为缺失，不重试

FIN_FIELDS = {
    "operating_income_yoy_growth_ratio", "net_profit_yoy_growth_ratio", "sale_gross_margin",
    "sale_net_interest_ratio", "index_weighted_avg_roe", "index_deduct_weighted_avg_roe",
    "assets_debt_ratio", "current_ratio", "net_profit_cash_content",
    "operating_cash_flow_net_divide_income",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ms_to_date(ms) -> pd.Series:
    return pd.to_datetime(ms, unit="ms", utc=True).dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()


class State:
    def __init__(self, path: Path = STATE):
        self.path = path
        self.data = {"status": "running", "started_at": _now(), "steps": []}

    def step(self, name: str, **kw) -> None:
        self.data.update({"step": name, "updated_at": _now(), **kw})
        self.data["steps"].append({"step": name, "at": _now()})
        self.save()

    def update(self, **kw) -> None:
        self.data.update({"updated_at": _now(), **kw})
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def read_state() -> dict | None:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _find_url(obj) -> str | None:
    """下载链接接口只说返回预签名链接，没给字段名；在 data 里找第一个 http(s) 字符串。"""
    if isinstance(obj, str) and obj.startswith(("http://", "https://")):
        return obj
    if isinstance(obj, dict):
        for k in ("url", "download_url", "presigned_url", "href"):
            if isinstance(obj.get(k), str) and obj[k].startswith("http"):
                return obj[k]
        for v in obj.values():
            u = _find_url(v)
            if u:
                return u
    if isinstance(obj, list):
        for v in obj:
            u = _find_url(v)
            if u:
                return u
    return None


async def build(limit: int | None = None, refresh: bool = False, concurrency: int = 6) -> dict:
    key = load_api_key()
    if not key:
        raise SystemExit("缺少扶摇 API Key：设置 FUYAO_API_KEY 或 FUYAO_API_KEY_FILE")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st = State()
    st.save()
    cli = FuyaoClient(key, ledger=LEDGER, concurrency=concurrency)
    work = duckdb.connect(str(WORK))
    try:
        meta = await _build(cli, work, st, limit, refresh)
        st.update(status="done", stats=cli.stats, snapshot_id=meta["snapshot_id"], finished_at=_now())
        return meta
    except Exception as e:
        st.update(status="failed", error=f"{type(e).__name__}: {e}", stats=cli.stats)
        raise
    finally:
        work.close()
        await cli.close()


async def _build(cli: FuyaoClient, work, st: State, limit: int | None, refresh: bool) -> dict:
    # 1. 代码表
    st.step("代码表")
    items, env = await cli.tickers("a-share")
    tickers = pd.DataFrame(items)
    tickers = tickers[tickers["thscode"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")].copy()
    tickers_ts = env["data"].get("timestamp")
    if limit:
        tickers = tickers.sort_values("thscode").head(limit)
    codes = tickers["thscode"].tolist()
    st.update(universe=len(codes))

    # 2. 行业：同花顺行业指数目录 + 成分股，自动识别层级
    st.step("行业成分")
    cat = (await cli.industry_catalog("industry"))["data"]
    catalog = pd.DataFrame(cat["item"])
    members = []
    sem_results = await asyncio.gather(*[cli.constituents(c) for c in catalog["thscode"]], return_exceptions=True)
    for code, res in zip(catalog["thscode"], sem_results, strict=True):
        if isinstance(res, Exception):
            raise RuntimeError(f"行业 {code} 成分股获取失败：{res}")
        for it in res["data"]["item"]:
            members.append({"index_code": code, "thscode": it["thscode"]})
    members = pd.DataFrame(members)
    industry, ind_info = choose_industry(catalog, members)

    # 3. 指数成分（股票池与大中小盘代理）
    st.step("指数成分")
    idx_members = {}
    for u in UNIVERSES:
        if u.index_thscode:
            res = await cli.constituents(u.index_thscode)
            idx_members[u.id] = {it["thscode"] for it in res["data"]["item"]}
            if len(idx_members[u.id]) < 100:
                raise RuntimeError(f"{u.label} 成分股只有 {len(idx_members[u.id])} 只，疑似接口异常")

    # 4. 估值快照（每批 100 只）
    st.step("估值快照", done=0, total=len(codes))
    val_rows, val_ts = [], []
    batches = [codes[i:i + 100] for i in range(0, len(codes), 100)]
    results = await asyncio.gather(*[cli.valuations(b) for b in batches], return_exceptions=True)
    for b, res in zip(batches, results, strict=True):
        if isinstance(res, Exception):
            raise RuntimeError(f"估值快照失败（{b[0]} 起 {len(b)} 只）：{res}")
        val_ts.append(res["data"].get("timestamp"))
        for it in res["data"]["item"]:
            it["val_request_id"] = res["request_id"]
            val_rows.append(it)
    valuation = pd.DataFrame(val_rows).drop_duplicates("thscode").set_index("thscode")

    # 5. 财务指标（断点续跑；日更模式沿用已有结果）
    fin = await _fin_indicators(cli, work, st, codes, refresh)

    # 6. 行情：10 年日 K + 近 10 日增量 + 除权事件
    st.step("行情数据下载")
    daily, events, cal_last = await _prices(cli, work)
    daily = daily[daily["thscode"].isin(codes)]
    st.step("行情指标计算")
    adj = adjusted_returns(daily, events)
    sanity = event_sanity(adj)
    if sanity["events"] > 200 and sanity["extreme_share"] > 0.05:
        raise RuntimeError(f"除权日收益率异常（|r|>25% 占比 {sanity['extreme_share']:.1%}），"
                           "疑似送转比例单位理解错误，停止构建")
    as_of = daily["date"].max()
    pf = price_features(adj, events, as_of)

    # 7. 合成
    st.step("合成快照")
    f = tickers.set_index("thscode")[["ticker", "name", "list_date"]].copy()
    f["exchange"] = f.index.str[-2:]
    f["is_st"] = f["name"].fillna("").str.upper().str.contains("ST")
    f = f.join(industry, how="left")
    for uid, members_set in idx_members.items():
        f[f"in_{uid}"] = f.index.isin(members_set)
    f = f.join(valuation[["pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm", "val_request_id"]], how="left")
    f = f.join(fin, how="left")
    f = f.join(pf, how="left")
    pdate = pd.to_datetime(f["price_date"].fillna(as_of.strftime("%Y-%m-%d")))
    f["listed_days"] = (pdate - pd.to_datetime(f["list_date"], errors="coerce")).dt.days
    derive(f)
    for col in ("in_hs300", "in_zz500", "in_zz1000", "is_st"):
        f[col] = f[col].fillna(False).astype(bool)

    snapshot_id = datetime.now().strftime("%Y%m%d-%H%M")
    val_as_of = max([t for t in val_ts if t] or [0])
    meta = {
        "snapshot_id": snapshot_id, "mode": "live", "built_at": _now(),
        "universe": len(f), "limit": limit,
        "sources": {
            "tickers": {"endpoint": "/api/meta/tickers/list", "timestamp": tickers_ts, "count": len(tickers)},
            "industry": ind_info,
            "index_members": {k: len(v) for k, v in idx_members.items()},
            "valuation": {"endpoint": "/api/a-share/valuations/snapshot", "timestamp_ms": val_as_of,
                          "as_of": _fmt_ms(val_as_of)},
            "financials": {"endpoint": "/api/a-share/financials/indicators", "reports": list(REPORTS),
                           "latest": REPORT_LATEST},
            "prices": {"endpoint": "market-dumps daily-k + daily-k-10d + adjustment-factors",
                       "last_date": as_of.strftime("%Y-%m-%d"), "calendar_last_trading_day": cal_last,
                       "event_sanity": sanity},
        },
        "fetch_stats": dict(cli.stats),
        "notes": [],
    }
    if cal_last and cal_last > as_of.strftime("%Y-%m-%d"):
        meta["notes"].append(f"行情数据截至 {as_of:%Y-%m-%d}，晚于它的交易日 {cal_last} 尚未进入导出文件。")
    _write_snapshot(f.reset_index(), meta)
    return meta


def _fmt_ms(ms) -> str | None:
    if not ms:
        return None
    return pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(TZ).strftime("%Y-%m-%d %H:%M")


def choose_industry(catalog: pd.DataFrame, members: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """同花顺行业指数有多个层级。用成分股包含关系识别层级：被别的指数完整包含的是下级。
    选覆盖率 ≥ 80% 的最细一级作为"行业中位数"的分组；一只股票只归一个组。"""
    sets = members.groupby("index_code")["thscode"].apply(set).to_dict()
    names = dict(zip(catalog["thscode"], catalog["name"], strict=False))
    level = {}
    codes = sorted(sets, key=lambda c: len(sets[c]))
    for c in codes:
        parents = [p for p in codes if p != c and len(sets[p]) > len(sets[c]) and sets[c] <= sets[p]]
        level[c] = len(parents) + 1
    all_stocks = set(members["thscode"])
    chosen_level, info = 1, {}
    for lv in sorted(set(level.values())):
        lv_codes = [c for c in level if level[c] == lv]
        covered = set().union(*(sets[c] for c in lv_codes)) if lv_codes else set()
        info[lv] = {"groups": len(lv_codes), "coverage": round(len(covered) / max(len(all_stocks), 1), 4)}
        if info[lv]["coverage"] >= 0.8 and len(lv_codes) <= 150:
            chosen_level = lv
    rows = {}
    for c in sorted([c for c in level if level[c] == chosen_level], key=lambda c: len(sets[c])):
        for s in sets[c]:
            rows.setdefault(s, c)
    top = {}
    for c in [c for c in level if level[c] == 1]:
        for s in sets[c]:
            top.setdefault(s, names.get(c))
    df = pd.DataFrame({"industry_code": pd.Series(rows)})
    df["industry_name"] = df["industry_code"].map(names)
    df["industry_l1"] = df.index.map(top)
    return df, {"endpoint": "/api/a-share-index/catalog/ths-index-list?tag=industry + constituents",
                "level_chosen": chosen_level, "levels": info, "groups": int(df["industry_code"].nunique())}


async def _fin_indicators(cli: FuyaoClient, work, st: State, codes: list[str], refresh: bool) -> pd.DataFrame:
    work.execute("""CREATE TABLE IF NOT EXISTS raw_fin (thscode VARCHAR, report VARCHAR, index_id VARCHAR,
                    value DOUBLE, request_id VARCHAR, fetched_at VARCHAR)""")
    work.execute("""CREATE TABLE IF NOT EXISTS raw_fin_status (thscode VARCHAR, report VARCHAR, code INTEGER,
                    message VARCHAR, request_id VARCHAR, fetched_at VARCHAR)""")
    # 全量和日更都是"只补缺"：已取过的（股票, 报告期）不重复请求。日更时新上市或之前缺的会被补上；
    # 新服务器没有工作库时等同全量，不会出现"沿用了一个不存在的旧结果"导致财务列全空。
    done = set(map(tuple, work.execute("SELECT DISTINCT thscode, report FROM raw_fin_status").fetchall()))
    todo = [(c, r) for c in codes for r in REPORTS if (c, r) not in done]
    st.step("财务指标" + ("（日更补缺）" if refresh else ""), done=len(codes) * len(REPORTS) - len(todo),
            total=len(codes) * len(REPORTS))
    chunk = 400
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        res = await asyncio.gather(*[cli.fin_indicators(c, r) for c, r in part], return_exceptions=True)
        vals, stats = [], []
        for (c, r), x in zip(part, res, strict=True):
            ts = _now()
            if isinstance(x, FuyaoError):
                if x.code in NOT_READY:
                    stats.append((c, r, x.code, x.message[:120], x.request_id, ts))
                    continue
                raise RuntimeError(f"财务指标 {c} {r} 失败：{x}")
            if isinstance(x, Exception):
                raise RuntimeError(f"财务指标 {c} {r} 失败：{x}")
            stats.append((c, r, 0, "ok", x["request_id"], ts))
            for ab in (x["data"] or {}).get("abilities", []):
                for ind in ab.get("indicators", []):
                    if ind["index_id"] in FIN_FIELDS:
                        v = ind.get("value")
                        vals.append((c, r, ind["index_id"], float(v) if v not in (None, "") else None,
                                     x["request_id"], ts))
        if vals:
            work.executemany("INSERT INTO raw_fin VALUES (?, ?, ?, ?, ?, ?)", vals)
        work.executemany("INSERT INTO raw_fin_status VALUES (?, ?, ?, ?, ?, ?)", stats)
        st.update(done=len(codes) * len(REPORTS) - len(todo) + i + len(part), stats=dict(cli.stats))
    raw = work.execute("""SELECT thscode, report, index_id, value, request_id FROM raw_fin
                          WHERE thscode IN (SELECT UNNEST(?))""", [codes]).df()
    wide = raw.pivot_table(index="thscode", columns=["report", "index_id"], values="value", aggfunc="last")

    def col(report: str, field: str) -> pd.Series:
        key = (report, field)
        return wide[key] if key in wide.columns else pd.Series(np.nan, index=wide.index)

    out = pd.DataFrame(index=wide.index)
    L, Q, Y = REPORT_LATEST, REPORT_PREV_Q, REPORT_LAST_YEAR
    out["rev_yoy"] = col(L, "operating_income_yoy_growth_ratio")
    out["np_yoy"] = col(L, "net_profit_yoy_growth_ratio")
    out["rev_yoy_q1"] = col(Q, "operating_income_yoy_growth_ratio")
    out["rev_yoy_ly"] = col(Y, "operating_income_yoy_growth_ratio")
    out["np_yoy_ly"] = col(Y, "net_profit_yoy_growth_ratio")
    out["gross_margin"] = col(L, "sale_gross_margin")
    out["gm_ly"] = col(Y, "sale_gross_margin")
    out["net_margin"] = col(L, "sale_net_interest_ratio")
    out["roe_raw"] = col(L, "index_weighted_avg_roe")
    out["roe_ly"] = col(Y, "index_weighted_avg_roe")
    out["debt_ratio"] = col(L, "assets_debt_ratio")
    out["current_ratio"] = col(L, "current_ratio")
    out["cash_content"] = col(L, "net_profit_cash_content")
    out["ocf_to_rev"] = col(L, "operating_cash_flow_net_divide_income")
    rid = raw[raw["report"] == L].groupby("thscode")["request_id"].first()
    out["fin_request_id"] = rid
    return out


async def _prices(cli: FuyaoClient, work) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    DUMPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    files = {}
    for kind in ("daily-k", "daily-k-10d", "adjustment-factors"):
        dest = DUMPS / f"{kind}-{stamp}.parquet"
        if not dest.exists():
            env = await cli.dump_url(kind)
            url = _find_url(env.get("data"))
            if not url:
                raise RuntimeError(f"{kind} 下载链接接口没有返回 URL：{json.dumps(env)[:300]}")
            await cli.download(url, dest)
        files[kind] = dest
    con = duckdb.connect()
    cutoff_ms = int((pd.Timestamp.now(tz=TZ) - pd.Timedelta(days=430)).timestamp() * 1000)
    q = """SELECT thscode, date_ms, close_price, turnover FROM read_parquet(?) WHERE date_ms >= ?"""
    d10y = con.execute(q, [str(files["daily-k"]), cutoff_ms]).df()
    d10d = con.execute(q, [str(files["daily-k-10d"]), cutoff_ms]).df()
    ev = con.execute("SELECT * FROM read_parquet(?)", [str(files["adjustment-factors"])]).df()
    con.close()
    daily = pd.concat([d10y, d10d]).drop_duplicates(["thscode", "date_ms"], keep="last")
    daily["date"] = _ms_to_date(daily["date_ms"])
    ev["ex_date"] = _ms_to_date(ev["ex_date_ms"])
    cal_last = None
    try:
        cal = await cli.calendar()
        dates = [it["date"] for it in cal["data"]["item"]]
        today = datetime.now().strftime("%Y%m%d")
        past = [d for d in dates if d <= today]
        if past:
            cal_last = f"{past[-1][:4]}-{past[-1][4:6]}-{past[-1][6:]}"
    except FuyaoError:
        pass
    return daily, ev, cal_last


def _write_snapshot(features: pd.DataFrame, meta: dict) -> None:
    if len(features) < 50:
        raise RuntimeError(f"快照只有 {len(features)} 只股票，疑似取数不完整，不替换现有快照")
    tmp = SNAPSHOT_PATH.with_suffix(".duckdb.new")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    con.register("f", features)
    con.execute("CREATE TABLE features AS SELECT * FROM f")
    con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO meta VALUES ('meta', ?)", [json.dumps(meta, ensure_ascii=False, default=str)])
    con.close()
    if SNAPSHOT_PATH.exists():
        shutil.copy2(SNAPSHOT_PATH, SNAPSHOT_PATH.with_suffix(".prev.duckdb"))
    os.replace(tmp, SNAPSHOT_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description="从扶摇接口构建明筛数据快照")
    ap.add_argument("--limit", type=int, default=None, help="只取代码表前 N 只（联调用）")
    ap.add_argument("--refresh", action="store_true", help="日更：重取估值和行情，财务指标只补缺")
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()
    t0 = time.time()
    meta = asyncio.run(build(a.limit, a.refresh, a.concurrency))
    print(json.dumps({"snapshot_id": meta["snapshot_id"], "universe": meta["universe"],
                      "seconds": round(time.time() - t0), "stats": meta["fetch_stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

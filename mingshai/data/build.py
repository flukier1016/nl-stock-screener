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
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ..registry import FIN_REPORTS, INCOME_LIMIT, REPORT_LAST_YEAR, REPORT_LATEST, REPORTS, UNIVERSES
from ..store import DATA_DIR, SNAPSHOT_PATH
from .features import adjusted_returns, event_sanity, price_features
from .fixture import derive
from .fuyao import FuyaoClient, FuyaoError, load_api_key
from .quality import listing_dates, preserve_current, require_quality

WORK = DATA_DIR / "work.duckdb"
LEDGER = DATA_DIR / "ledger.jsonl"
STATE = DATA_DIR / "build_state.json"
DUMPS = DATA_DIR / "dumps"
TZ = "Asia/Shanghai"
# 某只股票某一期"没有数据"的几种返回：记为缺失（覆盖率里看得到），不终止构建。
# 实测：代码表里个别北交所代码在财务接口返回 1002 Unknown thscode；未发布的报告期返回 5003。
MISSING = {1002, 3001, 3002, 3004, 5003}
FATAL = {2001, 2003}                 # Key 无效 / 无权限：立即终止
MAX_ERROR_RATE = 0.02                # 其他错误超过 2% 视为系统性故障，终止且不替换旧快照

FIN_FIELDS = {
    "sale_gross_margin", "sale_net_interest_ratio", "index_weighted_avg_roe", "index_deduct_weighted_avg_roe",
    "assets_debt_ratio", "current_ratio", "net_profit_cash_content", "operating_cash_flow_net_divide_income",
}
INCOME_FIELDS = ("operating_income", "operating_costs", "net_profit", "parent_holder_net_profit")


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


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


async def build(limit: int | None = None, refresh: bool = False, concurrency: int = 6, qps: float = 12.0) -> dict:
    key = load_api_key()
    if not key:
        raise SystemExit("缺少扶摇 API Key：设置 FUYAO_API_KEY 或 FUYAO_API_KEY_FILE")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st = State()
    st.save()
    cli = FuyaoClient(key, ledger=LEDGER, concurrency=concurrency, min_interval=1.0 / max(qps, 0.5))
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
    inc = await _income(cli, work, st, codes)

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
    f = f.join(inc, how="left")
    f = f.join(pf, how="left")
    pdate = pd.to_datetime(f["price_date"].fillna(as_of.strftime("%Y-%m-%d")))
    f["list_date"] = listing_dates(f["list_date"])
    f["listed_days"] = (pdate - f["list_date"]).dt.days
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
            "financials": {"endpoint": "/api/a-share/financials/indicators（比率）+ income-statements（增速自算）",
                           "reports": list(REPORTS), "indicator_reports": list(FIN_REPORTS), "latest": REPORT_LATEST,
                           "missing": _missing_summary(work)},
            "prices": {"endpoint": "market-dumps daily-k + daily-k-10d + adjustment-factors",
                       "last_date": as_of.strftime("%Y-%m-%d"), "calendar_last_trading_day": cal_last,
                       "event_sanity": sanity},
        },
        "fetch_stats": dict(cli.stats),
        "notes": [],
    }
    if cal_last and cal_last > as_of.strftime("%Y-%m-%d"):
        meta["notes"].append(f"行情更新到 {as_of:%Y-%m-%d} 收盘，{cal_last} 的行情还没发布。")
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


class _ErrorBudget:
    """单只股票没数据算缺失；鉴权错误立刻终止；其他错误累计超过 2% 认为是系统性故障。"""

    def __init__(self, what: str):
        self.what, self.calls, self.errors, self.examples = what, 0, 0, []

    def check(self, code: str, err: Exception) -> bool:
        """返回 True 表示可以记为缺失；否则累计错误，超预算时抛出。"""
        self.calls += 1
        if isinstance(err, FuyaoError):
            if err.code in FATAL:
                raise RuntimeError(f"{self.what}：扶摇 Key 无效或无权限（code={err.code}），终止构建") from err
            if err.code in MISSING:
                return True
        self.errors += 1
        if len(self.examples) < 3:
            self.examples.append(f"{code}: {err}")
        if self.calls >= 50 and self.errors / self.calls > MAX_ERROR_RATE:
            raise RuntimeError(f"{self.what} 错误率 {self.errors}/{self.calls} 超过 {MAX_ERROR_RATE:.0%}，"
                               f"疑似接口故障，终止构建：{self.examples}")
        return False

    def ok(self) -> None:
        self.calls += 1


async def _fin_indicators(cli: FuyaoClient, work, st: State, codes: list[str], refresh: bool) -> pd.DataFrame:
    work.execute("""CREATE TABLE IF NOT EXISTS raw_fin (thscode VARCHAR, report VARCHAR, index_id VARCHAR,
                    value DOUBLE, request_id VARCHAR, fetched_at VARCHAR)""")
    work.execute("""CREATE TABLE IF NOT EXISTS raw_fin_status (thscode VARCHAR, report VARCHAR, code INTEGER,
                    message VARCHAR, request_id VARCHAR, fetched_at VARCHAR)""")
    # 全量和日更都是"只补缺"：已取过的（股票, 报告期）不重复请求；新服务器没有工作库时等同全量。
    done = set(map(tuple, work.execute("SELECT DISTINCT thscode, report FROM raw_fin_status").fetchall()))
    todo = [(c, r) for c in codes for r in FIN_REPORTS if (c, r) not in done]
    total = len(codes) * len(FIN_REPORTS)
    st.step("财务指标" + ("（日更补缺）" if refresh else ""), done=total - len(todo), total=total)
    budget = _ErrorBudget("财务指标")
    chunk = 300
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        res = await asyncio.gather(*[cli.fin_indicators(c, r) for c, r in part], return_exceptions=True)
        vals, stats = [], []
        for (c, r), x in zip(part, res, strict=True):
            ts = _now()
            if isinstance(x, Exception):
                if budget.check(c, x):
                    stats.append((c, r, getattr(x, "code", None), str(getattr(x, "message", x))[:120],
                                  getattr(x, "request_id", None), ts))
                continue
            budget.ok()
            stats.append((c, r, 0, "ok", x["request_id"], ts))
            for ab in (x["data"] or {}).get("abilities", []):
                for ind in ab.get("indicators", []):
                    if ind["index_id"] in FIN_FIELDS:
                        v = ind.get("value")
                        vals.append((c, r, ind["index_id"], float(v) if v not in (None, "") else None,
                                     x["request_id"], ts))
        if vals:
            work.executemany("INSERT INTO raw_fin VALUES (?, ?, ?, ?, ?, ?)", vals)
        if stats:
            work.executemany("INSERT INTO raw_fin_status VALUES (?, ?, ?, ?, ?, ?)", stats)
        st.update(done=total - len(todo) + i + len(part), stats=dict(cli.stats))
    raw = work.execute("""SELECT thscode, report, index_id, value, request_id FROM raw_fin
                          WHERE thscode IN (SELECT UNNEST(?))""", [codes]).df()
    wide = raw.pivot_table(index="thscode", columns=["report", "index_id"], values="value", aggfunc="last")

    def col(report: str, field: str) -> pd.Series:
        key = (report, field)
        return wide[key] if key in wide.columns else pd.Series(np.nan, index=wide.index)

    out = pd.DataFrame(index=wide.index)
    L, Y = REPORT_LATEST, REPORT_LAST_YEAR
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


async def _income(cli: FuyaoClient, work, st: State, codes: list[str]) -> pd.DataFrame:
    """利润表（季度、累计口径）最近 9 期 → 上半年与一季度的营收、归母净利润原始金额与披露日。"""
    work.execute("""CREATE TABLE IF NOT EXISTS raw_income (thscode VARCHAR, fiscal_year INTEGER, fiscal_period VARCHAR,
                    report_date_ms BIGINT, period_end_ms BIGINT, operating_income DOUBLE, operating_costs DOUBLE,
                    net_profit DOUBLE, parent_holder_net_profit DOUBLE, request_id VARCHAR, fetched_at VARCHAR)""")
    work.execute("""CREATE TABLE IF NOT EXISTS raw_income_status (thscode VARCHAR, code INTEGER, message VARCHAR,
                    request_id VARCHAR, fetched_at VARCHAR)""")
    done = {r[0] for r in work.execute("SELECT DISTINCT thscode FROM raw_income_status").fetchall()}
    todo = [c for c in codes if c not in done]
    st.step("利润表", done=len(codes) - len(todo), total=len(codes))
    budget = _ErrorBudget("利润表")
    chunk = 300
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        res = await asyncio.gather(*[cli.income_statements(c, INCOME_LIMIT) for c in part], return_exceptions=True)
        rows, stats = [], []
        for c, x in zip(part, res, strict=True):
            ts = _now()
            if isinstance(x, Exception):
                if budget.check(c, x):
                    stats.append((c, getattr(x, "code", None), str(getattr(x, "message", x))[:120],
                                  getattr(x, "request_id", None), ts))
                continue
            budget.ok()
            stats.append((c, 0, "ok", x["request_id"], ts))
            for it in (x["data"] or {}).get("item", []):
                rows.append((c, it.get("fiscal_year"), it.get("fiscal_period"), it.get("report_date_ms"),
                             it.get("period_end_ms"), *[_num(it.get(k)) for k in INCOME_FIELDS], x["request_id"], ts))
        if rows:
            work.executemany("INSERT INTO raw_income VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        if stats:
            work.executemany("INSERT INTO raw_income_status VALUES (?,?,?,?,?)", stats)
        st.update(done=len(codes) - len(todo) + i + len(part), stats=dict(cli.stats))
    raw = work.execute("""SELECT * FROM raw_income WHERE thscode IN (SELECT UNNEST(?))""", [codes]).df()
    raw = raw.drop_duplicates(["thscode", "fiscal_year", "fiscal_period"], keep="last")
    key = raw["fiscal_year"].astype(str) + raw["fiscal_period"].astype(str)
    rev = raw.assign(k=key).pivot_table(index="thscode", columns="k", values="operating_income", aggfunc="last")
    npf = raw.assign(k=key).pivot_table(index="thscode", columns="k", values="parent_holder_net_profit", aggfunc="last")
    rpt = raw.assign(k=key).pivot_table(index="thscode", columns="k", values="report_date_ms", aggfunc="last")

    def g(t: pd.DataFrame, k: str) -> pd.Series:
        return t[k] if k in t.columns else pd.Series(np.nan, index=t.index)

    out = pd.DataFrame(index=rev.index.union(npf.index))
    out["rev_h1_26"], out["rev_h1_25"], out["rev_h1_24"] = g(rev, "2026Q2"), g(rev, "2025Q2"), g(rev, "2024Q2")
    out["rev_q1_26"], out["rev_q1_25"] = g(rev, "2026Q1"), g(rev, "2025Q1")
    out["np_h1_26"], out["np_h1_25"], out["np_h1_24"] = g(npf, "2026Q2"), g(npf, "2025Q2"), g(npf, "2024Q2")
    rd = g(rpt, "2026Q2")
    out["rpt_h1_26"] = pd.to_datetime(rd, unit="ms", utc=True).dt.tz_convert(TZ).dt.strftime("%Y-%m-%d")
    out["inc_request_id"] = raw.groupby("thscode")["request_id"].first()
    return out


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _missing_summary(work) -> dict:
    """每类"没数据"的返回码各有多少，写进快照元数据，页面数据状态里能看到。"""
    out = {}
    for table, label in (("raw_fin_status", "财务指标"), ("raw_income_status", "利润表")):
        try:
            rows = work.execute(f"SELECT code, COUNT(*) FROM {table} WHERE code <> 0 GROUP BY code").fetchall()
            out[label] = {str(c): n for c, n in rows}
        except duckdb.CatalogException:
            out[label] = {}
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
    # Validate before touching the current file or its recoverable predecessor.
    meta["quality"] = require_quality(features, mode=meta.get("mode", "live"))
    tmp = SNAPSHOT_PATH.with_suffix(".duckdb.new")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    con.register("f", features)
    con.execute("CREATE TABLE features AS SELECT * FROM f")
    con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO meta VALUES ('meta', ?)", [json.dumps(meta, ensure_ascii=False, default=str)])
    con.close()
    preserve_current(SNAPSHOT_PATH)
    os.replace(tmp, SNAPSHOT_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description="从扶摇接口构建所以然数据快照")
    ap.add_argument("--limit", type=int, default=None, help="只取代码表前 N 只（联调用）")
    ap.add_argument("--refresh", action="store_true", help="日更：重取估值和行情，财务指标只补缺")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--qps", type=float, default=12.0, help="每秒请求上限；遇到限流会自动放慢")
    a = ap.parse_args()
    t0 = time.time()
    meta = asyncio.run(build(a.limit, a.refresh, a.concurrency, a.qps))
    print(json.dumps({"snapshot_id": meta["snapshot_id"], "universe": meta["universe"],
                      "seconds": round(time.time() - t0), "stats": meta["fetch_stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

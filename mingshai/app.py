"""所以然 Web 服务（FastAPI）。

主链路：/api/parse（一句话 → 片段 + 澄清选项 + 默认条件）→ /api/compile（用户改选项）→
/api/check（守门 + 冲突）→ /api/screen（确定性筛选 + 敏感度）→ /api/evidence（单股证据卡）。
后续动作：/api/compare、/api/strategies（保存）、/api/monitors（转为监控）、/api/explain（AI 白话解读）。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import conflicts, engine, explain, llm, monitors, parse_llm
from .compile import compile_strategy, describe
from .dsl import Segment, Strategy
from .guard import check_strategy
from .registry import METRICS_BY_ID, REFUSE_HINT, catalog
from .store import Dataset, Store

VERSION = (Path(__file__).parent / "VERSION").read_text().strip()
STATIC = Path(__file__).parent / "static"
store = Store()
store.reload()
@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_scheduler, daemon=True).start()
    yield


app = FastAPI(title="所以然 · 一句话选股", version=VERSION, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)
_cache: OrderedDict[str, tuple[Strategy, dict]] = OrderedDict()
_build_lock = threading.Lock()


# ── 工具 ────────────────────────────────────────────────────────────
def dataset(mode: str | None) -> Dataset:
    ds = store.get(mode)
    if ds is None:
        raise HTTPException(503, detail={
            "code": "DATA_UNAVAILABLE",
            "message": store.load_error or "真实数据快照尚未就绪",
            "hint": "数据构建完成前不会给出任何筛选结论；可以切换到演示数据（合成，股票名带“演示”）先体验流程。",
            "build": _build_state(),
        })
    return ds


def _build_state() -> dict | None:
    from .data.build import read_state
    return read_state()


def run_cached(ds: Dataset, s: Strategy) -> dict:
    key = s.fingerprint(ds.snapshot_id) + ds.mode
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key][1]
    res = engine.run(ds, s)
    _cache[key] = (s, res)
    while len(_cache) > 64:
        _cache.popitem(last=False)
    return res


def _mode(request: Request) -> str | None:
    return request.query_params.get("data") or request.headers.get("X-Data-Mode") or None


def _client(x_client_id: str | None) -> str:
    if not x_client_id or len(x_client_id) > 64:
        raise HTTPException(400, detail={"message": "缺少匿名客户端编号"})
    return x_client_id


# ── 元信息 ──────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    ds = store.get()
    return {"status": "ok", "version": VERSION, "snapshot_id": ds.snapshot_id if ds else None,
            "data_ready": ds is not None, "llm": llm.public_info()}


@app.get("/api/meta")
def meta(request: Request):
    mode = _mode(request)
    ds = store.get(mode)
    out = {"version": VERSION, "llm": llm.public_info(), "catalog": catalog(), "build": _build_state(),
           "data_ready": ds is not None, "data_mode": mode or store.mode, "load_error": store.load_error}
    if ds is not None:
        out["snapshot"] = ds.meta
        out["industries"] = sorted(ds.industries)
        out["coverage"] = ds.coverage()
        out["universe_size"] = int(len(ds.frame))
    return out


@app.get("/api/industries")
def industries(request: Request):
    ds = dataset(_mode(request))
    return {"by_name": ds.industries, "by_code": {v: k for k, v in ds.industries.items()}}


# ── 解析与编译 ──────────────────────────────────────────────────────
class ParseIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)


class CompileIn(BaseModel):
    query: str
    segments: list[Segment]
    choices: dict[int, str] = Field(default_factory=dict)


@app.post("/api/parse")
def api_parse(body: ParseIn, request: Request):
    ds = dataset(_mode(request))
    pr = parse_llm.parse(body.query.strip(), ds.industries)
    strategy, assumptions, notes = compile_strategy(body.query, pr.segments)
    refused = [s for s in pr.segments if s.kind == "refused"]
    return {"parse": pr.model_dump(), "strategy": strategy.model_dump(), "assumptions": pr.assumptions + assumptions,
            "notes": notes, "refuse_hint": REFUSE_HINT if refused else None,
            "only_refused": bool(refused) and not strategy.conditions}


@app.post("/api/compile")
def api_compile(body: CompileIn):
    strategy, assumptions, notes = compile_strategy(body.query, body.segments, body.choices)
    return {"strategy": strategy.model_dump(), "assumptions": assumptions, "notes": notes}


# ── 校验、筛选、证据 ────────────────────────────────────────────────
class StrategyIn(BaseModel):
    strategy: Strategy


@app.post("/api/check")
def api_check(body: StrategyIn, request: Request):
    ds = dataset(_mode(request))
    issues = check_strategy(body.strategy, set(ds.frame["industry_code"].dropna()))
    hints = [] if any(i["severity"] == "error" for i in issues) else conflicts.check(ds, body.strategy)
    return {"issues": issues, "conflicts": hints}


@app.post("/api/screen")
def api_screen(body: StrategyIn, request: Request):
    ds = dataset(_mode(request))
    s = body.strategy
    issues = check_strategy(s, set(ds.frame["industry_code"].dropna()))
    if any(i["severity"] == "error" for i in issues):
        return JSONResponse(status_code=422, content={"issues": issues})
    t0 = time.monotonic()
    res = run_cached(ds, s)
    hints = conflicts.check(ds, s)
    conds = [{**c.model_dump(), "text": describe(c.metric, c.op, c.value, c.basis),
              "metric_name": METRICS_BY_ID[c.metric].name, "unit": METRICS_BY_ID[c.metric].unit}
             for c in s.conditions]
    rows = [{"c": r["thscode"], "n": r["name"], "i": r["industry"], "k": r["category"],
             "v": [r["conds"][c.id]["value"] for c in s.conditions],
             "s": [r["conds"][c.id]["status"][0] for c in s.conditions],
             "t": [r["conds"][c.id]["threshold"] for c in s.conditions],
             "g": r["worst_gap"]} for r in res["rows"]]
    return {
        "run_id": res["run_id"], "snapshot_id": res["snapshot_id"], "data_mode": res["data_mode"],
        "elapsed_ms": int((time.monotonic() - t0) * 1000), "conditions": conds,
        "universe_steps": res["universe_steps"], "universe_count": res["universe_count"],
        "counts": res["counts"], "funnel": res["funnel"], "impact": res["impact"],
        "fail_distribution": res["fail_distribution"], "rows": rows,
        "summary": explain.summary(res, s), "relax": conflicts.relax_suggestions(res, s) if res["counts"]["passed"] < 5 else [],
        "conflicts": hints, "data_sources": ds.meta.get("sources", {}), "data_notes": _data_notes(ds),
    }


def _data_notes(ds: Dataset) -> list[str]:
    p = ds.meta.get("sources", {}).get("prices", {})
    last, cal = p.get("last_date"), p.get("calendar_last_trading_day")
    if last and cal and cal > last:
        return [f"行情更新到 {last} 收盘，{cal} 的行情还没发布。"]
    return [n for n in ds.meta.get("notes", []) if "行情数据截至" not in n]


class EvidenceIn(BaseModel):
    strategy: Strategy
    query: str = Field(min_length=1, max_length=40)


@app.post("/api/evidence")
def api_evidence(body: EvidenceIn, request: Request):
    ds = dataset(_mode(request))
    res = run_cached(ds, body.strategy)
    code = _resolve_code(ds, body.query.strip())
    if code is None:
        raise HTTPException(404, detail={"message": f"没有找到“{body.query}”，请输入 6 位代码或完整简称"})
    if code not in set(res["_index"]):
        r = ds.frame.loc[code]
        reasons = []
        for step in _universe_reasons(ds, body.strategy, code):
            reasons.append(step)
        return {"thscode": code, "name": r["name"], "category": "out_of_universe", "category_text": "不在股票池",
                "verdict": "没有进入股票池：" + "；".join(reasons), "items": [], "facts": [], "inferences": [],
                "uncertain": [], "snapshot_id": ds.snapshot_id, "data_mode": ds.mode}
    return explain.evidence(ds, res, body.strategy, code)


def _resolve_code(ds: Dataset, q: str) -> str | None:
    f = ds.frame
    if q.upper() in f.index:
        return q.upper()
    hit = f.index[f["ticker"].astype(str) == q]
    if len(hit):
        return hit[0]
    hit = f.index[f["name"] == q]
    if len(hit):
        return hit[0]
    hit = f.index[f["name"].astype(str).str.contains(q, regex=False)]
    return hit[0] if len(hit) == 1 else None


def _universe_reasons(ds: Dataset, s: Strategy, code: str) -> list[str]:
    r = ds.frame.loc[code]
    out = []
    from .registry import UNIVERSES_BY_ID
    u = UNIVERSES_BY_ID[s.universe.base]
    if u.index_thscode and not bool(r.get(f"in_{u.id}")):
        out.append(f"不是{u.label}成分股")
    if s.universe.industries and r.get("industry_code") not in s.universe.industries:
        out.append(f"所属行业是「{r.get('industry_name')}」，不在所选行业内")
    if s.universe.exclude_st and bool(r.get("is_st")):
        out.append("带 ST 标记，默认剔除")
    if s.universe.min_listed_days and (r.get("listed_days") or 0) < s.universe.min_listed_days:
        out.append(f"上市 {int(r.get('listed_days') or 0)} 天，不满 {s.universe.min_listed_days} 天")
    if s.universe.exclude_bj and r.get("exchange") == "BJ":
        out.append("北交所股票，已按设置剔除")
    return out or ["未知原因"]


class CompareIn(BaseModel):
    strategy: Strategy
    thscodes: list[str] = Field(min_length=2, max_length=5)


@app.post("/api/compare")
def api_compare(body: CompareIn, request: Request):
    ds = dataset(_mode(request))
    res = run_cached(ds, body.strategy)
    metric_ids = list(dict.fromkeys([c.metric for c in body.strategy.conditions] +
                                    ["pe_ttm", "pb_mrq", "roe_ann", "rev_yoy", "np_yoy", "debt_ratio",
                                     "div_yield", "vol_60d", "mdd_120d", "ret_60d", "amt_20d"]))
    rows = {r["thscode"]: r for r in res["rows"]}
    out = []
    for code in body.thscodes:
        if code not in ds.frame.index:
            continue
        r = ds.frame.loc[code]
        out.append({"thscode": code, "name": r["name"], "industry": r.get("industry_name"),
                    "category": rows.get(code, {}).get("category", "out_of_universe"),
                    "values": {m: explain._s(r.get(m)) and float(r.get(m)) for m in metric_ids}})
    pool = ds.frame.loc[res["_index"]]
    pct = {m: {c["thscode"]: _pct(pool[m], c["values"][m]) for c in out} for m in metric_ids}
    return {"metrics": [METRICS_BY_ID[m].as_dict() for m in metric_ids], "stocks": out, "percentile": pct,
            "note": "分位为该值在当前股票池中的位置（0% 最小，100% 最大），只描述位置，不代表好坏。"}


def _pct(series, v):
    if v is None:
        return None
    s = series.dropna()
    return None if not len(s) else round(float((s < v).mean() * 100), 1)


class ExplainIn(BaseModel):
    strategy: Strategy


@app.post("/api/explain")
def api_explain(body: ExplainIn, request: Request):
    """AI 白话解读：只能复述引擎给出的事实，写完过数字守门和合规检查，不通过就整段丢弃。"""
    ds = dataset(_mode(request))
    res = run_cached(ds, body.strategy)
    summ = explain.summary(res, body.strategy)
    if not llm.available():
        return {"ok": False, "reason": "未配置大模型", "fallback": summ["text"]}
    conds = "\n".join(f"- {describe(c.metric, c.op, c.value, c.basis)}（{METRICS_BY_ID[c.metric].definition}）"
                      for c in body.strategy.conditions)
    funnel = "\n".join(f"- 满足前 {i + 1} 条后剩 {f['count']} 只" for i, f in enumerate(res["funnel"]))
    impact = "\n".join(f"- 去掉“{f['label']}”会多出 {i['removed_adds']} 只" for f, i in zip(res["funnel"], res["impact"], strict=True))
    from .registry import UNIVERSES_BY_ID
    u = UNIVERSES_BY_ID[body.strategy.universe.base]
    pool = " → ".join(f"{x['step']} {x['count']} 只" for x in res["universe_steps"])
    pool_note = f"（{u.note}）" if u.index_thscode else ""
    facts = (f"用户原话：{body.strategy.query}\n股票池：{pool}{pool_note}\n条件：\n{conds}\n结果：{summ['text']}\n"
             f"逐条漏斗：\n{funnel}\n条件影响：\n{impact}\n数据：财务 2026 中报；估值快照与行情时点见数据状态栏。")
    system = ("你是选股结果的解说员。只能复述下面给出的事实（股票池与条件都已写明），用 3 条以内的中文短句说明：这组条件实际在筛什么、"
              "哪一条最严、用户可以怎么调整。禁止出现任何没有在事实里给出的数字；禁止预测涨跌、承诺收益、"
              "给出买卖建议；不要点名推荐任何股票。")
    try:
        text, info = llm.complete_text(system, facts)
    except llm.LLMError as e:
        return {"ok": False, "reason": str(e), "fallback": summ["text"]}
    # 守门规则：解读里出现的每个数字，都必须出现在交给它的事实材料里（含指标名里的“60 日”“2026 中报”）
    allowed = {float(x.replace(",", "")) for x in explain._NUM_RE.findall(facts)} | set(summ["numbers"])
    bad_nums = explain.number_guard(text, allowed)
    bad_words = explain.compliance_guard(text)
    if bad_nums or bad_words:
        return {"ok": False, "blocked": True, "reason": "AI 解读未通过守门器，已丢弃",
                "unsupported_numbers": bad_nums, "compliance_hits": bad_words, "draft": text,
                "fallback": summ["text"], "model": info.get("model")}
    return {"ok": True, "text": text, "model": info.get("model"), "latency_ms": info.get("latency_ms"),
            "checked": {"numbers": "全部数字都能在引擎结果里找到", "compliance": "未出现预测、承诺或买卖建议"}}


# ── 保存与监控 ──────────────────────────────────────────────────────
class SaveIn(BaseModel):
    strategy: Strategy
    name: str = ""


@app.post("/api/strategies")
def api_save(body: SaveIn, request: Request, x_client_id: str | None = Header(None)):
    cid = _client(x_client_id)
    ds = dataset(_mode(request))
    s = body.strategy.model_copy(update={"name": body.name or body.strategy.name})
    res = run_cached(ds, s)
    return monitors.save_strategy(cid, s, res, ds.mode)


@app.get("/api/strategies")
def api_list(x_client_id: str | None = Header(None)):
    return monitors.list_strategies(_client(x_client_id))


@app.delete("/api/strategies/{sid}")
def api_delete(sid: str, x_client_id: str | None = Header(None)):
    return {"deleted": monitors.delete_strategy(_client(x_client_id), sid)}


class MonitorIn(BaseModel):
    strategy_id: str


@app.post("/api/monitors")
def api_monitor_create(body: MonitorIn, x_client_id: str | None = Header(None)):
    try:
        return monitors.create_monitor(_client(x_client_id), body.strategy_id)
    except KeyError as e:
        raise HTTPException(404, detail={"message": str(e)}) from e


@app.get("/api/monitors")
def api_monitor_list(x_client_id: str | None = Header(None)):
    return monitors.list_monitors(_client(x_client_id))


@app.get("/api/monitors/{mid}/runs")
def api_monitor_runs(mid: str, x_client_id: str | None = Header(None)):
    try:
        return monitors.monitor_runs(_client(x_client_id), mid)
    except KeyError as e:
        raise HTTPException(404, detail={"message": str(e)}) from e


@app.post("/api/monitors/{mid}/run")
def api_monitor_run(mid: str, request: Request, x_client_id: str | None = Header(None)):
    cid = _client(x_client_id)
    ds = dataset(_mode(request))
    try:
        monitors.monitor_runs(cid, mid)
        return monitors.run_monitor(mid, ds, run_cached, trigger="manual")
    except KeyError as e:
        raise HTTPException(404, detail={"message": str(e)}) from e


# ── 数据构建（管理口令保护）与定时刷新 ──────────────────────────────────
@app.get("/api/data/status")
def data_status():
    ds = store.get()
    return {"build": _build_state(), "snapshot": ds.meta if ds else None, "load_error": store.load_error}


@app.post("/api/admin/build")
def admin_build(request: Request, refresh: bool = False, authorization: str | None = Header(None)):
    token = os.getenv("MINGSHAI_ADMIN_TOKEN")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, detail={"message": "需要管理口令"})
    started = _start_build(refresh=refresh)
    return {"started": started, "build": _build_state()}


MAX_UPLOAD = 200 * 1024 * 1024
_REQUIRED_TABLES = {"snapshot": {"features", "meta"}, "work": {"raw_fin", "raw_income"}}


@app.post("/api/admin/snapshot")
async def admin_upload(request: Request, kind: str = "snapshot", authorization: str | None = Header(None)):
    """运维用：上传本地构建好的快照（或取数工作库），校验表结构后原子替换。需要管理口令。"""
    token = os.getenv("MINGSHAI_ADMIN_TOKEN")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, detail={"message": "需要管理口令"})
    if kind not in _REQUIRED_TABLES:
        raise HTTPException(400, detail={"message": "kind 只能是 snapshot 或 work"})
    body = await request.body()
    if not body or len(body) > MAX_UPLOAD:
        raise HTTPException(413, detail={"message": "文件为空或超过 200MB"})
    from .data.build import WORK
    from .store import DATA_DIR, SNAPSHOT_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f"{kind}.upload.tmp"
    tmp.write_bytes(body)
    try:
        import duckdb
        con = duckdb.connect(str(tmp), read_only=True)
        tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        rows = con.execute("SELECT COUNT(*) FROM features").fetchone()[0] if kind == "snapshot" else None
        con.close()
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, detail={"message": f"不是有效的 DuckDB 文件：{e}"}) from e
    missing = _REQUIRED_TABLES[kind] - tables
    if missing or (kind == "snapshot" and rows < 50):
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, detail={"message": f"表结构不完整：缺 {sorted(missing)}，或股票数过少"})
    os.replace(tmp, SNAPSHOT_PATH if kind == "snapshot" else WORK)
    if kind == "snapshot":
        store.reload()
        _cache.clear()
    ds = store.get()
    return {"ok": True, "kind": kind, "bytes": len(body), "snapshot_id": ds.snapshot_id if ds else None,
            "load_error": store.load_error}


def _start_build(refresh: bool = False) -> bool:
    if not _build_lock.acquire(blocking=False):
        return False

    def work():
        try:
            from .data.build import build
            asyncio.run(build(refresh=refresh))
            store.reload()
            _cache.clear()
            ds = store.get()
            if ds is not None:
                monitors.run_all(ds, run_cached)
        except Exception:  # noqa: BLE001 —— 失败原因已写进 build_state.json，旧快照继续服务
            pass
        finally:
            _build_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return True


def _scheduler():
    """有 Key 时：没有快照就全量构建；之后每个交易日北京时间 16:30 后日更一次。"""
    from .data.fuyao import load_api_key
    if not load_api_key() or os.getenv("MINGSHAI_AUTO_BUILD", "1") != "1":
        return
    if store.get() is None:
        _start_build(refresh=False)
    bj = timezone(timedelta(hours=8))
    while True:
        time.sleep(600)
        now = datetime.now(bj)
        cutoff = now.replace(hour=16, minute=30, second=0, microsecond=0)   # 收盘后行情导出大致就绪
        ds = store.get()
        try:
            built = datetime.fromisoformat(ds.meta["built_at"]).astimezone(bj) if ds else None
        except (KeyError, ValueError):
            built = None
        if now.weekday() < 5 and now >= cutoff and (built is None or built < cutoff):
            _start_build(refresh=True)




# ── 前端 ──────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")

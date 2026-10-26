"""所以然 Web 服务（FastAPI）。

主链路：/api/parse（一句话 → 片段 + 澄清选项 + 默认条件）→ /api/compile（用户改选项）→
/api/check（守门 + 冲突）→ /api/screen（确定性筛选 + 敏感度）→ /api/evidence（单股证据卡）。
后续动作：/api/compare、/api/strategies（保存）、/api/monitors（转为监控）、/api/explain（AI 白话解读）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
ASSET_VERSION = hashlib.sha256((STATIC / "app.js").read_bytes() + (STATIC / "style.css").read_bytes()).hexdigest()[:12]
store = Store()
store.reload()
@asynccontextmanager
async def lifespan(_app):
    st = llm.prepare_key()
    print(f"[llm-budget] 已用 ${st['spent_usd']:.2f} / 上限 ${st['limit_usd']:g}"
          + (f"；{st['reason']}" if st["disabled"] else ""), flush=True)
    threading.Thread(target=_scheduler, daemon=True).start()
    yield


# 不公开接口文档（/docs、/redoc、/openapi.json 会把管理接口的结构也列出来）
app = FastAPI(title="所以然 · 一句话选股", version=VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2048)

# 浏览器安全响应头：只允许本站脚本、禁止被嵌入别的网页、不嗅探类型；字体样式表来自 Google Fonts
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Strict-Transport-Security": "max-age=31536000",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    if request.url.path.startswith("/api/") or request.url.path == "/":
        resp.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=0, must-revalidate")
    return resp


class AIGate:
    """AI 调用限流，防止有人刷接口把额度耗光：同一来源 10 分钟最多 15 次；全站 1 小时最多 40 次（来源 IP 可伪造，全站上限才是硬保护）。"""

    def __init__(self, per_client: int = 15, client_window: float = 600, total: int = 40, total_window: float = 3600):
        self.per_client, self.client_window, self.total, self.total_window = per_client, client_window, total, total_window
        self._lock = threading.Lock()
        self._by_client: dict[str, deque] = {}
        self._all: deque = deque()

    def take(self, client: str) -> str | None:
        """记一次调用；超限时不记，返回原因。"""
        now = time.monotonic()
        with self._lock:
            while self._all and now - self._all[0] > self.total_window:
                self._all.popleft()
            q = self._by_client.setdefault(client, deque())
            while q and now - q[0] > self.client_window:
                q.popleft()
            if len(q) >= self.per_client:
                return f"AI 调用太频繁（同一来源 {self.client_window / 60:g} 分钟最多 {self.per_client} 次）"
            if len(self._all) >= self.total:
                return f"全站 AI 调用太频繁（每 {self.total_window / 3600:g} 小时最多 {self.total} 次）"
            q.append(now)
            self._all.append(now)
            if len(self._by_client) > 5000:
                self._by_client = {k: v for k, v in self._by_client.items() if v and now - v[-1] <= self.client_window}
            return None


ai_gate = AIGate()


def _origin(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _admin_ok(authorization: str | None) -> bool:
    token = os.getenv("MINGSHAI_ADMIN_TOKEN")
    return bool(token) and secrets.compare_digest((authorization or "").encode(), f"Bearer {token}".encode())
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
    issues = check_strategy(s, set(ds.frame["industry_code"].dropna()))
    if any(i["severity"] == "error" for i in issues):
        raise HTTPException(422, detail={"issues": issues, "message": "意图或条件尚未确认，暂停执行"})
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
    info = llm.public_info()
    degraded = ds is None or store.load_warning or info["health"]["status"] in ("auth_failed", "degraded")
    # HTTP liveness remains 200 for deployment probes; readiness is explicit.
    return {"status": "degraded" if degraded else "ok", "version": VERSION,
            "snapshot_id": ds.snapshot_id if ds else None, "data_ready": ds is not None,
            "data_warning": store.load_warning, "llm": info}


@app.get("/api/meta")
def meta(request: Request):
    mode = _mode(request)
    ds = store.get(mode)
    out = {"version": VERSION, "llm": llm.public_info(), "catalog": catalog(), "build": _build_state(),
           "data_ready": ds is not None, "data_mode": mode or store.mode, "load_error": store.load_error,
           "data_warning": store.load_warning}
    if ds is not None:
        out["snapshot"] = ds.meta
        out["industries"] = sorted(ds.industries)
        out["coverage"] = ds.coverage()
        out["universe_size"] = int(len(ds.frame))
        out["data_notes"] = _data_notes(ds)
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
    pr = parse_llm.parse(body.query.strip(), ds.industries, gate=lambda: ai_gate.take(_origin(request)))
    strategy, assumptions, notes = compile_strategy(body.query, pr.segments)
    refused = [s for s in pr.segments if s.kind == "refused"]
    return {"parse": pr.model_dump(), "strategy": strategy.model_dump(), "assumptions": pr.assumptions + assumptions,
            "notes": notes, "refuse_hint": REFUSE_HINT if refused else None,
            "only_refused": bool(refused) and not strategy.conditions, "llm": llm.public_info()}


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
             "g": r["worst_gap"], "universe_missing": r.get("universe_missing", [])} for r in res["rows"]]
    return {
        "run_id": res["run_id"], "snapshot_id": res["snapshot_id"], "data_mode": res["data_mode"],
        "elapsed_ms": int((time.monotonic() - t0) * 1000), "conditions": conds,
        "universe_steps": res["universe_steps"], "universe_count": res["universe_count"],
        "counts": res["counts"], "funnel": res["funnel"], "impact": res["impact"],
        "fail_distribution": res["fail_distribution"], "rows": rows,
        "summary": explain.summary(res, s), "relax": conflicts.relax_suggestions(res, s) if res["counts"]["passed"] < 5 else [],
        "conflicts": hints, "data_sources": ds.meta.get("sources", {}), "data_notes": _data_notes(ds),
        "universe_unknown_count": res["universe_unknown_count"],
    }


def _data_notes(ds: Dataset) -> list[str]:
    p = ds.meta.get("sources", {}).get("prices", {})
    last, cal = p.get("last_date"), p.get("calendar_last_trading_day")
    notes = [n for n in ds.meta.get("notes", []) if "行情数据截至" not in n]
    if last and cal and cal > last:
        notes.append(f"行情更新到 {last} 收盘，{cal} 的行情还没发布。")
    if last and ds.mode != "demo":
        try:
            bj = timezone(timedelta(hours=8))
            age = (datetime.now(bj).date() - datetime.fromisoformat(last).date()).days
            if age > 5:
                notes.append(f"行情截至{last}，距今{age}天；当前使用历史快照，请核对交易日和数据更新状态。")
        except ValueError:
            notes.append("行情时点无法解析，请先核对数据状态。")
    if store.load_warning and ds.mode != "demo":
        notes.append(store.load_warning)
    return list(dict.fromkeys(notes))


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
    if r.get("industry_code") in s.universe.excluded_industries:
        out.append(f"所属行业「{r.get('industry_name')}」已被你剔除")
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
    """AI only selects fact IDs; trusted templates render all displayed text."""
    ds = dataset(_mode(request))
    res = run_cached(ds, body.strategy)
    summ = explain.summary(res, body.strategy)
    if not llm.available():
        return {"ok": False, "reason": llm.unavailable_reason(), "fallback": summ["text"]}
    if why := ai_gate.take(_origin(request)):
        return {"ok": False, "reason": why, "fallback": summ["text"]}
    facts = explain.explanation_facts(res, body.strategy)
    schema = {"type": "object", "properties": {"fact_ids": {"type": "array", "items": {"type": "string"}}},
              "required": ["fact_ids"], "additionalProperties": False}
    system = ("从已验证事实中选择1至3个最有助于理解结果的编号。"
              "只返回fact_ids数组，不写句子、数字、建议，不修改事实。优先概览和影响最大的条件。")
    import json
    try:
        selection, info = llm.complete_json(system, json.dumps(facts, ensure_ascii=False), schema, max_tokens=300,
                                            kind="explain")
        text, selected = explain.render_fact_selection(selection, facts)
    except (llm.LLMError, ValueError, TypeError) as e:
        return {"ok": False, "reason": str(e), "fallback": summ["text"], "blocked": True}
    return {"ok": True, "text": text, "facts": selected, "model": info.get("model"),
            "latency_ms": info.get("latency_ms"),
            "checked": {"numbers": "指标、数值、单位、期间均由程序按事实编号回填", "compliance": "只展示预先验证的事实"}}


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
    except ValueError as e:
        raise HTTPException(422, detail={"message": str(e)}) from e


# ── 数据构建（管理口令保护）与定时刷新 ──────────────────────────────────
@app.get("/api/data/status")
def data_status():
    ds = store.get()
    return {"build": _build_state(), "snapshot": ds.meta if ds else None, "load_error": store.load_error}


@app.post("/api/admin/build")
def admin_build(request: Request, refresh: bool = False, authorization: str | None = Header(None)):
    if not _admin_ok(authorization):
        raise HTTPException(401, detail={"message": "需要管理口令"})
    started = _start_build(refresh=refresh)
    return {"started": started, "build": _build_state()}


@app.post("/api/admin/repair-listings")
def admin_repair_listings(authorization: str | None = Header(None)):
    if not _admin_ok(authorization):
        raise HTTPException(401, detail={"message": "需要管理口令"})
    if not _build_lock.acquire(blocking=False):
        raise HTTPException(409, detail={"message": "数据更新进行中，请稍后重试"})
    try:
        from .data.repair import repair_listings
        result = asyncio.run(repair_listings())
        store.reload()
        _cache.clear()
        return result
    except Exception as e:
        raise HTTPException(503, detail={"message": f"上市日期核验失败，保留原始文件：{type(e).__name__}"}) from e
    finally:
        _build_lock.release()


MAX_UPLOAD = 200 * 1024 * 1024
_REQUIRED_TABLES = {"snapshot": {"features", "meta"}, "work": {"raw_fin", "raw_income"}}


@app.get("/api/admin/snapshot")
def admin_download(authorization: str | None = Header(None)):
    """Authenticated backup for diagnosis and rollback; never exposes keys or user strategies."""
    if not _admin_ok(authorization):
        raise HTTPException(401, detail={"message": "需要管理口令"})
    from .store import SNAPSHOT_PATH
    if not SNAPSHOT_PATH.is_file():
        raise HTTPException(404, detail={"message": "没有可备份的数据快照"})
    return FileResponse(SNAPSHOT_PATH, filename="snapshot.duckdb", media_type="application/octet-stream")


@app.post("/api/admin/snapshot")
async def admin_upload(request: Request, kind: str = "snapshot", authorization: str | None = Header(None)):
    """运维用：上传本地构建好的快照（或取数工作库），校验表结构后原子替换。需要管理口令。"""
    if not _admin_ok(authorization):
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
    if kind == "snapshot":
        from .store import load_snapshot
        try:
            load_snapshot(tmp)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise HTTPException(422, detail={"message": f"快照质量不合格，保留当前数据：{e}"}) from e
        if SNAPSHOT_PATH.exists():
            from .data.quality import preserve_current
            preserve_current(SNAPSHOT_PATH)
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
    # A failed listing-date contract can be repaired using a single metadata
    # request, without an expensive full rebuild or changing market timestamps.
    from .store import SNAPSHOT_PATH
    if store.load_warning and SNAPSHOT_PATH.exists() and _build_lock.acquire(blocking=False):
        try:
            from .data.repair import repair_listings
            asyncio.run(repair_listings())
            store.reload()
            _cache.clear()
        except Exception as e:
            print(f"[listing-repair] 未完成核验：{type(e).__name__}", flush=True)
        finally:
            _build_lock.release()
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
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('/static/app.js"', f'/static/app.js?v={ASSET_VERSION}"')
    html = html.replace('/static/style.css"', f'/static/style.css?v={ASSET_VERSION}"')
    return HTMLResponse(html)

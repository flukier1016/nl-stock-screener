"""保存策略与监控任务（SQLite）。

* 保存：存的是 :class:`Strategy` 的 JSON + 保存时的快照编号与入选名单，之后可以重跑对比；
* 监控：每次数据快照更新后重跑一次，记录"新进入 / 移出"名单及原因（移出的股票卡在哪个条件）；
  快照没有更新时明确记"数据未更新"，**不会**把旧结果当成新一期结果。

匿名使用：浏览器生成一个随机 client_id，只用来区分各自保存的策略，不收集任何个人信息。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime

from .compile import describe
from .dsl import Strategy
from .store import DATA_DIR

DB_PATH = DATA_DIR / "app.sqlite"
_lock = threading.Lock()


def _con() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY, client_id TEXT, name TEXT, query TEXT, strategy_json TEXT,
        created_at TEXT, snapshot_id TEXT, run_id TEXT, passed_json TEXT, counts_json TEXT, data_mode TEXT);
    CREATE TABLE IF NOT EXISTS monitors (
        id TEXT PRIMARY KEY, strategy_id TEXT, client_id TEXT, created_at TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS monitor_runs (
        id TEXT PRIMARY KEY, monitor_id TEXT, run_at TEXT, snapshot_id TEXT, run_id TEXT,
        passed_json TEXT, entered_json TEXT, exited_json TEXT, counts_json TEXT, note TEXT, trigger TEXT);
    """)
    return con


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def save_strategy(client_id: str, s: Strategy, result: dict, data_mode: str) -> dict:
    sid = uuid.uuid4().hex[:10]
    with _lock, _con() as con:
        con.execute("INSERT INTO strategies VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            sid, client_id, s.name or s.query[:40], s.query, s.model_dump_json(), _now(),
            result["snapshot_id"], result["run_id"], json.dumps(result["passed"]),
            json.dumps(result["counts"]), data_mode))
    return get_strategy(sid)


def get_strategy(sid: str) -> dict | None:
    with _con() as con:
        r = con.execute("SELECT * FROM strategies WHERE id=?", (sid,)).fetchone()
    return _row(r) if r else None


def list_strategies(client_id: str) -> list[dict]:
    with _con() as con:
        rows = con.execute("SELECT * FROM strategies WHERE client_id=? ORDER BY created_at DESC LIMIT 50",
                           (client_id,)).fetchall()
    return [_row(r) for r in rows]


def delete_strategy(client_id: str, sid: str) -> bool:
    with _lock, _con() as con:
        n = con.execute("DELETE FROM strategies WHERE id=? AND client_id=?", (sid, client_id)).rowcount
        con.execute("DELETE FROM monitors WHERE strategy_id=? AND client_id=?", (sid, client_id))
    return n > 0


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("strategy_json", "passed_json", "counts_json"):
        if k in d and d[k]:
            d[k.replace("_json", "")] = json.loads(d.pop(k))
    return d


def create_monitor(client_id: str, strategy_id: str) -> dict:
    s = get_strategy(strategy_id)
    if not s or s["client_id"] != client_id:
        raise KeyError("策略不存在")
    mid = uuid.uuid4().hex[:10]
    with _lock, _con() as con:
        con.execute("INSERT INTO monitors VALUES (?,?,?,?,1)", (mid, strategy_id, client_id, _now()))
        # 第一条记录就是保存时的基线
        con.execute("INSERT INTO monitor_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            uuid.uuid4().hex[:10], mid, s["created_at"], s["snapshot_id"], s["run_id"],
            json.dumps(s["passed"]), "[]", "[]", json.dumps(s["counts"]), "基线（保存策略时的结果）", "baseline"))
    return {"id": mid, "strategy_id": strategy_id}


def list_monitors(client_id: str) -> list[dict]:
    with _con() as con:
        rows = con.execute("""SELECT m.*, s.name, s.query FROM monitors m JOIN strategies s ON s.id=m.strategy_id
                              WHERE m.client_id=? AND m.active=1 ORDER BY m.created_at DESC""", (client_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            last = con.execute("SELECT * FROM monitor_runs WHERE monitor_id=? ORDER BY run_at DESC LIMIT 1",
                               (r["id"],)).fetchone()
            d["last_run"] = _run(last) if last else None
            out.append(d)
    return out


def monitor_runs(client_id: str, mid: str) -> list[dict]:
    with _con() as con:
        m = con.execute("SELECT * FROM monitors WHERE id=? AND client_id=?", (mid, client_id)).fetchone()
        if not m:
            raise KeyError("监控不存在")
        rows = con.execute("SELECT * FROM monitor_runs WHERE monitor_id=? ORDER BY run_at DESC LIMIT 30",
                           (mid,)).fetchall()
    return [_run(r) for r in rows]


def _run(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("passed_json", "entered_json", "exited_json", "counts_json"):
        d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
    return d


def run_monitor(mid: str, ds, engine_run, trigger: str = "manual") -> dict:
    """engine_run(ds, strategy) -> result；由 app 注入，避免循环依赖。"""
    with _con() as con:
        m = con.execute("SELECT * FROM monitors WHERE id=?", (mid,)).fetchone()
        if not m:
            raise KeyError("监控不存在")
        s = get_strategy(m["strategy_id"])
        prev = con.execute("SELECT * FROM monitor_runs WHERE monitor_id=? ORDER BY run_at DESC LIMIT 1",
                           (mid,)).fetchone()
    strategy = Strategy.model_validate(s["strategy"])
    if prev and prev["snapshot_id"] == ds.snapshot_id:
        note = f"数据快照 {ds.snapshot_id} 与上次相同，没有新数据，名单不变（未重复计算）。"
        rec = {"passed": json.loads(prev["passed_json"]), "entered": [], "exited": [],
               "counts": json.loads(prev["counts_json"]), "run_id": prev["run_id"]}
    else:
        res = engine_run(ds, strategy)
        old = set(json.loads(prev["passed_json"])) if prev else set()
        new = set(res["passed"])
        rows = {r["thscode"]: r for r in res["rows"]}
        conds = {c.id: c for c in strategy.conditions}
        entered = [{"thscode": c, "name": rows[c]["name"]} for c in sorted(new - old) if c in rows]
        exited = []
        for c in sorted(old - new):
            r = rows.get(c)
            if r is None:
                exited.append({"thscode": c, "name": c, "why": "已不在股票池（可能被调出指数、被 ST 或停牌缺数据）"})
            else:
                why = [f"不再满足“{describe(conds[i].metric, conds[i].op, conds[i].value, conds[i].basis)}”"
                       for i in r["fails"]] or ["相关数据缺失，无法判定"]
                exited.append({"thscode": c, "name": r["name"], "why": "；".join(why)})
        note = f"快照 {ds.snapshot_id}：新进入 {len(entered)} 只，移出 {len(exited)} 只。"
        rec = {"passed": res["passed"], "entered": entered, "exited": exited, "counts": res["counts"],
               "run_id": res["run_id"]}
    with _lock, _con() as con:
        con.execute("INSERT INTO monitor_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            uuid.uuid4().hex[:10], mid, _now(), ds.snapshot_id, rec["run_id"], json.dumps(rec["passed"]),
            json.dumps(rec["entered"], ensure_ascii=False), json.dumps(rec["exited"], ensure_ascii=False),
            json.dumps(rec["counts"]), note, trigger))
    return {"note": note, **{k: rec[k] for k in ("entered", "exited", "counts")}}


def run_all(ds, engine_run, trigger: str = "data_refresh") -> int:
    with _con() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM monitors WHERE active=1").fetchall()]
    for mid in ids:
        try:
            run_monitor(mid, ds, engine_run, trigger)
        except Exception:  # noqa: BLE001 —— 单个监控失败不影响其他监控
            continue
    return len(ids)

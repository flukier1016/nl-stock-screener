"""扶摇（同花顺金融数据 API）客户端。

取数纪律（沿用熊猫债 iFinD 取数的做法）：

* Key 只从环境变量或本地文件读，不打印、不写日志、不进仓库；
* 每次请求（含重试）都写进 JSONL 台账：接口、参数、HTTP 状态、业务 code、
  request_id、条数、耗时。结论里的数字能追到具体哪一次请求；
* 只对限流和上游暂时不可用（HTTP 429、code 4001/5002/5003）做有限次退避重试，
  其他错误原样抛出，由调用方决定记为缺失还是终止，**不吞错误**；
* 并发由信号量 + 最小请求间隔控制，遇到限流自动放慢。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from pathlib import Path

import httpx

BASE_URL = os.getenv("FUYAO_BASE_URL", "https://fuyao.aicubes.cn")
RETRYABLE_CODES = {4001, 5002, 5003}


class FuyaoError(Exception):
    def __init__(self, path: str, code: int | None, message: str, request_id: str | None = None,
                 http_status: int | None = None):
        super().__init__(f"{path}: code={code} http={http_status} {message} (request_id={request_id})")
        self.path, self.code, self.message = path, code, message
        self.request_id, self.http_status = request_id, http_status


def load_api_key() -> str | None:
    for var in ("FUYAO_API_KEY", "HITHINK_FINANCE_API_KEY"):
        if os.getenv(var):
            return os.environ[var].strip()
    f = os.getenv("FUYAO_API_KEY_FILE")
    if f and Path(f).expanduser().exists():
        return Path(f).expanduser().read_text(encoding="utf-8").strip()
    return None


class FuyaoClient:
    def __init__(self, api_key: str, ledger: Path | None = None, concurrency: int = 6,
                 min_interval: float = 0.08, timeout: float = 30.0, max_attempts: int = 5):
        if not api_key:
            raise ValueError("缺少扶摇 API Key（FUYAO_API_KEY / FUYAO_API_KEY_FILE）")
        self._key = api_key
        self.ledger = ledger
        self.sem = asyncio.Semaphore(concurrency)
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self._last = 0.0
        self._pace_lock = asyncio.Lock()
        self.stats = {"requests": 0, "ok": 0, "retries": 0, "errors": 0, "throttled": 0}
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=timeout,
                                       headers={"X-api-key": api_key, "Accept": "application/json"})

    async def close(self) -> None:
        await self._http.aclose()

    async def _pace(self) -> None:
        async with self._pace_lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    def _log(self, rec: dict) -> None:
        if self.ledger is None:
            return
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async def get(self, path: str, params: dict | None = None) -> dict:
        """返回完整信封（code=0）。失败抛 FuyaoError。"""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(1, self.max_attempts + 1):
            async with self.sem:
                await self._pace()
                t0 = time.monotonic()
                http_status, code, msg, rid, n = None, None, "", None, None
                try:
                    resp = await self._http.get(path, params=params)
                    http_status = resp.status_code
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    code = body.get("code") if isinstance(body, dict) else None
                    msg = (body.get("message") if isinstance(body, dict) else "") or ""
                    rid = body.get("request_id") if isinstance(body, dict) else None
                    data = body.get("data") if isinstance(body, dict) else None
                    if isinstance(data, dict) and isinstance(data.get("item"), list):
                        n = len(data["item"])
                except (httpx.TransportError, ValueError) as e:
                    msg = f"{type(e).__name__}: {e}"
                elapsed = int((time.monotonic() - t0) * 1000)
            self.stats["requests"] += 1
            self._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": path, "params": params,
                       "http": http_status, "code": code, "message": msg[:200], "request_id": rid,
                       "items": n, "ms": elapsed, "attempt": attempt})
            if http_status == 200 and code == 0:
                self.stats["ok"] += 1
                return body
            throttled = http_status == 429 or code == 4001
            retryable = throttled or code in RETRYABLE_CODES or http_status is None or (http_status or 0) >= 500
            if throttled:
                self.stats["throttled"] += 1
                self.min_interval = min(self.min_interval * 1.5, 2.0)
            if retryable and attempt < self.max_attempts:
                self.stats["retries"] += 1
                await asyncio.sleep(min(2 ** attempt, 20) * (0.5 + random.random()))
                continue
            self.stats["errors"] += 1
            raise FuyaoError(path, code, msg or "请求失败", rid, http_status)
        raise FuyaoError(path, None, "超过最大重试次数")

    # ── 具体接口 ────────────────────────────────────────────────────
    async def tickers(self, asset_type: str = "a-share") -> tuple[list[dict], dict]:
        items, offset, limit = [], 0, 10000
        env = None
        while True:
            env = await self.get("/api/meta/tickers/list",
                                 {"asset_type": asset_type, "limit": limit, "offset": offset})
            batch = env["data"]["item"]
            items += batch
            if len(batch) < limit:
                break
            offset += limit
        return items, env

    async def industry_catalog(self, tag: str = "industry") -> dict:
        return await self.get("/api/a-share-index/catalog/ths-index-list", {"tag": tag})

    async def constituents(self, thscode: str) -> dict:
        return await self.get("/api/a-share-index/constituents/ths-stock-list", {"thscode": thscode})

    async def valuations(self, thscodes: list[str]) -> dict:
        return await self.get("/api/a-share/valuations/snapshot", {"thscodes": ",".join(thscodes)})

    async def fin_indicators(self, thscode: str, report: str) -> dict:
        return await self.get("/api/a-share/financials/indicators", {"thscode": thscode, "report": report})

    async def calendar(self) -> dict:
        return await self.get("/api/a-share/calendar/trading-days")

    async def index_history(self, thscode: str, start_ms: int, end_ms: int) -> dict:
        return await self.get("/api/a-share-index/prices/historical",
                              {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms})

    async def dump_url(self, kind: str) -> dict:
        """kind: daily-k / daily-k-10d / adjustment-factors。返回短时有效的预签名链接。"""
        return await self.get(f"/api/dump/market-dumps/{kind}/download-url")

    async def download(self, url: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as raw:
            async with raw.stream("GET", url) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1 << 20):
                        fh.write(chunk)
                        size += len(chunk)
        tmp.replace(dest)
        self._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": "download", "params": {"dest": dest.name},
                   "http": 200, "code": 0, "bytes": size})
        return size

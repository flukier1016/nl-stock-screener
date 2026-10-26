"""条件契约：大模型、规则解析器、前端和引擎之间只用这几个结构说话。

用户在页面上看到、改动、保存的都是 :class:`Strategy`。它是纯数据，可以序列化、
比较、哈希（同一策略 + 同一数据快照 → 同一个 ``run_id``），不含任何代码或 SQL。
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

Op = Literal["<=", ">=", "<", ">", "between"]
Basis = Literal["absolute", "industry_median", "market_median"]
SegmentKind = Literal[
    "concept", "explicit", "universe", "industry", "exclude_industry", "exclude", "unsupported", "refused",
]
ENGINE_VERSION = "screen-v2"


class Condition(BaseModel):
    id: str
    metric: str
    op: Op
    value: float | list[float]
    basis: Basis = "absolute"
    concept_id: str | None = None
    option_id: str | None = None
    label: str = ""
    origin: Literal["concept", "explicit", "user"] = "concept"
    source_text: str = ""


class UniverseSpec(BaseModel):
    base: str = "all_a"
    industries: list[str] = Field(default_factory=list)       # 同花顺行业指数 thscode，多选取并集
    excluded_industries: list[str] = Field(default_factory=list)
    exclude_st: bool = True
    min_listed_days: int = 365
    exclude_bj: bool = False


class IntentIssue(BaseModel):
    code: str
    text: str
    message: str


class Strategy(BaseModel):
    name: str = ""
    query: str = ""
    universe: UniverseSpec = Field(default_factory=UniverseSpec)
    conditions: list[Condition] = Field(default_factory=list)
    near_miss_tolerance: float = 0.20
    intent_issues: list[IntentIssue] = Field(default_factory=list)

    def canonical(self) -> str:
        """决定结果的部分（股票池 + 条件）的规范 JSON；名称和原话不影响结果。"""
        body = {
            "universe": self.universe.model_dump(),
            "conditions": [
                {k: c.model_dump()[k] for k in ("metric", "op", "value", "basis")}
                for c in self.conditions
            ],
            "near_miss_tolerance": self.near_miss_tolerance,
        }
        return json.dumps(body, ensure_ascii=False, sort_keys=True)

    def fingerprint(self, snapshot_id: str) -> str:
        raw = f"{ENGINE_VERSION}|{self.canonical()}|{snapshot_id}".encode()
        return hashlib.sha256(raw).hexdigest()[:12]


class Segment(BaseModel):
    """用户原话里的一个片段，以及它被理解成了什么。"""

    text: str
    kind: SegmentKind
    concept_id: str | None = None
    option_id: str | None = None
    metric: str | None = None
    op: Op | None = None
    value: float | list[float] | None = None
    basis: Basis = "absolute"
    universe_id: str | None = None
    industry: str | None = None          # 行业名（解析阶段）
    industry_code: str | None = None     # 行业指数 thscode（对上行业表后）
    reason: str = ""
    by: Literal["rules", "llm"] = "rules"


class ParseResult(BaseModel):
    query: str
    mode: Literal["llm", "rules", "rules_fallback"]
    model: str | None = None
    segments: list[Segment] = Field(default_factory=list)
    blocked: list[dict] = Field(default_factory=list)      # 被守门器拦下的大模型输出
    assumptions: list[str] = Field(default_factory=list)
    fallback_reason: str = ""
    latency_ms: int | None = None
    rules_shadow: list[Segment] | None = None              # 同一句话的规则解析，作对照

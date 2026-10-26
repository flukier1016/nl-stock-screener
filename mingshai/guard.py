"""守门器：大模型和用户输入进入引擎之前的唯一入口。

两类检查：

* :func:`check_segment` —— 大模型（或规则）解析出的每个片段，只能引用口径字典里
  存在的概念、方案、指标、股票池和行业；数值必须在指标的合法范围内。不合格的片段
  **整条拦下并记录原因**，不做"帮它改对"的静默修复。
* :func:`check_strategy` —— 用户改过的条件在运行前再查一遍。有 error 就不运行，
  前端直接展示原因。

守门器只管"合法"，"合理"（冲突、张力、覆盖率）交给 :mod:`mingshai.conflicts`。
"""
from __future__ import annotations

import math

from .dsl import Condition, Segment, Strategy
from .intent import inspect_intent
from .registry import BASES, CONCEPTS_BY_ID, METRICS_BY_ID, OPS, UNIVERSES_BY_ID

MAX_CONDITIONS = 15
EXCLUDE_FLAGS = {"st", "new", "bj"}
RELATIVE_MULT_RANGE = (0.1, 10.0)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_value(metric_id: str, op: str, value, basis: str = "absolute") -> str | None:
    """返回错误原因；合法返回 None。"""
    m = METRICS_BY_ID.get(metric_id)
    if m is None:
        return f"口径字典里没有指标 {metric_id!r}"
    if op not in OPS:
        return f"不支持的比较符 {op!r}"
    if basis not in BASES:
        return f"不支持的阈值基准 {basis!r}"
    if basis != "absolute":
        if not m.relative_ok:
            return f"{m.name} 不能用相对阈值"
        if op == "between":
            return "相对阈值不支持区间写法，请拆成两条"
        if not _num(value) or not (RELATIVE_MULT_RANGE[0] <= value <= RELATIVE_MULT_RANGE[1]):
            return f"相对阈值的倍数需在 {RELATIVE_MULT_RANGE[0]}～{RELATIVE_MULT_RANGE[1]} 之间"
        return None
    if op == "between":
        if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(_num(v) for v in value)):
            return "区间条件需要两个数字"
        lo, hi = value
        if lo >= hi:
            return f"区间下限 {lo} 不小于上限 {hi}"
        vals = [lo, hi]
    else:
        if not _num(value):
            return "阈值必须是数字"
        vals = [value]
    for v in vals:
        if not (m.lo <= v <= m.hi):
            return f"{m.name} 的阈值 {v} 超出合法范围 [{m.lo:g}, {m.hi:g}] {m.unit}"
    return None


def check_segment(seg: Segment, industries: dict[str, str]) -> tuple[Segment | None, str | None]:
    """industries: 行业名 → 同花顺行业指数 thscode。"""
    k = seg.kind
    if k == "concept":
        c = CONCEPTS_BY_ID.get(seg.concept_id or "")
        if c is None:
            return None, f"口径字典里没有概念 {seg.concept_id!r}"
        if c.universe:
            return seg.model_copy(update={"kind": "universe", "universe_id": c.universe}), None
        if seg.option_id and c.option(seg.option_id) is None:
            return None, f"概念“{c.label}”没有方案 {seg.option_id!r}"
        opt = seg.option_id or (c.default_option.id if c.default_option else None)
        return seg.model_copy(update={"option_id": opt}), None
    if k == "explicit":
        err = check_value(seg.metric or "", seg.op or "", seg.value, seg.basis)
        return (None, err) if err else (seg, None)
    if k == "universe":
        if seg.universe_id not in UNIVERSES_BY_ID:
            return None, f"没有股票池 {seg.universe_id!r}"
        return seg, None
    if k in ("industry", "exclude_industry"):
        name = (seg.industry or "").strip()
        code = resolve_industry(name, industries)
        if code is None:
            return seg.model_copy(update={
                "kind": "unsupported",
                "reason": f"没有找到名为“{name}”的同花顺行业，请用支持的行业名称改写后重新解析",
            }), None
        return seg.model_copy(update={"industry_code": code}), None
    if k == "exclude":
        if seg.metric not in EXCLUDE_FLAGS:
            return None, f"不认识的剔除项 {seg.metric!r}"
        return seg, None
    if k in ("unsupported", "refused"):
        if not seg.reason:
            return seg.model_copy(update={"reason": "无法转为数据条件"}), None
        return seg, None
    return None, f"未知片段类型 {k!r}"


def resolve_industry(name: str, industries: dict[str, str]) -> str | None:
    """行业名 → thscode。先精确，再"行业名包含用户说的词"，有多个时取名字最短的。"""
    if not name:
        return None
    if name in industries:
        return industries[name]
    hits = [n for n in industries if name in n]
    if not hits:
        hits = [n for n in industries if n in name and len(n) >= 2]
    if not hits:
        return None
    return industries[min(hits, key=len)]


def check_condition(c: Condition) -> str | None:
    return check_value(c.metric, c.op, c.value, c.basis)


def check_strategy(s: Strategy, known_industries: set[str] | None = None) -> list[dict]:
    """返回 [{severity, condition_id, message}]；severity=error 时不允许运行。"""
    issues: list[dict] = []
    intent_issues = list(s.intent_issues) + inspect_intent(s.query)
    for issue in {i.code + i.text: i for i in intent_issues}.values():
        issues.append({"severity": "error", "condition_id": None, "code": issue.code,
                       "message": issue.message, "source_text": issue.text})
    if s.universe.base not in UNIVERSES_BY_ID:
        issues.append({"severity": "error", "condition_id": None,
                       "message": f"没有股票池 {s.universe.base!r}"})
    if known_industries is not None:
        for code in s.universe.industries + s.universe.excluded_industries:
            if code not in known_industries:
                issues.append({"severity": "error", "condition_id": None,
                               "message": f"行业代码 {code} 不在当前数据快照里"})
    if set(s.universe.industries) & set(s.universe.excluded_industries):
        issues.append({"severity": "error", "condition_id": None,
                       "message": "同一个行业同时被保留和剔除，请先解决冲突。"})
    if len(s.conditions) > MAX_CONDITIONS:
        issues.append({"severity": "error", "condition_id": None,
                       "message": f"条件最多 {MAX_CONDITIONS} 条，现在有 {len(s.conditions)} 条"})
    seen: set[str] = set()
    for c in s.conditions:
        if c.id in seen:
            issues.append({"severity": "error", "condition_id": c.id, "message": "条件编号重复"})
        seen.add(c.id)
        err = check_condition(c)
        if err:
            issues.append({"severity": "error", "condition_id": c.id, "message": err})
    if not (0 <= s.near_miss_tolerance <= 1):
        issues.append({"severity": "error", "condition_id": None,
                       "message": "“差一点”容忍度需在 0～1 之间"})
    return issues

"""把解析片段和用户在澄清面板里的选择，编译成可执行的 :class:`Strategy`。

这一步完全确定：同样的片段 + 同样的选择 → 同样的条件列表。用户之后在条件表里
改阈值、删条件、加条件，改的都是这里产出的 Strategy。
"""
from __future__ import annotations

from .dsl import Condition, Segment, Strategy, UniverseSpec
from .registry import CONCEPTS_BY_ID, METRICS_BY_ID, REPORT_LABELS, REPORT_LATEST, UNIVERSES_BY_ID

SKIP = "__skip__"
OP_TEXT = {"<=": "≤", ">=": "≥", "<": "<", ">": ">", "between": "介于"}


def describe(metric_id: str, op: str, value, basis: str = "absolute") -> str:
    m = METRICS_BY_ID[metric_id]
    if basis == "industry_median":
        v = "行业中位数" if value == 1 else f"行业中位数 × {value:g}"
        return f"{m.name} {OP_TEXT[op]} {v}"
    if basis == "market_median":
        v = "全市场中位数" if value == 1 else f"全市场中位数 × {value:g}"
        return f"{m.name} {OP_TEXT[op]} {v}"
    if op == "between":
        return f"{m.name} 介于 {value[0]:g}～{value[1]:g}{m.unit}"
    return f"{m.name} {OP_TEXT[op]} {value:g}{m.unit}"


def compile_strategy(query: str, segments: list[Segment], choices: dict[int, str] | None = None,
                     ) -> tuple[Strategy, list[str], list[dict]]:
    """返回 (策略, 默认假设, 编译期提示)。choices: 片段序号 → 方案 id 或 SKIP。"""
    choices = choices or {}
    notes: list[dict] = []
    universe = UniverseSpec()
    universes_seen: list[tuple[str, str]] = []
    conds: list[Condition] = []
    sig: set[tuple] = set()

    def push(metric, op, value, basis, **kw):
        key = (metric, op, str(value), basis)
        if key in sig:
            return
        sig.add(key)
        conds.append(Condition(id=f"c{len(conds) + 1}", metric=metric, op=op, value=value,
                               basis=basis, **kw))

    for i, seg in enumerate(segments):
        choice = choices.get(i)
        if choice == SKIP:
            continue
        if seg.kind == "universe" and seg.universe_id:
            universes_seen.append((seg.universe_id, seg.text))
        elif seg.kind == "industry" and seg.industry_code:
            if seg.industry_code not in universe.industries:
                universe.industries.append(seg.industry_code)
        elif seg.kind == "exclude":
            if seg.metric == "st":
                universe.exclude_st = True
            elif seg.metric == "new":
                universe.min_listed_days = max(universe.min_listed_days, 365)
            elif seg.metric == "bj":
                universe.exclude_bj = True
        elif seg.kind == "concept":
            c = CONCEPTS_BY_ID[seg.concept_id]
            opt = c.option(choice) if choice else None
            opt = opt or c.option(seg.option_id or "") or c.default_option
            if opt is None:
                continue
            for t in opt.conds:
                value = list(t.value) if isinstance(t.value, tuple) else t.value
                push(t.metric, t.op, value, t.basis, concept_id=c.id, option_id=opt.id,
                     label=c.label, origin="concept", source_text=seg.text)
        elif seg.kind == "explicit" and seg.metric:
            push(seg.metric, seg.op, seg.value, seg.basis, label="你写明的条件",
                 origin="explicit", source_text=seg.text)

    if universes_seen:
        ids = list(dict.fromkeys(u for u, _ in universes_seen))
        universe.base = ids[0]
        if len(ids) > 1:
            names = "、".join(UNIVERSES_BY_ID[u].label for u in ids)
            notes.append({"severity": "conflict", "condition_ids": [],
                          "message": f"同时提到了 {names}，两者没有重叠，先按 {UNIVERSES_BY_ID[ids[0]].label} 处理。"})

    u = UNIVERSES_BY_ID[universe.base]
    assumptions = [
        f"股票池：{u.label}" + (f"（{u.note}）" if u.note and universe.base != "all_a" else ""),
        "默认剔除 ST 和上市不满一年的新股，可在下方修改。",
        f"财务数据统一用 {REPORT_LABELS[REPORT_LATEST]}（三季报还在陆续发布）。",
    ]
    if any(c.basis != "absolute" for c in conds):
        assumptions.append("“行业中位数”按同花顺行业计算，可比公司不足 5 家时记为缺失。")
    strategy = Strategy(name=query[:40], query=query, universe=universe, conditions=conds)
    return strategy, assumptions, notes

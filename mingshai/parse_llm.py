"""大模型意图解析：把一句模糊的话，映射到口径字典里的概念、方案、指标。

大模型能做的事被限定为"在目录里挑选"：

* 输出被 JSON Schema 约束；每个片段再过一遍 :func:`mingshai.guard.check_segment`，
  引用了目录外的 id、数值越界的片段整条拦下，原因展示给用户；
* 不允许输出任何股票代码、公司名或关于具体公司的数字——选股由确定性引擎完成；
* 同一句话同时跑规则解析作为对照，前端可以展开看两者差异（AI 验证记录用的也是它）。

大模型不可用、超时、返回非法 JSON 时，自动退回规则解析，并在页面上写明原因。
"""
from __future__ import annotations

import json

from . import llm
from .dsl import ParseResult, Segment
from .guard import check_segment
from .parse_rules import parse as rule_parse
from .registry import CONCEPTS, METRICS, REFUSE_HINT, UNIVERSES

SCHEMA = {
    "type": "object",
    "properties": {
        "paraphrase": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["concept", "explicit", "universe", "industry",
                                                        "exclude", "unsupported", "refused"]},
                    "concept_id": {"type": ["string", "null"]},
                    "option_id": {"type": ["string", "null"]},
                    "metric": {"type": ["string", "null"]},
                    "op": {"anyOf": [{"type": "string", "enum": ["<=", ">=", "<", ">", "between"]}, {"type": "null"}]},
                    "value": {"type": ["number", "null"]},
                    "value_hi": {"type": ["number", "null"]},
                    "basis": {"type": "string", "enum": ["absolute", "industry_median", "market_median"]},
                    "universe_id": {"type": ["string", "null"]},
                    "industry": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
                "required": ["text", "kind", "concept_id", "option_id", "metric", "op", "value", "value_hi",
                             "basis", "universe_id", "industry", "reason"],
                "additionalProperties": False,
            },
        },
        "clarify": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["paraphrase", "segments", "clarify"],
    "additionalProperties": False,
}


def _catalog_text(industries: list[str]) -> str:
    lines = ["## 模糊概念（concept_id → 可选方案 option_id，★ 为推荐）"]
    for c in CONCEPTS:
        if c.universe:
            lines.append(f"- {c.id}「{c.label}」同义：{'、'.join(c.synonyms)} → 这是股票池概念，输出 kind=universe, universe_id={c.universe}")
            continue
        opts = "；".join(f"{'★' if o.recommended else ''}{o.id}={o.label}" for o in c.options)
        lines.append(f"- {c.id}「{c.label}」同义：{'、'.join(c.synonyms)}｜方案：{opts}")
    lines.append("\n## 指标（metric，用于用户写明了数字的条件）")
    for m in METRICS:
        lines.append(f"- {m.id}：{m.name}（{m.unit}；{m.definition}）")
    lines.append("\n## 股票池（universe_id）")
    for u in UNIVERSES:
        lines.append(f"- {u.id}：{u.label}")
    lines.append("\n## 同花顺行业（industry 只能从这里原样抄名称）")
    lines.append("、".join(industries) if industries else "（当前快照没有行业表）")
    return "\n".join(lines)


SYSTEM_TMPL = """你是“所以然”选股产品的意图解析器。你的唯一工作：把用户一句关于选股的话，切成片段，
并把每个片段映射到下面目录里的 id。真正的筛选和所有数字由确定性引擎完成，不由你完成。

规则：
1. 只能使用目录里出现的 concept_id / option_id / metric / universe_id / 行业名称。目录里没有的，
   输出 kind=unsupported，并在 reason 里用一句话说明为什么无法转成数据条件、可以怎么换说法。
2. 预测涨跌、承诺收益、要买卖建议、打听目标价或内幕的片段，输出 kind=refused，reason 写明类别
   （预测未来涨跌 / 收益承诺 / 直接买卖建议 / 价格预测或非公开信息）。
3. 模糊说法（如“经营改善”“估值合理”）输出 kind=concept；option_id 默认选 ★ 推荐方案，
   只有当用户原话明显更贴近另一个方案时才换，并在 reason 里说明理由。
4. 用户写明了数字的条件输出 kind=explicit：填 metric、op、value（区间用 op=between，value=下限，value_hi=上限）。
   “ROE”一律对应 roe_ann（中报年化），reason 里注明。百分数只写数字（15% → 15）。
5. 行业输出 kind=industry，industry 填目录里最接近的行业名称原文；大盘/中盘/小盘用 kind=universe。
6. “剔除 ST / 次新 / 北交所”输出 kind=exclude，metric 分别填 st / new / bj。
7. 不要输出任何股票代码、公司名称，也不要编造任何关于具体公司的数字。
8. text 字段必须是用户原话里的连续片段；没有用到的字段填 null，basis 默认 absolute。
9. paraphrase：用一句中文复述你理解的选股意图；clarify：最多 3 个需要用户确认的问题，没有就给空数组。
只输出一个 JSON 对象，格式：
{{"paraphrase": "…", "clarify": ["…"], "segments": [{{"text": "…", "kind": "concept", "concept_id": "…", "option_id": "…",
  "metric": null, "op": null, "value": null, "value_hi": null, "basis": "absolute", "universe_id": null, "industry": null, "reason": "…"}}]}}

{catalog}
"""


def parse(query: str, industries: dict[str, str]) -> ParseResult:
    shadow = [s.model_copy(update={"by": "rules"}) for s in _guarded(rule_parse(query, industries), industries)[0]]
    # 合规前置：整句只有预测涨跌、承诺收益、买卖建议这类内容时，规则层直接拒绝，不调用大模型
    meaningful = [s for s in shadow if s.kind != "unsupported"]
    if meaningful and all(s.kind == "refused" for s in meaningful):
        return ParseResult(query=query, mode="rules", segments=shadow,
                           fallback_reason="合规拦截：这句话只有预测或建议类内容，在调用大模型之前就被规则层拒绝")
    if not llm.available():
        return ParseResult(query=query, mode="rules", segments=shadow, rules_shadow=None,
                           fallback_reason=f"{llm.unavailable_reason()}，使用规则解析")
    system = SYSTEM_TMPL.format(catalog=_catalog_text(sorted(industries)))
    try:
        data, info = llm.complete_json(system, f"用户原话：{query}", SCHEMA)
    except llm.BudgetError as e:
        return ParseResult(query=query, mode="rules_fallback", segments=shadow, fallback_reason=f"{e}，已改用规则解析")
    except llm.LLMError as e:
        return ParseResult(query=query, mode="rules_fallback", segments=shadow,
                           fallback_reason=f"大模型解析失败（{e}），已改用规则解析")
    raw, pre_blocked = [], []
    for item in data.get("segments", []):
        value = item.get("value")
        if item.get("op") == "between" and value is not None and item.get("value_hi") is not None:
            value = [float(value), float(item["value_hi"])]
        try:
            raw.append(Segment(
                text=str(item.get("text", ""))[:60], kind=item.get("kind"), concept_id=item.get("concept_id"),
                option_id=item.get("option_id"), metric=item.get("metric"), op=item.get("op"), value=value,
                basis=item.get("basis") or "absolute", universe_id=item.get("universe_id"),
                industry=item.get("industry"), reason=str(item.get("reason", ""))[:200], by="llm",
            ))
        except Exception as e:  # noqa: BLE001 —— 结构不合法的片段也要记录，而不是丢掉
            pre_blocked.append({"segment": item, "reason": f"结构不合法：{e}"})
    segs, blocked = _guarded(raw, industries)
    blocked = pre_blocked + blocked
    if query.strip() and not segs and not blocked:
        return ParseResult(query=query, mode="rules_fallback", segments=shadow,
                           fallback_reason="大模型没有解析出任何片段，已改用规则解析")
    for s in segs:
        if s.kind == "refused" and not s.reason:
            s.reason = REFUSE_HINT
    return ParseResult(
        query=query, mode="llm", model=info.get("model"), segments=segs, blocked=blocked,
        latency_ms=info.get("latency_ms"), rules_shadow=shadow,
        assumptions=[f"AI 复述：{data.get('paraphrase', '')}"] + [f"待确认：{q}" for q in data.get("clarify", [])[:3]],
    )


def _guarded(segs: list[Segment], industries: dict[str, str]) -> tuple[list[Segment], list[dict]]:
    ok, blocked = [], []
    for s in segs:
        fixed, err = check_segment(s, industries)
        if err:
            blocked.append({"segment": json.loads(s.model_dump_json()), "reason": err})
        elif fixed is not None:
            ok.append(fixed)
    return ok, blocked

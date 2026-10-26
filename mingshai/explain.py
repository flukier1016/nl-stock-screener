"""解释层：每只股票为什么入选、为什么被排除，以及整体结果的摘要。

* **证据卡完全由模板生成**：数值、阈值、单位、报告期、来源接口、字段、request_id
  都直接取自快照，大模型不参与。
* 每张卡分三栏：**事实**（数据本身）/ **推断**（按用户选的口径得出的判断）/
  **不确定**（缺失、口径局限、时点差异），对应题目"事实、推断与不确定信息必须区分"。
* 大模型只选择已验证事实的编号；模板按编号回填指标、数值、单位、期间。
  不接收模型生成的展示文案。旧number_guard保留作历史兼容工具，不作为事实归属的保证。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .compile import OP_TEXT, describe
from .dsl import Strategy
from .registry import (
    CONCEPTS_BY_ID,
    METRICS_BY_ID,
    REFUSE_PATTERNS,
    REPORT_LABELS,
    REPORT_LAST_YEAR,
    REPORT_LATEST,
    REPORT_PREV_Q,
)
from .store import Dataset

STATUS_TEXT = {"pass": "满足", "fail": "不满足", "missing": "数据缺失"}
CATEGORY_TEXT = {"passed": "入选", "near_miss": "差一点", "excluded": "被排除", "missing": "数据缺失"}


def fmt(v, metric_id: str | None = None, unit: bool = True) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    m = METRICS_BY_ID.get(metric_id) if metric_id else None
    d = m.decimals if m else 2
    s = f"{v:,.{d}f}"
    return s + (m.unit if (m and unit) else "")


def _raw_trace(ds: Dataset, code: str, metric: str) -> str:
    """派生指标展开到原始字段。"""
    r = ds.frame.loc[code]
    L, Q, Y = (REPORT_LABELS[x] for x in (REPORT_LATEST, REPORT_PREV_Q, REPORT_LAST_YEAR))
    g = lambda k: fmt(r.get(k), None, unit=False)  # noqa: E731
    yi = lambda k: "—" if pd.isna(r.get(k)) else f"{r.get(k) / 1e8:,.2f} 亿元"  # noqa: E731
    traces = {
        "rev_yoy": f"2026 上半年营收 {yi('rev_h1_26')} ÷ 2025 上半年 {yi('rev_h1_25')} − 1",
        "np_yoy": f"2026 上半年归母净利润 {yi('np_h1_26')}，2025 上半年 {yi('np_h1_25')}；(今年 − 去年) ÷ |去年|",
        "rev_yoy_accel": (f"二季度单季营收 {yi('rev_q2_26')}（上年 {yi('rev_q2_25')}，同比 {g('rev_q2_yoy')}%）；"
                          f"一季度 {yi('rev_q1_26')}（上年 {yi('rev_q1_25')}，同比 {g('rev_q1_yoy')}%）"),
        "rev_yoy_vs_ly": f"今年上半年营收同比 {g('rev_yoy')}% − 去年上半年同比 {g('rev_yoy_ly')}%（2024 上半年营收 {yi('rev_h1_24')}）",
        "np_yoy_vs_ly": f"今年上半年归母净利润同比 {g('np_yoy')}% − 去年上半年同比 {g('np_yoy_ly')}%（2024 上半年 {yi('np_h1_24')}）",
        "roe_ann": f"{L}加权 ROE 原值 {g('roe_raw')}%（半年、未年化）× 2",
        "roe_delta": f"{L} ROE {g('roe_raw')}% − {Y} ROE {g('roe_ly')}%",
        "gm_delta": f"{L}毛利率 {g('gross_margin')}% − {Y}毛利率 {g('gm_ly')}%",
        "div_yield": f"近 365 天每股现金分红合计 {g('div_sum_12m')} 元 ÷ 收盘价 {g('last_close')} 元",
    }
    return traces.get(metric, "")


def evidence(ds: Dataset, result: dict, s: Strategy, code: str) -> dict:
    evals = {e.cond.id: e for e in result["evals"]}
    row = next((r for r in result["rows"] if r["thscode"] == code), None)
    if row is None:
        return {"error": "该股票不在当前股票池里"}
    meta = ds.meta.get("sources", {})
    r = ds.frame.loc[code]
    items, facts, inferences, uncertain = [], [], [], list(row.get("universe_missing", []))
    for c in s.conditions:
        e = evals[c.id]
        m = METRICS_BY_ID[c.metric]
        st = e.status.get(code)
        v = e.value.get(code)
        thr = e.threshold.get(code)
        if c.basis == "absolute":
            if c.op == "between":
                thr_text = f"{c.value[0]:g}～{c.value[1]:g}{m.unit}"
            else:
                thr_text = f"{OP_TEXT[c.op]} {c.value:g}{m.unit}"
        else:
            base = e.basis_value.get(code)
            peers = e.peers.get(code)
            scope = f"所属行业「{r.get('industry_name') or '未知'}」" if c.basis == "industry_median" else "全市场"
            if base is None or pd.isna(base):
                thr_text = f"{OP_TEXT[c.op]} {scope}中位数（可比公司 {int(peers or 0)} 家，不足 5 家无法计算）"
            else:
                mult = "" if c.value == 1 else f" × {c.value:g} = {fmt(thr, c.metric)}"
                thr_text = f"{OP_TEXT[c.op]} {scope}中位数 {fmt(base, c.metric)}{mult}（可比公司 {int(peers)} 家）"
        gap_text = ""
        if st == "fail":
            diff = (v - thr) if c.op in ("<=", "<") else (thr - v) if c.op in (">=", ">") else None
            if diff is not None:
                gap_text = f"差 {fmt(abs(diff), c.metric)}"
                if abs(thr) > 1e-9:
                    gap_text += f"（相当于阈值的 {abs(diff) / abs(thr):.0%}）"
        source = {"provider": m.source, "fields": m.fields, "period": m.period, "unit": m.unit}
        if m.category == "估值":
            source["as_of"] = meta.get("valuation", {}).get("as_of")
            source["request_id"] = _s(r.get("val_request_id"))
        elif m.category == "成长":
            source["request_id"] = _s(r.get("inc_request_id"))
            source["as_of"] = f"2026 中报披露日 {_s(r.get('rpt_h1_26')) or '未知'}"
        elif m.category in ("盈利", "偿债", "现金流"):
            source["request_id"] = _s(r.get("fin_request_id"))
        elif m.category in ("行情", "流动性", "分红"):
            source["as_of"] = _s(r.get("price_date")) or meta.get("prices", {}).get("last_date")
        elif c.metric == "listed_days":
            source["as_of"] = _s(r.get("price_date")) or meta.get("prices", {}).get("last_date")
            source["request_id"] = _s(r.get("listing_request_id"))
        trace = _raw_trace(ds, code, c.metric)
        item = {
            "condition_id": c.id, "label": c.label or "条件", "condition": describe(c.metric, c.op, c.value, c.basis),
            "metric": m.name, "value": None if pd.isna(v) else float(v), "value_text": fmt(v, c.metric),
            "threshold_text": thr_text, "status": st, "status_text": STATUS_TEXT[st], "gap_text": gap_text,
            "trace": trace, "source": source, "caveat": m.caveat,
            "missing_reason": e.missing_reason.get(code) or "",
        }
        items.append(item)
        if st == "missing":
            uncertain.append(f"{m.name}：{item['missing_reason']}，该条件无法判定，不按通过处理。")
        else:
            facts.append(f"{m.name} = {item['value_text']}" + (f"（{trace}）" if trace else "")
                         + f"，{m.period}，来源 {m.fields}")
            concept = CONCEPTS_BY_ID.get(c.concept_id or "")
            who = f"“{concept.label}”口径中的" if concept else "你写明的"
            inferences.append(f"{who}条件“{item['condition']}”：{item['status_text']}"
                              + (f"，{gap_text}" if gap_text else "") + "。")
        if m.caveat and st != "missing":
            uncertain.append(f"{m.name}：{m.caveat}")
    if r.get("is_st"):
        uncertain.append("该公司名称带 ST，存在退市风险警示。")
    uncertain.append(f"财务数据统一取 {REPORT_LABELS[REPORT_LATEST]}；若该公司三季报已在快照之后披露，结论可能变化。")
    verdict = _verdict(row, items)
    return {
        "thscode": code, "name": r["name"], "industry": r.get("industry_name"),
        "industry_l1": _s(r.get("industry_l1")), "category": row["category"],
        "category_text": CATEGORY_TEXT[row["category"]], "verdict": verdict, "items": items,
        "facts": facts, "inferences": inferences, "uncertain": list(dict.fromkeys(uncertain)),
        "snapshot_id": ds.snapshot_id, "data_mode": ds.mode,
    }


def _verdict(row: dict, items: list[dict]) -> str:
    cat = row["category"]
    if row.get("universe_missing"):
        return "股票池资格无法判定：" + "；".join(row["universe_missing"]) + "。不会按入选处理。"
    if cat == "passed":
        return f"满足你设定的全部 {len(items)} 个条件。"
    fails = [i for i in items if i["status"] == "fail"]
    miss = [i for i in items if i["status"] == "missing"]
    if cat == "missing":
        return "已满足的条件都通过了，但 " + "、".join(i["metric"] for i in miss) + " 缺数据，无法判定是否入选。"
    head = "；".join(f"{i['condition']}（实际 {i['value_text']}，{i['gap_text'] or '不满足'}）" for i in fails[:3])
    if cat == "near_miss":
        return f"只差一个条件：{head}。"
    return f"被 {len(fails)} 个条件排除：{head}" + ("；另有缺失数据" if miss else "") + "。"


def summary(result: dict, s: Strategy) -> dict:
    c = result["counts"]
    n = result["universe_count"]
    lines = [f"股票池 {n} 只，满足全部 {len(s.conditions)} 个条件的 {c['passed']} 只；"
             f"只差一个条件的 {c['near_miss']} 只；因数据缺失无法判定的 {c['missing']} 只。"]
    imp = sorted(result["impact"], key=lambda x: -x["removed_adds"])
    if imp and imp[0]["removed_adds"] > 0:
        cond = next(x for x in s.conditions if x.id == imp[0]["condition_id"])
        lines.append(f"最卡人的条件是“{describe(cond.metric, cond.op, cond.value, cond.basis)}”："
                     f"去掉它会多出 {imp[0]['removed_adds']} 只。")
    numbers = {n, len(s.conditions), *c.values(), *(i["removed_adds"] for i in result["impact"])}
    return {"text": "".join(lines), "numbers": sorted(numbers)}


def explanation_facts(result: dict, s: Strategy) -> dict[str, dict]:
    """Bind each sentence to its typed source. The model only selects IDs."""
    facts = {"overview": {"text": summary(result, s)["text"], "source": "engine.counts",
                          "values": {"universe": result["universe_count"], **result["counts"]}}}
    for c in s.conditions:
        facts[f"condition:{c.id}"] = {
            "text": f"条件“{describe(c.metric, c.op, c.value, c.basis)}”按你确认的口径执行。",
            "source": "strategy.conditions", "condition_id": c.id, "metric": c.metric,
            "op": c.op, "value": c.value, "basis": c.basis,
            "unit": METRICS_BY_ID[c.metric].unit, "period": METRICS_BY_ID[c.metric].period,
        }
    for impact in result["impact"]:
        c = next(c for c in s.conditions if c.id == impact["condition_id"])
        facts[f"impact:{c.id}"] = {
            "text": f"去掉“{describe(c.metric, c.op, c.value, c.basis)}”会多出{impact['removed_adds']}只；这只是条件变化的影响。",
            "source": "engine.impact", "condition_id": c.id, "removed_adds": impact["removed_adds"],
        }
    return facts


def render_fact_selection(selection: dict, facts: dict[str, dict]) -> tuple[str, list[dict]]:
    if set(selection) != {"fact_ids"}:
        raise ValueError("模型必须只返回事实编号")
    ids = selection["fact_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or not all(isinstance(i, str) for i in ids):
        raise ValueError("事实编号需为1至3项")
    if len(set(ids)) != len(ids) or any(i not in facts for i in ids):
        raise ValueError("事实编号不存在或重复")
    chosen = [{"id": i, **facts[i]} for i in ids]
    return "\n".join(f"• {f['text']}" for f in chosen), chosen


_NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def number_guard(text: str, allowed: set[float] | list[float]) -> list[str]:
    """返回文中找不到出处的数字。百分比、千分位、四舍五入到 0～2 位小数都算对上。"""
    pool = set()
    for a in allowed:
        try:
            a = float(a)
        except (TypeError, ValueError):
            continue
        for d in (0, 1, 2):
            pool.add(round(a, d))
            pool.add(round(a * 100, d))
    bad = []
    for tok in _NUM_RE.findall(text):
        x = float(tok.replace(",", ""))
        if x in pool or any(abs(x - p) < 1e-9 for p in pool):
            continue
        bad.append(tok)
    return bad


def compliance_guard(text: str) -> list[str]:
    hits = []
    for pat, why in REFUSE_PATTERNS:
        if re.search(pat, text):
            hits.append(why)
    for w in ("建议买入", "建议卖出", "值得买", "可以买", "推荐买", "加仓", "减仓", "必然上涨", "一定会"):
        if w in text:
            hits.append(f"出现“{w}”")
    return hits


def _s(x) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return str(x)

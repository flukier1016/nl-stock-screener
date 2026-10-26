"""意图解析评测：同一批句子分别跑规则解析和大模型解析，按片段打分。

用法::

    .venv/bin/python eval/run_eval.py              # 只跑规则
    .venv/bin/python eval/run_eval.py --llm        # 规则 + 大模型（需要配置大模型 Key）

评分口径（每条用例只检查它写明的项）：
概念集合（召回；未标 allow_extra 时也查多余；concepts_alt 列出同样合理的另一种理解）、
写明数字的条件（比较编译后的条件：指标 + 数值 + 精确比较符 + 基准）、
股票池、行业、拒绝片段数、未识别片段数、剔除项。结果写到 eval/results/。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mingshai import llm, parse_llm  # noqa: E402
from mingshai.compile import compile_strategy  # noqa: E402
from mingshai.guard import check_segment  # noqa: E402
from mingshai.parse_rules import parse as rule_parse  # noqa: E402
from mingshai.store import load_demo, load_snapshot  # noqa: E402

def score(case: dict, segs) -> dict:
    checks = {}
    got_c = {s.concept_id for s in segs if s.kind == "concept"}
    if "concepts" in case:
        alts = [set(case["concepts"])] + [set(a) for a in case.get("concepts_alt", [])]
        checks["concepts"] = any(exp <= got_c and (case.get("allow_extra") or got_c <= exp) for exp in alts)
    if "explicit" in case:
        # 比较最终编译出的条件，而不是片段类型：用户写的数字既可能被解析成“写明的条件”，
        # 也可能被映射到某个概念的等价方案（如“破净”→ 低估值的“市净率 1 倍以下”方案），两者执行起来一样
        strategy, _, _ = compile_strategy(case["q"], segs)
        got = [(c.metric, c.op, c.value, c.basis) for c in strategy.conditions]
        ok = True
        for expected in case["explicit"]:
            m, op, v = expected[:3]
            basis = expected[3] if len(expected) > 3 else "absolute"
            hit = any(g[0] == m and g[1] == op and _eq(g[2], v) and g[3] == basis for g in got)
            ok &= hit
        checks["explicit"] = ok
        expected_literals = case["explicit"]
        checks["no_extra_explicit"] = all(
            any(c.metric == e[0] and c.op == e[1] and _eq(c.value, e[2])
                and c.basis == (e[3] if len(e) > 3 else "absolute") for e in expected_literals)
            for c in strategy.conditions if c.origin == "explicit"
        )
    if "universe" in case:
        us = [s.universe_id for s in segs if s.kind == "universe"]
        checks["universe"] = bool(us) and us[0] == case["universe"]
    if "industries" in case:
        names = [s.industry or "" for s in segs if s.kind == "industry"]
        checks["industries"] = all(any(e in n for n in names) for e in case["industries"])
    if "refused" in case:
        checks["refused"] = sum(s.kind == "refused" for s in segs) >= case["refused"]
    if "unsupported_min" in case:
        checks["unsupported"] = sum(s.kind == "unsupported" for s in segs) >= case["unsupported_min"]
    if "excludes" in case:
        flags = {s.metric for s in segs if s.kind == "exclude"}
        checks["excludes"] = set(case["excludes"]) <= flags
    if "refused" not in case:
        checks["no_false_refusal"] = not any(s.kind == "refused" for s in segs)
    if "must_block" in case:
        strategy, _, _ = compile_strategy(case["q"], segs)
        checks["required_block"] = case["must_block"] in {i.code for i in strategy.intent_issues}
    return {"pass": all(checks.values()), "checks": checks,
            "got": [f"{s.kind}:{s.concept_id or s.metric or s.universe_id or s.industry or ''}" for s in segs],
            "segments": [s.model_dump(exclude_none=True) for s in segs]}


def _eq(a, b) -> bool:
    if isinstance(b, list):
        return isinstance(a, list) and len(a) == 2 and all(abs(float(x) - float(y)) < 1e-6 for x, y in zip(a, b, strict=True))
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def run(mode: str, cases: list[dict], industries: dict[str, str]) -> dict:
    rows = []
    for c in cases:
        t0 = time.monotonic()
        if mode == "rules":
            segs = []
            for s in rule_parse(c["q"], industries):
                fixed, err = check_segment(s, industries)
                if fixed is not None and not err:
                    segs.append(fixed)
            meta = {}
        else:
            r = parse_llm.parse(c["q"], industries)
            segs = r.segments
            meta = {"mode": r.mode, "blocked": len(r.blocked), "fallback": r.fallback_reason}
        res = score(c, segs)
        res.update({"id": c["id"], "q": c["q"], "tag": c.get("tag", ""), "ms": int((time.monotonic() - t0) * 1000), **meta})
        rows.append(res)
    n = len(rows)
    by_tag = {}
    for r in rows:
        by_tag.setdefault(r["tag"] or "常规", []).append(r["pass"])
    return {"mode": mode, "n": n, "pass": sum(r["pass"] for r in rows), "pass_rate": round(sum(r["pass"] for r in rows) / n, 3),
            "by_tag": {k: f"{sum(v)}/{len(v)}" for k, v in by_tag.items()}, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    cases = json.loads((ROOT / "eval/cases.json").read_text(encoding="utf-8"))
    ds = load_snapshot() or load_demo()
    out = {"snapshot": ds.snapshot_id, "industries": len(ds.industries), "results": [run("rules", cases, ds.industries)]}
    if a.llm:
        if not llm.available():
            raise SystemExit("没有配置大模型")
        out["llm"] = llm.public_info()
        out["results"].append(run("llm", cases, ds.industries))
    stamp = time.strftime("%Y%m%d-%H%M")
    dest = ROOT / "eval/results" / f"{stamp}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in out["results"]:
        print(f"[{r['mode']}] 通过 {r['pass']}/{r['n']}（{r['pass_rate']:.0%}） 分组 {r['by_tag']}")
        for row in r["rows"]:
            if not row["pass"]:
                bad = [k for k, v in row["checks"].items() if not v]
                print(f"   ✗ {row['id']:<12} {row['q']}  未过：{bad}  得到：{row['got']}")
    print("结果文件：", dest.relative_to(ROOT))


if __name__ == "__main__":
    main()

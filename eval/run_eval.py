"""意图解析评测：同一批句子分别跑规则解析和大模型解析，按片段打分。

用法::

    .venv/bin/python eval/run_eval.py              # 只跑规则
    .venv/bin/python eval/run_eval.py --llm        # 规则 + 大模型（需要配置大模型 Key）

评分口径（每条用例只检查它写明的项）：
概念集合（召回；未标 allow_extra 时也查多余）、写明数字的条件（指标 + 数值 + 方向族）、
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
from mingshai.guard import check_segment  # noqa: E402
from mingshai.parse_rules import parse as rule_parse  # noqa: E402
from mingshai.store import load_demo, load_snapshot  # noqa: E402

FAMILY = {"<": "upper", "<=": "upper", ">": "lower", ">=": "lower", "between": "between"}


def score(case: dict, segs) -> dict:
    checks = {}
    got_c = {s.concept_id for s in segs if s.kind == "concept"}
    if "concepts" in case:
        exp = set(case["concepts"])
        checks["concepts"] = exp <= got_c and (case.get("allow_extra") or got_c <= exp)
    if "explicit" in case:
        got = [(s.metric, FAMILY.get(s.op), s.value) for s in segs if s.kind == "explicit"]
        ok = True
        for m, op, v in case["explicit"]:
            hit = any(g[0] == m and g[1] == FAMILY[op] and _eq(g[2], v) for g in got)
            ok &= hit
        checks["explicit"] = ok
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
    return {"pass": all(checks.values()), "checks": checks,
            "got": [f"{s.kind}:{s.concept_id or s.metric or s.universe_id or s.industry or ''}" for s in segs]}


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

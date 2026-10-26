"""Reproducible acceptance gates; no API keys, model calls or market downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mingshai.compile import compile_strategy
from mingshai.engine import run
from mingshai.guard import check_segment, check_strategy
from mingshai.parse_rules import parse
from mingshai.store import Dataset, load_demo


def gate_cases() -> list[dict]:
    ds = load_demo()
    rows = []
    for case in json.loads((ROOT / "eval/acceptance_cases.json").read_text())["cases"]:
        segments = [check_segment(s, ds.industries)[0] for s in parse(case["query"], ds.industries)]
        strategy, _, _ = compile_strategy(case["query"], [s for s in segments if s is not None])
        issues = check_strategy(strategy, set(ds.industries.values()))
        conditions = [[c.metric, c.op, c.value, c.basis] for c in strategy.conditions]
        if "blocked" in case:
            passed = case["blocked"] in {i.get("code") for i in issues}
        else:
            def normalized(conditions):
                def key(c):
                    value = [float(v) for v in c[2]] if isinstance(c[2], list) else float(c[2])
                    return json.dumps([c[0], c[1], value, c[3] if len(c) > 3 else "absolute"])
                return sorted(key(c) for c in conditions)
            passed = (not issues and normalized(conditions) == normalized(case["conditions"])
                      and sorted(strategy.universe.industries) == sorted(ds.industries[n] for n in case["industries"])
                      and sorted(strategy.universe.excluded_industries) == sorted(ds.industries[n] for n in case["excluded_industries"]))
        rows.append({"id": case["id"], "query": case["query"], "pass": passed,
                     "expected": case, "actual_strategy": strategy.model_dump(), "issues": issues})
    return rows


def golden_stock_sets() -> list[dict]:
    """Four manually specified companies; expected sets are hand-calculated."""
    ds = load_demo()
    frame = ds.frame.iloc[:4].copy()
    frame.index = ["600101.SH", "600102.SH", "600103.SH", "600104.SH"]
    frame["name"] = ["样本甲", "样本乙", "样本丙", "样本丁"]
    frame["industry_name"] = ["银行", "白酒", "证券", "电力"]
    frame["industry_code"] = [ds.industries[n] for n in frame["industry_name"]]
    frame["is_st"] = False
    frame["listed_days"] = [1000, 1000, 1000, float("nan")]
    frame["pe_ttm"] = [15, 14, 25, 10]
    frame["pb_mrq"] = [0.8, 3, 0.9, 1.2]
    frame["div_yield"] = [6, 3, 2, 9]
    frame["vol_60d"] = [25, 10, 30, 15]
    gold = Dataset(frame, {"mode": "demo", "snapshot_id": "golden-four-v1"}, ds.industries)
    cases = [
        ("PE低于15倍", ["600102.SH"]),
        ("PE不超过15倍", ["600101.SH", "600102.SH"]),
        ("不要银行股，股息率不低于3%", ["600102.SH"]),
        ("银行股里PB低于1倍", ["600101.SH"]),
        ("近60日波动率低于20%", ["600102.SH"]),
    ]
    rows = []
    for query, expected in cases:
        strategy, _, _ = compile_strategy(query, parse(query, gold.industries))
        result = run(gold, strategy)
        passed = not check_strategy(strategy) and sorted(result["passed"]) == expected
        rows.append({"query": query, "pass": passed, "expected_stocks": expected,
                     "actual_stocks": result["passed"], "counts": result["counts"],
                     "run_id": result["run_id"]})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows, stocks = gate_cases(), golden_stock_sets()
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "purpose": "Regression acceptance, not real-user accuracy or investment performance",
        "cases_sha256": hashlib.sha256((ROOT / "eval/acceptance_cases.json").read_bytes()).hexdigest(),
        "intent_gates": {"passed": sum(r["pass"] for r in rows), "n": len(rows), "rows": rows},
        "golden_stock_sets": {"passed": sum(r["pass"] for r in stocks), "n": len(stocks), "rows": stocks},
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Intent gates: {sum(r['pass'] for r in rows)}/{len(rows)}; golden stock sets: {sum(r['pass'] for r in stocks)}/{len(stocks)}")
    for row in rows + stocks:
        if not row["pass"]:
            print(json.dumps(row, ensure_ascii=False))
    if not all(r["pass"] for r in rows + stocks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

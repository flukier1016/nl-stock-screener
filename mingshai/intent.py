"""Conservative intent checks shared by both parsers and the execution gate.

Unsupported logic is a blocking issue, never an invitation to drop a word.
This release deliberately accepts conjunctions and industry exclusions. It
does not attempt to flatten boolean expressions into a conjunction.
"""
from __future__ import annotations

import re
import unicodedata

from .dsl import IntentIssue


def inspect_intent(query: str) -> list[IntentIssue]:
    text = unicodedata.normalize("NFKC", query).lower()
    issues: list[IntentIssue] = []

    def add(code: str, match: re.Match, message: str) -> None:
        issues.append(IntentIssue(code=code, text=match.group(), message=message))

    if m := re.search(r"或者|或是|或|\bor\b|任一|至少一(?:项|条)|二选一", text):
        add("unsupported_or", m, "这句话包含“或”的逻辑。本版本只执行同时满足的条件，请拆成两次筛选，或改写后重新解析。")
    if m := re.search(r"不是不|并非不|不排除|不要不|除了.+以外.*(?:都|全部)", text):
        add("complex_negation", m, "这句话的否定范围需要确认，请明确写出要保留或剔除的行业、指标。")
    if m := re.search(r"分红稳定|稳定分红|连续(?:多年|\d+年)分红|每年(?:都)?(?:分红|分钱)", text):
        add("dividend_history_unavailable", m, "多年分红稳定性需要逐年分红记录，不能用近12个月股息率替代。请明确改用股息率，或移除这项要求后重新解析。")
    if m := re.search(r"(?:一季报|三季报|季报|年报|ttm\s*roe|roe\s*ttm|扣非净利润|扣非利润)", text):
        add("unsupported_financial_period", m, "这项财务口径尚未支持；本版本财务条件统一使用2026中报。请改写后重新解析。")

    # A window immediately attached to a metric has a precise meaning. Fixed
    # windows are explicit; a different window must not become a 60-day metric.
    windows = [
        (r"(?:近|过去|最近)?\s*(\d+)\s*(?:个)?(?:交易日|日|天)\s*(?:年化)?波动率", 60, "波动率"),
        (r"(?:近|过去|最近)?\s*(\d+)\s*(?:个)?(?:交易日|日|天)\s*(?:最大)?回撤", 120, "最大回撤"),
        (r"(?:近|过去|最近)?\s*(\d+)\s*(?:个)?(?:交易日|日|天)\s*(?:涨跌幅|涨幅)", None, "涨跌幅"),
        (r"(?:近|过去|最近)?\s*(\d+)\s*(?:个)?月\s*股息率", 12, "股息率（月）"),
    ]
    for pattern, expected, label in windows:
        for m in re.finditer(pattern, text):
            n = int(m.group(1))
            if (expected is not None and n != expected) or (expected is None and n not in (20, 60, 250)):
                add("unsupported_window", m, f"“{m.group()}”的时间窗口尚未支持，不能替换成其他期间的{label}。请修改后重新解析。")
    if m := re.search(r"(?:近|过去|最近)(?:一|二|三|六|十二|半|\d+)(?:个)?(?:月|年|周)(?:的)?(?:年化)?(?:波动率|最大回撤|回撤)", text):
        add("unsupported_window", m, "请用交易日明确窗口：波动率支持60日，最大回撤支持120日；其他窗口暂不执行。")
    if m := re.search(r"(?:排名|前|top\s*)\s*\d+|历史(?:估值)?分位|自(?:身|己)历史|连续\d+(?:个)?(?:季度|年|天|日)", text):
        add("unsupported_history_or_ranking", m, "这项排名或历史序列要求尚未支持，不能只执行其中的单期指标。请改写后重新解析。")
    return issues

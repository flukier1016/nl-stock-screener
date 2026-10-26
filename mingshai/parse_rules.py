"""规则解析器：大模型不可用时的兜底，也是大模型输出的对照组。

刻意做成**确定性、可读**的：同义词表、正则和优先级都写在这里，合规和产品同事
能直接审。顺序：

1. 合规拦截（预测、承诺、买卖建议）——先于一切，命中的片段不再参与后续解析；
2. 用户写明的数字条件（"市盈率低于 20""ROE 大于 15%"）；
3. 股票池、行业、剔除项；
4. 模糊概念（最长匹配优先，"分红稳定"不会被"稳定"抢走）；
5. 剩下识别不了的片段，原样列为"未识别"，不猜。
"""
from __future__ import annotations

import re
import unicodedata

from .dsl import Segment
from .guard import resolve_industry
from .intent import inspect_intent
from .registry import CONCEPTS, REFUSE_PATTERNS

# 指标别名（长的在前）。ROE 一律按"年化 ROE"理解，并在原因里写明
_METRIC_ALIASES: list[tuple[str, str]] = sorted([
    ("市盈率ttm", "pe_ttm"), ("市盈率", "pe_ttm"), ("pe(ttm)", "pe_ttm"), ("pe", "pe_ttm"),
    ("市净率", "pb_mrq"), ("pb", "pb_mrq"), ("市销率", "ps_ttm"), ("ps", "ps_ttm"),
    ("市现率", "pcf_ttm"),
    ("净资产收益率", "roe_ann"), ("roe", "roe_ann"),
    ("股息率", "div_yield"), ("股息", "div_yield"),
    ("资产负债率", "debt_ratio"), ("负债率", "debt_ratio"),
    ("营业收入增速", "rev_yoy"), ("营业收入增长", "rev_yoy"), ("营收增速", "rev_yoy"),
    ("营收增长", "rev_yoy"), ("收入增速", "rev_yoy"), ("收入增长", "rev_yoy"), ("营收同比", "rev_yoy"),
    ("净利润增速", "np_yoy"), ("净利润增长", "np_yoy"), ("利润增速", "np_yoy"),
    ("利润增长", "np_yoy"), ("净利增速", "np_yoy"), ("净利润同比", "np_yoy"),
    ("毛利率", "gross_margin"), ("净利率", "net_margin"),
    ("年化波动率", "vol_60d"), ("波动率", "vol_60d"),
    ("近60日年化波动率", "vol_60d"), ("60日年化波动率", "vol_60d"),
    ("近60日波动率", "vol_60d"), ("60日波动率", "vol_60d"),
    ("最大回撤", "mdd_120d"), ("回撤", "mdd_120d"),
    ("近120日最大回撤", "mdd_120d"), ("120日最大回撤", "mdd_120d"),
    ("近12个月股息率", "div_yield"), ("12个月股息率", "div_yield"),
    ("近一个月涨幅", "ret_20d"), ("近1个月涨幅", "ret_20d"), ("20日涨幅", "ret_20d"),
    ("近三个月涨幅", "ret_60d"), ("近3个月涨幅", "ret_60d"), ("60日涨幅", "ret_60d"),
    ("近一年涨幅", "ret_250d"), ("近1年涨幅", "ret_250d"), ("250日涨幅", "ret_250d"),
    ("日均成交额", "amt_20d"), ("成交额", "amt_20d"),
    ("流动比率", "current_ratio"), ("现金含量", "cash_content"),
], key=lambda t: -len(t[0]))

_LT = r"(?:低于|小于|不到|少于|<(?!=))"
_LE = r"(?:不超过|不高于|不大于|<=|≤)"
_GT = r"(?:高于|大于|超过|>(?!=))"
_GE = r"(?:不低于|不少于|不小于|至少|>=|≥)"
_NUM = r"(-?\d+(?:\.\d+)?)"
_UNIT = r"\s*(?:%|％|倍|亿元|亿|个百分点)?"

# 用户口语里的固定说法 → 明确条件
_PHRASES: list[tuple[str, str, str, object, str]] = [
    (r"破净", "pb_mrq", "between", [0.0, 1.0], "“破净”= 市净率在 0～1 倍之间"),
    (r"(?:不要|剔除|排除|去掉)亏损(?:的)?|(?:盈利|赚钱)的公司|没有亏损", "pe_ttm", ">", 0.0,
     "“不要亏损”= 市盈率(TTM) 为正"),
]
_UNIVERSE_WORDS = [
    (r"沪深\s*300|沪深三百|hs300", "hs300"), (r"中证\s*500|中证五百|zz500", "zz500"),
    (r"中证\s*1000|中证一千|zz1000", "zz1000"), (r"全\s*a|全部\s*a股|全市场", "all_a"),
]
# 剔除动词可以管到后面并列的几项："剔除 ST 和次新股"。Python 正则不支持变长回看，所以在代码里往回找动词
_EXCL_VERB = re.compile(r"剔除|排除|不要|去掉|不含|除去|不选|不考虑|不看|除了|除|非")
_EXCL_TARGETS = [(r"\*?st(?:股)?", "st"), (r"次新(?:股)?", "new"), (r"北交所(?:股票)?", "bj")]
_INDUSTRY_ALIASES = {
    "券商": "证券", "芯片": "半导体", "医药": "医药", "银行股": "银行", "保险股": "保险",
    "白酒": "白酒", "光伏": "光伏设备", "新能源车": "汽车整车", "电池": "电池",
}
_FILLERS = re.compile(
    r"帮我|请|给我|我想|想要|想找|找一?些?|选出?|筛选?出?|挑出?|看看|一些|几只|有哪些|哪些|"
    r"股票|个股|标的|公司|的|里面?|当中|之中|中的|最好|要求|并且|而且|同时|以及|还要|还有|也|"
    r"且|和|与|及|要|是|有|比较|相对|一点|一下|吧|呢|啊|吗|里|哪只|哪个|那种|这种|类型|那些|这些"
)
_SPLIT = re.compile(r"[，,、。；;！!？?\s/]+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", " ", t).strip()


class _Spans:
    def __init__(self, text: str):
        self.text = text
        self.mask = [False] * len(text)

    def free(self, a: int, b: int) -> bool:
        return not any(self.mask[a:b])

    def take(self, a: int, b: int) -> None:
        for i in range(a, b):
            self.mask[i] = True

    def leftovers(self) -> list[tuple[int, str]]:
        out, buf, start = [], [], None
        for i, ch in enumerate(self.text):
            if self.mask[i]:
                if buf:
                    out.append((start, "".join(buf)))
                buf, start = [], None
            else:
                if start is None:
                    start = i
                buf.append(ch)
        if buf:
            out.append((start, "".join(buf)))
        return out


def parse(query: str, industries: dict[str, str] | None = None) -> list[Segment]:
    industries = industries or {}
    t = normalize(query)
    sp = _Spans(t)
    found: list[tuple[int, Segment]] = []

    def add(m: re.Match | tuple[int, int], seg: Segment) -> None:
        a, b = (m.start(), m.end()) if isinstance(m, re.Match) else m
        sp.take(a, b)
        found.append((a, seg))

    for issue in inspect_intent(t):
        start = t.find(issue.text)
        if start >= 0 and sp.free(start, start + len(issue.text)):
            add((start, start + len(issue.text)), Segment(text=issue.text, kind="unsupported", reason=issue.message))

    # 1. 合规拦截
    for pat, why in REFUSE_PATTERNS:
        for m in re.finditer(pat, t):
            if sp.free(m.start(), m.end()):
                add(m, Segment(text=m.group(0), kind="refused", reason=why))

    # 2. 固定说法 + 写明数字的条件
    for pat, metric, op, value, why in _PHRASES:
        for m in re.finditer(pat, t):
            if sp.free(m.start(), m.end()):
                add(m, Segment(text=m.group(0), kind="explicit", metric=metric, op=op,
                               value=value, reason=why))
    for alias, metric in _METRIC_ALIASES:
        a = re.escape(alias)
        pats = [
            (rf"{a}\s*(?:在|介于)?\s*{_NUM}{_UNIT}\s*(?:到|至|-|~|～)\s*{_NUM}{_UNIT}\s*(?:之间)?", "between"),
            (rf"{a}\s*{_LE}\s*{_NUM}{_UNIT}", "<="),
            (rf"{a}\s*{_LT}\s*{_NUM}{_UNIT}", "<"),
            (rf"{a}\s*{_GE}\s*{_NUM}{_UNIT}", ">="),
            (rf"{a}\s*{_GT}\s*{_NUM}{_UNIT}", ">"),
            (rf"{a}\s*(?:在)?\s*{_NUM}{_UNIT}\s*(?:以下|以内)", "<="),
            (rf"{a}\s*(?:在)?\s*{_NUM}{_UNIT}\s*(?:以上)", ">="),
        ]
        for pat, op in pats:
            for m in re.finditer(pat, t):
                if not sp.free(m.start(), m.end()):
                    continue
                prefix = t[max(0, m.start() - 8):m.start()]
                if neg := re.search(r"(?:不要|剔除|排除|不选|不含|不考虑)\s*$", prefix):
                    start = m.start() - len(prefix) + neg.start()
                    add((start, m.end()), Segment(text=t[start:m.end()], kind="unsupported",
                                                 reason="暂不对数值条件自动取反，请直接写需要保留的比较方向"))
                    continue
                if op == "between":
                    lo, hi = float(m.group(1)), float(m.group(2))
                    value: float | list[float] = [min(lo, hi), max(lo, hi)]
                else:
                    value = float(m.group(1))
                why = "按“年化 ROE（中报 × 2）”理解" if metric == "roe_ann" else ""
                if metric == "mdd_120d" and isinstance(value, float):
                    value = abs(value)
                add(m, Segment(text=m.group(0), kind="explicit", metric=metric, op=op,
                               value=value, reason=why))

    # 3. 股票池、剔除项、行业
    for pat, uid in _UNIVERSE_WORDS:
        for m in re.finditer(pat, t):
            if sp.free(m.start(), m.end()):
                add(m, Segment(text=m.group(0), kind="universe", universe_id=uid))
    hits = sorted(((m, flag) for pat, flag in _EXCL_TARGETS for m in re.finditer(pat, t)),
                  key=lambda x: x[0].start())
    for m, flag in hits:
        if not sp.free(m.start(), m.end()):
            continue
        window = re.split(r"[，。,；;]", t[max(0, m.start() - 12):m.start()])[-1]
        verbs = list(_EXCL_VERB.finditer(window))
        if not verbs:
            continue
        a = m.start() - len(window) + verbs[-1].start()
        start = a if sp.free(a, m.start()) else m.start()
        add((start, m.end()), Segment(text=t[start:m.end()], kind="exclude", metric=flag))
    names = {_strip_level(n): n for n in industries}
    for alias, target in sorted(_INDUSTRY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        names.setdefault(alias, target)
    for short in sorted(names, key=len, reverse=True):
        if len(short) < 2:
            continue
        for m in re.finditer(re.escape(short.lower()), t):
            if sp.free(m.start(), m.end()):
                full = names[short]
                code = resolve_industry(full, industries) if industries else None
                end = m.end()
                for suffix in ("行业", "板块", "股"):
                    if t.startswith(suffix, end):
                        end += len(suffix)
                        break
                prefix = t[max(0, m.start() - 12):m.start()]
                prefix = re.split(r"[，。,；;]", prefix)[-1]
                verbs = list(_EXCL_VERB.finditer(prefix))
                tail = prefix[verbs[-1].end():] if verbs else ""
                # The verb may govern a parallel list of industries, but not
                # an earlier ST/profit clause such as “不要亏损的银行股”.
                for industry_word in sorted(names, key=len, reverse=True):
                    tail = tail.replace(industry_word.lower(), "")
                tail = re.sub(r"行业|板块|股|[\s、和与及]+", "", tail)
                excluded = bool(verbs) and not tail
                start = m.start() - len(prefix) + verbs[-1].start() if excluded else m.start()
                if excluded:
                    for suffix in ("以外", "之外", "外"):
                        if t.startswith(suffix, end):
                            end += len(suffix)
                            break
                add((start, end), Segment(text=t[start:end], kind="exclude_industry" if excluded else "industry",
                                              industry=full, industry_code=code))

    # 4. 模糊概念（最长同义词优先）
    syn = sorted(((s.lower(), c) for c in CONCEPTS for s in c.synonyms), key=lambda x: -len(x[0]))
    for s, c in syn:
        for m in re.finditer(re.escape(s), t):
            if sp.free(m.start(), m.end()):
                prefix = t[max(0, m.start() - 6):m.start()]
                if re.search(r"(?:不要|不选|剔除|排除|不含)\s*$", prefix):
                    add(m, Segment(text=m.group(), kind="unsupported",
                                   reason="暂不支持对这一概念直接取反，请明确写出指标和比较方向"))
                    continue
                if c.universe:
                    add(m, Segment(text=m.group(0), kind="universe", universe_id=c.universe,
                                   concept_id=c.id))
                else:
                    opt = c.default_option.id if c.default_option else None
                    add(m, Segment(text=m.group(0), kind="concept", concept_id=c.id,
                                   option_id=opt))

    # 5. 剩下的片段
    for start, chunk in sp.leftovers():
        for piece in _SPLIT.split(chunk):
            core = _FILLERS.sub("", piece).strip(" 的，,。")
            if len(core) >= 2 and not core.isdigit():
                found.append((start + max(chunk.find(piece), 0), Segment(
                    text=re.sub(r"^(的|有|是)+|(的|吧|呢)+$", "", piece.strip()) or piece.strip(),
                    kind="unsupported",
                    reason="尚无对应的数据口径，需要改成可量化、受支持的说法")))

    found.sort(key=lambda x: x[0])
    return _dedupe([s for _, s in found])


def _strip_level(name: str) -> str:
    return re.sub(r"[ⅠⅡⅢⅣ]+$|[一二三]级$", "", name).strip()


def _dedupe(segs: list[Segment]) -> list[Segment]:
    out, seen = [], set()
    for s in segs:
        if s.kind == "concept":
            key = ("concept", s.concept_id)
        else:
            key = (s.kind, s.concept_id, s.metric, str(s.value), s.universe_id, s.industry, s.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out

"""口径字典：指标、模糊词、股票池的单一信源。

这一层回答两个问题：

1. **一个数从哪里来**：每个 :class:`Metric` 写清来源接口、字段、报告期或窗口、单位、
   白话口径和已知局限。引擎、解释卡、守门器、前端都从这里读，不在别处写魔法数字。
2. **一句模糊话可以指什么**：每个 :class:`Concept`（如"经营改善"）列出几种可执行的口径
   方案 :class:`Option`，标出推荐项，也列出"想做但数据做不到"的方案和原因。

设计与边界（诚实）：

* 大模型只能从这里挑 ``concept_id`` / ``option_id`` / ``metric_id``，挑不出来的一律被
  守门器拦下（见 :mod:`mingshai.guard`）。
* 报告期统一用 :data:`REPORT_LATEST`。三季报正在披露，混用不同报告期会让同一个条件
  对不同公司比较的不是同一段时间，所以宁可统一用中报，并在页面上写明。
* 估值接口只提供最新快照，没有历史估值，所以"低于自身历史估值分位"做不了，
  作为不可用方案展示出来，而不是悄悄换成别的口径。
* 扶摇暂无市值字段，大中小盘用沪深 300 / 中证 500 / 中证 1000 成分股代理，页面上写明。
"""
from __future__ import annotations

from dataclasses import dataclass

# ── 报告期（单一信源）────────────────────────────────────────────────
REPORT_LATEST = "2026-2"      # 2026 中报：截至 2026-10 全部 A 股已披露的最新报告期
REPORT_PREV_Q = "2026-1"      # 2026 一季报：和中报相减得到二季度单季，用来算"增速是否在加快"
REPORT_LAST_YEAR = "2025-2"   # 2025 中报：同口径去年同期，用来算同比变化
REPORTS = (REPORT_LATEST, REPORT_PREV_Q, REPORT_LAST_YEAR)
# 扶摇财务指标接口的同比增速字段实测全为空（抽查 8 只，三个报告期都是 null），
# 所以增速一律用利润表原始金额自己算；指标接口只取 ROE、毛利率、负债率等比率
FIN_REPORTS = (REPORT_LATEST, REPORT_LAST_YEAR)
INCOME_LIMIT = 9              # 利润表按季度取最近 9 期：2026Q2 往回到 2024Q2
REPORT_LABELS = {
    "2026-2": "2026 中报", "2026-1": "2026 一季报", "2025-2": "2025 中报",
    "2025-4": "2025 年报", "2026-3": "2026 三季报",
}

FUYAO = "同花顺金融数据 API（扶摇）"
EP_VALUATION = "GET /api/a-share/valuations/snapshot"
EP_FIN_IND = "GET /api/a-share/financials/indicators"
EP_INCOME = "GET /api/a-share/financials/income-statements?period=quarterly"
EP_DAILY_K = "GET /api/dump/market-dumps/daily-k/download-url（10 年日 K Parquet，未复权）"
EP_ADJ = "GET /api/dump/market-dumps/adjustment-factors/download-url（除权除息事件）"
EP_TICKERS = "GET /api/meta/tickers/list?asset_type=a-share"
EP_INDEX = "GET /api/a-share-index/constituents/ths-stock-list"

# 估值、行情等相对口径最少需要的可比样本数；不足时该条件记为"缺失"，不悄悄放行
MIN_PEERS = 5
# 差一点入选：只差一个条件，且差距不超过阈值的这个比例
NEAR_MISS_TOLERANCE = 0.20


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    unit: str                 # "%" / "倍" / "亿元" / "天" / "pp"（百分点）
    category: str
    source: str               # 来源接口
    fields: str               # 原始字段及计算方式
    period: str               # 报告期或时间窗口
    definition: str           # 白话口径
    direction: str = "none"   # higher：越大越好 / lower：越小越好 / none
    lo: float = -1e9          # 合法取值范围（守门器用）
    hi: float = 1e9
    decimals: int = 2
    relative_ok: bool = True  # 能否用"行业中位数 / 全市场中位数"作阈值
    caveat: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "unit": self.unit,
            "category": self.category, "source": self.source, "fields": self.fields,
            "period": self.period, "definition": self.definition,
            "direction": self.direction, "range": [self.lo, self.hi],
            "decimals": self.decimals, "relative_ok": self.relative_ok,
            "caveat": self.caveat,
        }


def _fin(id_, name, unit, category, field_, definition, *, report=REPORT_LATEST,
         direction="higher", lo=-1e4, hi=1e5, caveat="") -> Metric:
    return Metric(
        id=id_, name=name, unit=unit, category=category, source=f"{FUYAO} · {EP_FIN_IND}",
        fields=field_, period=REPORT_LABELS[report], definition=definition,
        direction=direction, lo=lo, hi=hi, caveat=caveat,
    )


_L, _Q, _Y = REPORT_LABELS[REPORT_LATEST], REPORT_LABELS[REPORT_PREV_Q], REPORT_LABELS[REPORT_LAST_YEAR]

METRICS: list[Metric] = [
    # ── 估值（最新快照）────────────────────────────────────────────
    Metric("pe_ttm", "市盈率 TTM", "倍", "估值", f"{FUYAO} · {EP_VALUATION}", "pe_ttm",
           "最新估值快照", "股价 ÷ 最近 12 个月每股收益。亏损公司为负值，不能按'越低越便宜'理解。",
           direction="lower", lo=-1e5, hi=1e5,
           caveat="负值表示亏损；估值快照随行情变化，时点见数据状态栏。"),
    Metric("pb_mrq", "市净率 MRQ", "倍", "估值", f"{FUYAO} · {EP_VALUATION}", "pb_mrq",
           "最新估值快照", "股价 ÷ 最近一期每股净资产。", direction="lower", lo=-1e4, hi=1e4,
           caveat="净资产为负时没有常规意义。"),
    Metric("ps_ttm", "市销率 TTM", "倍", "估值", f"{FUYAO} · {EP_VALUATION}", "ps_ttm",
           "最新估值快照", "股价 ÷ 最近 12 个月每股营业收入。", direction="lower", lo=-1e4, hi=1e5),
    Metric("pcf_ttm", "市现率 TTM", "倍", "估值", f"{FUYAO} · {EP_VALUATION}", "pcf_ttm",
           "最新估值快照", "股价 ÷ 最近 12 个月每股经营现金流。", direction="lower", lo=-1e5, hi=1e5,
           caveat="经营现金流为负时为负值。"),
    # ── 成长（利润表原始金额自算；单季 = 累计值相减）──────────────────────
    Metric("rev_yoy", "营业收入同比增速", "%", "成长", f"{FUYAO} · {EP_INCOME}",
           "operating_income：2026Q2 累计 ÷ 2025Q2 累计 − 1", f"{_L}（上半年累计）",
           "今年上半年营业收入相对去年上半年的增长率。", direction="higher", lo=-1e4, hi=1e5,
           caveat="去年同期营收 ≤ 0 时记为缺失。"),
    Metric("np_yoy", "归母净利润同比增速", "%", "成长", f"{FUYAO} · {EP_INCOME}",
           "parent_holder_net_profit：(2026Q2 累计 − 2025Q2 累计) ÷ |2025Q2 累计|", f"{_L}（上半年累计）",
           "今年上半年归母净利润相对去年上半年的增长率；去年同期亏损时按绝对值做分母，亏损收窄或扭亏记为正增长。",
           direction="higher", lo=-1e6, hi=1e6,
           caveat="利润基数很小或由亏转盈时增速会非常大，证据卡同时给出原始金额。"),
    Metric("rev_yoy_accel", "单季营收增速变化（Q2 较 Q1）", "pp", "成长", f"{FUYAO} · {EP_INCOME}",
           "二季度单季 = 2026Q2 累计 − 2026Q1；Q2 单季同比 − Q1 同比", "2026 年一、二季度单季",
           "二季度单季营收同比增速减去一季度营收同比增速。为正说明增长在加快。",
           direction="higher", lo=-1e5, hi=1e5,
           caveat="单季值由累计值相减得到；上年同期单季营收 ≤ 0 时记为缺失。"),
    Metric("rev_yoy_vs_ly", "营收增速变化（较去年同期）", "pp", "成长", f"{FUYAO} · {EP_INCOME}",
           "今年上半年营收同比 − 去年上半年营收同比（需 2024Q2）", f"{_L} 与 {_Y}",
           "今年中报营收增速减去去年中报营收增速。为正说明增长比去年快。",
           direction="higher", lo=-1e5, hi=1e5),
    Metric("np_yoy_vs_ly", "利润增速变化（较去年同期）", "pp", "成长", f"{FUYAO} · {EP_INCOME}",
           "今年上半年归母净利润同比 − 去年上半年同比", f"{_L} 与 {_Y}",
           "今年中报归母净利润增速减去去年中报增速。", direction="higher", lo=-1e7, hi=1e7,
           caveat="利润基数小时波动极大，建议与收入条件同用。"),
    # ── 盈利 ────────────────────────────────────────────────────
    _fin("gross_margin", "销售毛利率", "%", "盈利", "sale_gross_margin",
         f"{_L}累计毛利 ÷ 营业收入。", lo=-1e4, hi=100,
         caveat="银行、券商等金融企业没有毛利率口径，通常为空。"),
    Metric("gm_delta", "毛利率同比变化", "pp", "盈利", f"{FUYAO} · {EP_FIN_IND}",
           f"sale_gross_margin：{_L} − {_Y}", f"{_L} 与 {_Y}",
           "今年中报毛利率减去去年中报毛利率（百分点）。", direction="higher", lo=-1e4, hi=1e4),
    _fin("net_margin", "销售净利率", "%", "盈利", "sale_net_interest_ratio",
         f"{_L}累计净利润 ÷ 营业收入。", lo=-1e5, hi=1e4),
    Metric("roe_ann", "ROE（中报年化）", "%", "盈利", f"{FUYAO} · {EP_FIN_IND}",
           "index_weighted_avg_roe × 2", _L,
           "中报加权净资产收益率 × 2，简单年化，便于和常见的'ROE 10%/15%'门槛比较。",
           direction="higher", lo=-1e4, hi=1e4,
           caveat="原始字段是半年数据，未年化；季节性强的行业年化后会失真。解释卡同时给出原值。"),
    Metric("roe_delta", "ROE 同比变化", "pp", "盈利", f"{FUYAO} · {EP_FIN_IND}",
           f"index_weighted_avg_roe：{_L} − {_Y}", f"{_L} 与 {_Y}",
           "今年中报 ROE 减去去年中报 ROE（都未年化，同口径可比）。",
           direction="higher", lo=-1e4, hi=1e4),
    # ── 偿债与现金流 ─────────────────────────────────────────────
    _fin("debt_ratio", "资产负债率", "%", "偿债", "assets_debt_ratio",
         f"{_L}末总负债 ÷ 总资产。", direction="lower", lo=0, hi=1000,
         caveat="银行、保险、券商的负债率天然在 80% 以上，用这个口径筛金融股没有意义。"),
    _fin("current_ratio", "流动比率", "倍", "偿债", "current_ratio",
         f"{_L}末流动资产 ÷ 流动负债。", lo=0, hi=1e4,
         caveat="金融企业通常为空。"),
    _fin("cash_content", "净利润现金含量", "%", "现金流", "net_profit_cash_content",
         f"{_L}累计经营活动现金流净额 ÷ 净利润。≥100% 说明利润基本收到了现金。",
         caveat="净利润为负时该比值没有意义。"),
    _fin("ocf_to_rev", "销售现金比率", "%", "现金流", "operating_cash_flow_net_divide_income",
         f"{_L}累计经营活动现金流净额 ÷ 营业收入。"),
    # ── 行情（复权收益率序列）───────────────────────────────────────
    Metric("vol_60d", "60 日年化波动率", "%", "行情", f"{FUYAO} · {EP_DAILY_K} + {EP_ADJ}",
           "close_price 经除权除息调整后的日收益率，标准差 × √252", "最近 60 个交易日",
           "过去 60 个交易日日收益率的年化标准差，越低说明走势越平稳。",
           direction="lower", lo=0, hi=1000, caveat="有效交易日少于 40 天记为缺失。"),
    Metric("mdd_120d", "120 日最大回撤", "%", "行情", f"{FUYAO} · {EP_DAILY_K} + {EP_ADJ}",
           "复权净值序列的最大峰谷跌幅（取正数）", "最近 120 个交易日",
           "过去 120 个交易日里，从最高点跌下来最多跌了多少。", direction="lower", lo=0, hi=100),
    Metric("ret_20d", "20 日涨跌幅", "%", "行情", f"{FUYAO} · {EP_DAILY_K} + {EP_ADJ}",
           "复权收盘价 t / t−20 − 1", "最近 20 个交易日", "近一个月左右的复权涨跌幅。",
           lo=-100, hi=1e4),
    Metric("ret_60d", "60 日涨跌幅", "%", "行情", f"{FUYAO} · {EP_DAILY_K} + {EP_ADJ}",
           "复权收盘价 t / t−60 − 1", "最近 60 个交易日", "近三个月左右的复权涨跌幅。",
           lo=-100, hi=1e4),
    Metric("ret_250d", "250 日涨跌幅", "%", "行情", f"{FUYAO} · {EP_DAILY_K} + {EP_ADJ}",
           "复权收盘价 t / t−250 − 1", "最近 250 个交易日", "近一年左右的复权涨跌幅。",
           lo=-100, hi=1e5),
    Metric("amt_20d", "20 日日均成交额", "亿元", "流动性", f"{FUYAO} · {EP_DAILY_K}",
           "turnover 的 20 日均值 ÷ 1e8", "最近 20 个交易日", "近 20 个交易日平均每天成交多少钱。",
           lo=0, hi=1e5),
    Metric("div_yield", "股息率（近 12 个月）", "%", "分红", f"{FUYAO} · {EP_ADJ} + {EP_DAILY_K}",
           "近 365 天除息日的每股现金分红（税前）合计 ÷ 最新收盘价", "最近 365 天",
           "过去一年实际派发的现金分红相对现价的比例。", lo=0, hi=100,
           caveat="按除息日统计，税前口径；尚未实施的分红预案不计入。"),
    Metric("listed_days", "上市天数", "天", "基础", f"{FUYAO} · {EP_TICKERS}",
           "数据日期 − list_date", "截至行情数据日期", "公司上市了多少天。", lo=0, hi=1e5,
           relative_ok=False),
]
METRICS_BY_ID: dict[str, Metric] = {m.id: m for m in METRICS}

OPS = ("<=", ">=", "<", ">", "between")
BASES = ("absolute", "industry_median", "market_median")
BASIS_LABELS = {
    "absolute": "绝对值", "industry_median": "所属行业中位数", "market_median": "全市场中位数",
}


# ── 模糊词与口径方案 ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Cond:
    """口径方案里的一条条件模板。basis≠absolute 时 value 是倍数（1.0 = 中位数本身）。"""

    metric: str
    op: str
    value: float | tuple[float, float]
    basis: str = "absolute"

    def as_dict(self) -> dict:
        v = list(self.value) if isinstance(self.value, tuple) else self.value
        return {"metric": self.metric, "op": self.op, "value": v, "basis": self.basis}


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    summary: str
    conds: tuple[Cond, ...]
    recommended: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "summary": self.summary,
            "conds": [c.as_dict() for c in self.conds],
            "recommended": self.recommended, "note": self.note,
        }


@dataclass(frozen=True)
class Concept:
    id: str
    label: str
    synonyms: tuple[str, ...]
    question: str                         # 澄清时问用户的话
    options: tuple[Option, ...]
    unavailable: tuple[tuple[str, str], ...] = ()   # (想做的口径, 做不到的原因)
    universe: str | None = None           # 映射为股票池的概念（大盘股等）

    @property
    def default_option(self) -> Option | None:
        for o in self.options:
            if o.recommended:
                return o
        return self.options[0] if self.options else None

    def option(self, option_id: str) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "synonyms": list(self.synonyms),
            "question": self.question, "options": [o.as_dict() for o in self.options],
            "unavailable": [{"label": a, "reason": b} for a, b in self.unavailable],
            "universe": self.universe,
        }


CONCEPTS: list[Concept] = [
    Concept(
        "op_improve", "经营改善",
        ("经营改善", "基本面改善", "业绩改善", "经营向好", "业绩好转", "边际改善", "业绩向好",
         "经营好转", "基本面向好", "业绩改善中"),
        "“经营改善”你更看重哪一面？",
        (
            Option("growth_up", "收入利润双增，且收入增速比去年快",
                   "营收、净利润同比都为正，并且营收增速高于去年同期。",
                   (Cond("rev_yoy", ">", 0), Cond("np_yoy", ">", 0), Cond("rev_yoy_vs_ly", ">", 0)),
                   recommended=True),
            Option("margin_up", "盈利能力在变强", "毛利率和 ROE 都比去年同期高。",
                   (Cond("gm_delta", ">", 0), Cond("roe_delta", ">", 0)),
                   note="金融企业没有毛利率，会落入'数据缺失'。"),
            Option("accel", "增长在加速", "上半年营收同比为正，且二季度单季增速快于一季度。",
                   (Cond("rev_yoy", ">", 0), Cond("rev_yoy_accel", ">", 0))),
        ),
        unavailable=(("用三季报判断", "三季报正在陆续披露（截至快照只有少数公司发布），统一用中报，避免同一条件比较不同时间段。"),),
    ),
    Concept(
        "val_fair", "估值合理",
        ("估值合理", "估值不贵", "估值适中", "估值还行", "价格合理", "估值不高", "不贵"),
        "“估值合理”用哪个尺子量？",
        (
            Option("ind_pe", "市盈率不高于同行中位数", "PE(TTM) 为正，且不高于所属同花顺行业的中位数。",
                   (Cond("pe_ttm", ">", 0), Cond("pe_ttm", "<=", 1.0, "industry_median")),
                   recommended=True),
            Option("abs_pe", "市盈率在 0 到 30 倍之间", "不看行业，PE(TTM) 落在 (0, 30] 倍。",
                   (Cond("pe_ttm", "between", (0, 30)),)),
            Option("ind_pe_pb", "PE 和 PB 都不高于同行中位数", "更严格：两个估值指标都不高于行业中位数。",
                   (Cond("pe_ttm", ">", 0), Cond("pe_ttm", "<=", 1.0, "industry_median"),
                    Cond("pb_mrq", "<=", 1.0, "industry_median"))),
        ),
        unavailable=(("低于自身历史估值分位", "扶摇估值接口只提供最新快照，没有历史估值序列，无法计算分位。"),),
    ),
    Concept(
        "val_low", "低估值",
        ("低估值", "估值低", "低估", "便宜", "估值便宜", "低市盈率"),
        "“低估值”要多便宜？",
        (
            Option("pe15", "市盈率 15 倍以下", "PE(TTM) 落在 (0, 15] 倍。",
                   (Cond("pe_ttm", "between", (0, 15)),), recommended=True),
            Option("ind_low", "明显低于同行", "PE(TTM) 为正，且不超过行业中位数的 0.7 倍。",
                   (Cond("pe_ttm", ">", 0), Cond("pe_ttm", "<=", 0.7, "industry_median"))),
            Option("pb1", "市净率 1 倍以下（破净）", "PB(MRQ) 落在 (0, 1] 倍。",
                   (Cond("pb_mrq", "between", (0, 1)),)),
        ),
        unavailable=(("低于自身历史估值分位", "扶摇估值接口只提供最新快照，没有历史估值序列，无法计算分位。"),),
    ),
    Concept(
        "px_stable", "走势相对稳定",
        ("走势相对稳定", "走势稳定", "走势平稳", "股价稳定", "波动小", "低波动", "不大起大落",
         "走势稳健", "波动不大", "比较稳", "稳定"),
        "“走势稳定”指波动小，还是跌得少？",
        (
            Option("vol_mdd", "波动低于市场一半股票，且近半年回撤不超过 20%",
                   "60 日年化波动率不高于全市场中位数，且 120 日最大回撤 ≤ 20%。",
                   (Cond("vol_60d", "<=", 1.0, "market_median"), Cond("mdd_120d", "<=", 20)),
                   recommended=True),
            Option("vol_only", "只看波动：年化波动率 30% 以下", "60 日年化波动率 ≤ 30%。",
                   (Cond("vol_60d", "<=", 30),)),
            Option("mdd_only", "只看回撤：近半年最大回撤 15% 以内", "120 日最大回撤 ≤ 15%。",
                   (Cond("mdd_120d", "<=", 15),)),
        ),
    ),
    Concept(
        "growth_high", "高成长",
        ("高成长", "高增长", "成长性好", "增长快", "成长股", "高速增长", "业绩高增"),
        "“高成长”的门槛定多高？",
        (
            Option("g20", "营收和净利润都增长 20% 以上", "营收、净利润同比增速都 ≥ 20%。",
                   (Cond("rev_yoy", ">=", 20), Cond("np_yoy", ">=", 20)), recommended=True),
            Option("rev30", "只看收入：营收增长 30% 以上", "营收同比增速 ≥ 30%。",
                   (Cond("rev_yoy", ">=", 30),)),
        ),
    ),
    Concept(
        "div_high", "高股息",
        ("高股息", "高分红", "分红多", "股息率高", "红利", "分红稳定", "股息高", "高息"),
        "股息率要求多高？",
        (
            Option("dy3", "近 12 个月股息率 3% 以上", "过去一年现金分红 ÷ 现价 ≥ 3%。",
                   (Cond("div_yield", ">=", 3),), recommended=True),
            Option("dy5", "近 12 个月股息率 5% 以上", "过去一年现金分红 ÷ 现价 ≥ 5%。",
                   (Cond("div_yield", ">=", 5),)),
        ),
        unavailable=(("连续多年分红", "需要多年分红记录逐年核对，本版本只统计近 12 个月，已在口径中写明。"),),
    ),
    Concept(
        "roe_high", "盈利能力强",
        ("盈利能力强", "赚钱能力强", "高ROE", "ROE高", "高roe", "roe高", "盈利能力好", "很赚钱"),
        "“盈利能力强”的门槛？",
        (
            Option("roe15", "ROE 年化 15% 以上", "中报 ROE × 2 ≥ 15%。",
                   (Cond("roe_ann", ">=", 15),), recommended=True),
            Option("roe10_gm30", "ROE 年化 10% 以上且毛利率 30% 以上", "兼顾回报率和产品定价能力。",
                   (Cond("roe_ann", ">=", 10), Cond("gross_margin", ">=", 30))),
        ),
    ),
    Concept(
        "debt_low", "财务稳健",
        ("财务稳健", "负债低", "低负债", "资产负债率低", "负债率低", "不缺钱", "偿债能力强", "财务健康"),
        "“财务稳健”按什么标准？",
        (
            Option("d50_cr15", "负债率 50% 以下，流动比率 1.5 以上", "资产负债率 ≤ 50%，流动比率 ≥ 1.5。",
                   (Cond("debt_ratio", "<=", 50), Cond("current_ratio", ">=", 1.5)), recommended=True),
            Option("d40", "只看负债率：40% 以下", "资产负债率 ≤ 40%。",
                   (Cond("debt_ratio", "<=", 40),)),
        ),
    ),
    Concept(
        "cash_good", "现金流好",
        ("现金流好", "现金流充裕", "盈利质量高", "利润含金量高", "现金流健康", "回款好", "利润质量"),
        "“现金流好”看哪个比率？",
        (
            Option("cc100", "利润基本都收到了现金", "净利润为正（净利率 > 0），且净利润现金含量 ≥ 100%。",
                   (Cond("net_margin", ">", 0), Cond("cash_content", ">=", 100)), recommended=True),
            Option("ocf10", "经营现金流占收入 10% 以上", "销售现金比率 ≥ 10%。",
                   (Cond("ocf_to_rev", ">=", 10),)),
        ),
    ),
    Concept(
        "liquid", "流动性好",
        ("流动性好", "成交活跃", "交易活跃", "好买卖", "成交量大", "流动性充足"),
        "成交额门槛？",
        (
            Option("amt1", "日均成交额 1 亿元以上", "近 20 日日均成交额 ≥ 1 亿元。",
                   (Cond("amt_20d", ">=", 1),), recommended=True),
            Option("amt5", "日均成交额 5 亿元以上", "近 20 日日均成交额 ≥ 5 亿元。",
                   (Cond("amt_20d", ">=", 5),)),
        ),
    ),
    Concept(
        "mom_up", "走势偏强",
        ("走势强", "走势偏强", "强势", "趋势向上", "上升趋势", "涨势好", "走强"),
        "“走势偏强”看多长时间？",
        (
            Option("r60", "近三个月涨 10% 以上，近一个月没跌", "60 日涨幅 ≥ 10%，20 日涨幅 ≥ 0。",
                   (Cond("ret_60d", ">=", 10), Cond("ret_20d", ">=", 0)), recommended=True),
            Option("r250", "近一年涨 20% 以上", "250 日涨幅 ≥ 20%。", (Cond("ret_250d", ">=", 20),)),
        ),
    ),
    Concept(
        "not_hot", "涨幅不大",
        ("涨幅不大", "没涨太多", "不追高", "还没涨", "涨得不多", "位置不高", "低位"),
        "“没涨太多”的范围？",
        (
            Option("r60_20", "近三个月涨幅不超过 20%", "60 日涨跌幅 ≤ 20%。",
                   (Cond("ret_60d", "<=", 20),), recommended=True),
            Option("r250_0", "近一年还没涨", "250 日涨跌幅 ≤ 0。", (Cond("ret_250d", "<=", 0),)),
        ),
    ),
    Concept("cap_large", "大盘股", ("大盘股", "大盘", "蓝筹", "大市值", "权重股", "白马"),
            "", (), universe="hs300"),
    Concept("cap_mid", "中盘股", ("中盘股", "中盘", "中市值"), "", (), universe="zz500"),
    Concept("cap_small", "小盘股", ("小盘股", "小盘", "小市值"), "", (), universe="zz1000"),
]
CONCEPTS_BY_ID: dict[str, Concept] = {c.id: c for c in CONCEPTS}

# 概念之间的"张力"：不是逻辑冲突，但同时要求往往筛不出几只，需要提醒
TENSIONS: list[tuple[str, str, str]] = [
    ("growth_high", "val_low", "高成长的公司市场通常给高估值，“高成长 + 低估值”同时满足的往往很少。"),
    ("growth_high", "div_high", "高分红多见于成熟期公司，增速通常不高。"),
    ("px_stable", "mom_up", "低波动和明显上涨常常互相牵制，涨得快的股票波动通常也大。"),
    ("mom_up", "not_hot", "“走势偏强”和“涨幅不大”都在约束近期涨幅，区间可能很窄。"),
]


# ── 股票池 ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Universe:
    id: str
    label: str
    index_thscode: str | None
    note: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "index_thscode": self.index_thscode,
                "note": self.note}


UNIVERSES: list[Universe] = [
    Universe("all_a", "全部 A 股", None, "沪深京全部 A 股（默认剔除 ST 和上市不满一年的次新股）"),
    Universe("hs300", "沪深 300", "000300.SH", "也用作“大盘股”的代理口径（扶摇暂无市值字段）"),
    Universe("zz500", "中证 500", "000905.SH", "也用作“中盘股”的代理口径"),
    Universe("zz1000", "中证 1000", "000852.SH", "也用作“小盘股”的代理口径"),
]
UNIVERSES_BY_ID: dict[str, Universe] = {u.id: u for u in UNIVERSES}

# 金融行业关键词：负债率、毛利率、流动比率这些口径对它们不适用
FINANCIAL_INDUSTRY_KEYWORDS = ("银行", "保险", "证券", "券商", "多元金融", "非银")
NOT_FOR_FINANCIALS = ("debt_ratio", "current_ratio", "gross_margin", "gm_delta")

# 合规：预测、承诺、买卖建议类表达。命中即拒绝该片段，并给出可替代的可验证条件
REFUSE_PATTERNS: list[tuple[str, str]] = [
    (r"(明天|后天|下周|下个月|未来|接下来|短期内|近期)[^，。,]{0,6}?(会|能|将|要)?(大涨|大跌|涨停|跌停|翻倍|反弹|上涨|下跌|涨|跌)",
     "预测未来涨跌"),
    (r"(会|能|将|要|可能)(大涨|涨停|翻倍|暴涨|起飞|上涨|反弹)", "预测未来涨跌"),
    (r"(稳赚|必涨|保本|保证收益|稳定收益|无风险|包赚|只涨不跌|收益承诺)", "收益承诺"),
    (r"(推荐|建议)(我)?(买|买入|卖|卖出|加仓|减仓|抄底)|(该|能|可以|值得)(买|入手|抄底|上车)吗?|帮我(买|下单)",
     "直接买卖建议"),
    (r"(目标价|能涨到|涨到多少|翻几倍|内幕|消息股)", "价格预测或非公开信息"),
]
REFUSE_HINT = ("本产品只把你的想法翻译成可检查的历史与当期数据条件，不预测涨跌、不承诺收益、"
               "不给买卖建议。可以换成可验证的说法，例如“近 60 日涨幅”“波动率”“股息率”。")


def catalog() -> dict:
    """给前端和大模型看的完整口径目录（同一份数据，不另写一套）。"""
    return {
        "report_latest": REPORT_LATEST,
        "reports": {r: REPORT_LABELS[r] for r in REPORTS},
        "fin_reports": list(FIN_REPORTS),
        "metrics": [m.as_dict() for m in METRICS],
        "concepts": [c.as_dict() for c in CONCEPTS],
        "universes": [u.as_dict() for u in UNIVERSES],
        "ops": list(OPS), "bases": BASIS_LABELS,
        "near_miss_tolerance": NEAR_MISS_TOLERANCE, "min_peers": MIN_PEERS,
    }

# 测试说明

```bash
.venv/bin/python -m pytest            # 全部自动化测试（不联网：扶摇用 MockTransport，大模型用 monkeypatch）
.venv/bin/python eval/run_eval.py     # 意图解析评测（规则）；加 --llm 同时评测大模型
```

当前结果：**60 passed**；意图解析评测：规则 25/33，Claude 32/33（详见 `eval/results/`）。

## 1. 主链路

| 场景 | 预期 | 自动化测试 |
|---|---|---|
| “经营改善、估值合理、走势相对稳定” | 解析出 3 个概念 → 默认方案编译成 7 条条件 → 筛选 → 证据卡 → 保存 → 转监控 | `test_main_chain_on_demo` `test_rules_canonical_query` |
| 在澄清面板换方案 / 不用某一条 | 条件随之改变；被跳过的概念不产生条件 | `test_compile_choices_and_skip` |
| 同一策略重复运行、改策略名称 | `run_id` 与入选名单完全一致；名称不影响结果编号 | `test_engine_is_deterministic` |
| 分组完整性 | 入选 + 差一点 + 被排除 + 缺失 = 股票池；入选无失败无缺失；差一点只差一条且差距 ≤ 20% | `test_categories_partition_universe` |
| 漏斗、去掉条件的影响、阈值曲线三者一致 | 漏斗末项 = 入选数；“去掉第 i 条多出 N 只”与真正去掉后重跑的结果一致；曲线在当前阈值处 = 入选数 | `test_funnel_and_removal_impact_are_consistent` `test_sweep_matches_engine_at_current_threshold` |
| 查一只不在股票池里的股票 | 说明它为什么没进股票池（不是成分股 / 行业不符 / ST / 次新） | `test_evidence_out_of_universe_explains_why` |
| 写明数字的条件 | “低于”→`<`，“不超过”→`<=`，“大于”→`>`，“不低于”→`>=`，区间、“以上 / 以下” | `test_rules_explicit_numbers_and_ops` |
| 同义词冲突 | “分红稳定”归高股息，不被“稳定”抢走；同一概念出现两次只保留一次 | `test_rules_longest_synonym_wins` |

## 2. 数据缺失与接口失败

| 场景 | 预期 | 自动化测试 |
|---|---|---|
| 没有真实数据快照 | 所有筛选接口返回 503 `DATA_UNAVAILABLE`，页面显示原因和构建进度，不给任何结论；演示数据必须显式切换 | `test_live_without_snapshot_refuses_to_answer` |
| 某股票某指标缺失 | 该条件记为“缺失”，股票进入“数据缺失”组，**不算通过** | `test_missing_never_passes` |
| 行业可比公司不足 5 家 | 相对阈值无法计算，记为缺失并写明原因 | `test_industry_median_needs_enough_peers` |
| 扶摇返回业务错误（如 2001 未认证） | 不重试、原样抛出，台账记录 code 与 request_id，台账不含 Key | `test_client_raises_business_error_without_retry` |
| 扶摇限流（HTTP 429） | 退避后重试成功，统计限流与重试次数 | `test_client_retries_throttle_then_succeeds` |
| 上游持续不可用（5003） | 达到最大次数后抛出，构建终止，不替换旧快照 | `test_client_gives_up_after_max_attempts` |
| 下载链接接口返回结构未知 | 在返回体里找第一个 http(s) 链接；找不到就报错停止 | `test_find_url_in_unknown_shape` |
| 除权事件落在停牌日 | 顺延到之后第一个交易日 | `test_event_on_suspension_day_rolls_forward` |
| 送转比例单位理解错误 | 除权日出现大量假涨跌，体检发现后停止构建 | `test_wrong_bonus_unit_would_be_caught` |
| 行情不足 40 个交易日 | 波动率记为缺失 | `test_price_features_windows_and_missing` |
| 扶摇财务指标的同比字段为空 | 增速改用利润表原始金额自算；去年同期营收 ≤ 0 或利润为 0 时记为缺失；亏损收窄 / 扭亏记为正增长 | `test_growth_from_income_statement_amounts` |
| 大模型超时 / 返回非法 JSON | 自动退回规则解析，页面写明原因 | `test_llm_failure_falls_back_to_rules` |
| 大模型输出目录外的概念、越界数值 | 守门器整条拦下，页面展示被拦内容和原因；合法片段照常使用 | `test_llm_out_of_catalog_is_blocked` |
| 监控遇到同一数据快照 | 明确记录“没有新数据，名单不变”，不当作新一期结果 | `test_main_chain_on_demo` |

## 3. 极端与合规边界

| 场景 | 预期 | 自动化测试 |
|---|---|---|
| “明天会涨停的股票” | 拒绝（预测未来涨跌），并给出可验证的替代说法；只剩拒绝片段时不生成任何条件 | `test_rules_refuse` `test_only_refused_query` |
| “稳赚不赔的”“推荐我买哪只”“目标价多少” | 分别按收益承诺、直接买卖建议、价格预测拒绝 | `test_rules_refuse` |
| AI 白话解读里出现找不到出处的数字或买卖建议 | 整段丢弃，展示原因与草稿，改用模板摘要 | `test_number_guard` `test_compliance_guard` |
| 同一指标要求互斥（60 日涨幅 ≥ 30% 且 ≤ 20%） | 标记“冲突”，筛选结果必然为 0 | `test_interval_conflict_detected` |
| 同时要求大盘股和小盘股 | 标记“冲突”，先按第一个处理并提示确认 | `test_compile_flags_conflicting_universes` |
| 用资产负债率筛银行 | 标记“口径不适用”，建议改用市净率、股息率、ROE | `test_financials_applicability_warning` |
| 高成长 + 低估值 | 标记“张力”，给出各自满足和同时满足的真实只数 | `test_tension_reports_real_overlap` |
| 非法阈值（PE ≤ −5,000,000、区间上下限颠倒、相对阈值用区间） | 守门器报错，接口返回 422，不运行 | `test_check_value` `test_screen_rejects_illegal_strategy` |
| 无法量化的说法（“管理层靠谱”“护城河”） | 列为“无法转为数据条件”，不猜 | `test_rules_unknown_is_listed_not_guessed` |

## 4. 契约（漂移守门）

- 每个口径方案里的每条条件都必须是合法条件；每个概念恰好一个推荐方案；同义词不能跨概念重复（`test_every_option_condition_is_legal` `test_synonyms_are_unique_across_concepts`）。
- 每个指标都有来源、单位、口径期和定义（`test_metrics_have_source_unit_and_definition`）。
- 演示数据包含全部指标列，保证真实快照与演示数据走同一套派生逻辑（`test_demo_frame_has_all_metric_columns`）。

## 5. 真实数据联调检查（快照 20261015-0147）

- 规模：5,580 只 A 股（含北交所），16,722 次扶摇请求，成功 16,322 次，限流 0 次，
  “该期无数据”（5003）202 次，已按缺失处理；构建用时约 17 分钟。
- 行业：同花顺行业指数 320 个，自动识别出两级，取覆盖率 100% 的一级 90 组作为“行业中位数”分组。
- 复权体检：5,530 个除权除息日，|收益率| > 25% 的占比 0.0%，
  除权日收益率中位数 1.6%，说明送转、派息单位理解正确。
- 时点：估值快照 2026-10-15 13:30（盘中），行情截至 2026-10-14 收盘；交易日历显示最新交易日为
  2026-10-15，页面据此提示“当日行情尚未发布”。

覆盖率最低的 8 个指标：

| 指标 | 有数据的比例 |
|---|---|
| 毛利率同比变化 | 94.9% |
| ROE 同比变化 | 95.4% |
| 250 日涨跌幅 | 96.7% |
| 销售毛利率 | 97.8% |
| 流动比率 | 97.9% |
| 营收增速变化（较去年同期） | 98.5% |
| 利润增速变化（较去年同期） | 98.5% |
| ROE（中报年化） | 98.7% |

抽样核对（证据卡里同样展示原始金额与公式）：

| 股票 | 估值 | 成长（利润表自算） | ROE | 分红 |
|---|---|---|---|---|
| 贵州茅台 600519.SH | PE(TTM) 19.95，PB 6.46 | 上半年营收 907.03 亿（同比 +1.47%），归母净利润同比 -1.95% | ROE 原值 16.75% → 年化 33.50% | 股息率 4.04% |
| 工商银行 601398.SH | PE(TTM) 8.06，PB 0.76 | 上半年营收 4,658.59 亿（同比 +9.08%），归母净利润同比 +3.32% | ROE 原值 4.32% → 年化 8.63% | 股息率 3.67% |
| 宁德时代 300750.SZ | PE(TTM) 14.86，PB 3.33 | 上半年营收 2,769.17 亿（同比 +54.80%），归母净利润同比 +41.98% | ROE 原值 12.08% → 年化 24.16% | 股息率 2.99% |

主链路在真实数据上的结果（同一快照下可复现）：

| 一句话 | 股票池 | 入选 | 差一点 | 被排除 | 数据缺失 |
|---|---|---|---|---|---|
| 经营改善、估值合理、走势相对稳定 | 5,210 | 69 | 90 | 5,051 | 0 |
| 高股息、低波动的大盘股 | 299 | 62 | 23 | 214 | 0 |
| 银行股里市净率低于1倍、股息率5%以上的 | 42 | 12 | 13 | 17 | 0 |
| ROE大于15%、负债率不超过40%、现金流好的公司 | 5,210 | 79 | 134 | 4,995 | 2 |

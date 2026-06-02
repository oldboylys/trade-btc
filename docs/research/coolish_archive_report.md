# Coolish 公开账本策略洞察研究报告

> 数据来源：[BTC-Trading-Since-2020](https://github.com/bwjoke/BTC-Trading-Since-2020)  
> 分析模块：`analysis/coolish_archive/`（独立研究，未改动交易策略代码）

## 执行摘要

本报告从真实 BitMEX 账本（约 6 年、17 万+ 成交）反向归纳 **行为特征** 与 **可尝试策略假设**。结论为假设性质，需在外部 OHLCV（如 Binance）上回测验证，不构成实盘建议。

- 加载成交行数：160421（manifest 期望 173113）
- 时间范围：2020-05-01 09:03:47.360000+00:00 → 2026-04-22 15:30:40.784000+00:00
- 最新 adjusted wealth 倍数：52.364x
- 最大回撤（相对峰值）：-80.59%

## Tier 1：直接观测

### 品种集中度

| Symbol | 名义占比 % |
|--------|------------|
| XBTUSD | 83.96 |
| ETHUSD | 10.89 |
| LTCUSD | 1.68 |
| XRPUSD | 1.34 |
| DOTUSDT | 0.46 |
| DOGEUSDT | 0.37 |
| DOGEUSD | 0.34 |
| AXSUSDT | 0.26 |
| YFIUSDTZ20 | 0.09 |
| UNIUSDT | 0.08 |

- BTC 相关成交占比（按 USD foreignNotional）：**94.89%**
- XBTUSD 占比：**83.96%**
- Buy / Sell：**47.61% / 52.39%**
- 限价单占比：**55.93%**
- Maker (AddedLiquidity)：**35.51%**
- 分批成交占比：**79.78%**

### 持仓周期（XBTUSD 重建）

- primary_symbol: XBTUSD
- trade_count: 98785
- hold_session_count: 231
- hold_hours_median: 8.709988333333333
- hold_hours_p90: 238.03273888888887
- hold_hours_mean: 226.72921287157286
- hold_days_median: 0.3629161805555556
- pct_hold_over_24h: 36.79653679653679
- pct_hold_over_168h: 13.852813852813853
- flip_count_total: 230

### 单笔规模 vs 权益

- median_trade_pct_equity: 0.3028
- p90_trade_pct_equity: 8.5991
- max_trade_pct_equity: 21760.9336

## Tier 2：权益与分周期

- baseline_multiple: 1.0
- latest_multiple: 52.364
- max_multiple: 52.44
- max_drawdown_pct: -80.59

### 主要回撤片段（Top）

- -80.59% @ 2020-06-25 → 恢复约 20 天
- -62.24% @ 2020-10-02 → 恢复约 21 天
- -43.54% @ 2021-04-23 → 恢复约 8 天
- -37.83% @ 2020-05-04 → 恢复约 6 天
- -35.21% @ 2020-12-01 → 恢复约 16 天

### 出金与权益高点

- 出金笔数：5；合计约 66.0018 XBT
- 峰值 95% 附近出金占比：80.0%

### 回撤期敞口行为

- 判定：**hold_through**

## 图表

![权益倍数](../../analysis/output/equity_multiple.png)
![持仓 CDF](../../analysis/output/hold_duration_cdf.png)
![年度 Buy%](../../analysis/output/yearly_buy_pct.png)
![Maker/Taker](../../analysis/output/maker_taker_pie.png)

## 策略假设卡片（H1–H7）

### H1：BTC 趋势跟随 + 限价分批建仓（置信度：中）

**证据**：限价单占比约 55.9%；分批成交（PartiallyFilled）约 79.8%；持仓中位 8.7h、P90 238.0h。

**规则草案**：1h EMA 趋势过滤；5m/15m 突破或回踩入场；分 2~4 笔阶梯限价建仓，单笔不超过权益 2~5%。

**参数区间**：`{'trend_tf': '1h', 'entry_tf': '5m-15m', 'ladder_legs': '2-4', 'per_leg_pct_equity': '0.5-2.5'}`

**失效条件**：限价占比低且持仓中位 < 1h 时，假设不成立。

**Binance 验证建议**：用 Binance BTCUSDT 5m/1h 回测：突破回踩 + 分批限价成交模型。

---

### H2：多头为主、空头择时（置信度：低）

**证据**：BTC 成交 Buy 占比约 47.6%；2023+ 年均 Buy 约 49.7%。

**规则草案**：默认只做多；1h 趋势转空且权益回撤 > 10% 时允许对冲空单。

**参数区间**：`{'long_bias': '>55% buys', 'short_trigger_dd_pct': '10-20'}`

**失效条件**：多年 Buy/Sell 接近 50/50 且无 regime 差异。

**Binance 验证建议**：分牛熊子样本回测纯多 vs 多+择时空。

---

### H3：Maker 优先执行（置信度：低）

**证据**：AddedLiquidity (Maker) 约 35.5%；RemovedLiquidity (Taker) 约 64.5%。

**规则草案**：优先 Post-Only 限价；相对 mid 偏移 5~20 bps；超时 30~120s 撤单。

**参数区间**：`{'post_only': True, 'offset_bps': '5-20', 'cancel_sec': '30-120'}`

**失效条件**：Taker 占比 > 55%。

**Binance 验证建议**：纸交易统计 maker fill rate 与滑点。

---

### H4：波段持有（非剥头皮）（置信度：中）

**证据**：持仓中位 8.7h、P90 238.0h；>24h 持仓占比 36.8%。

**规则草案**：TP 3~8%、SL 1.5~3%；禁止日内 >3 次反手。

**参数区间**：`{'tp_pct': '3-8', 'sl_pct': '1.5-3', 'max_flips_per_day': '1-3'}`

**失效条件**：持仓中位 < 1h 且高频反手。

**Binance 验证建议**：对比不同 min_hold_hours 下回测夏普。

---

### H5：盈利后系统性降风险（置信度：中）

**证据**：完成出金 5 笔、合计约 66.00 XBT；峰值附近出金占比 80.0%。

**规则草案**：权益达滚动高点 95% 后减仓 20~30% 或收紧 trailing stop。

**参数区间**：`{'peak_threshold': '0.95', 'trim_pct': '20-30'}`

**失效条件**：出金与权益高点无时间关联（注意出金≠可交易规则）。

**Binance 验证建议**：回测 equity peak trailing de-risk 规则。

---

### H6：资金费意识持仓（置信度：低-中）

**证据**：资金费事件 1825 笔；累计约 1.35339 XBT。

**规则草案**：|funding rate| > 0.01% 时缩短持有或反向套利对冲。

**参数区间**：`{'funding_threshold': '0.01-0.05'}`

**失效条件**：资金费绝对值占盈亏可忽略。

**Binance 验证建议**：叠加 funding 序列与持仓方向交叉表。

---

### H7：回撤期敞口管理（置信度：中）

**证据**：最大回撤窗口行为：hold_through；净敞口变化 sum=0.681724；主导方向 Long。

**规则草案**：回撤期扛单；宽 SL 3~5%、小仓位 ≤1% 权益/笔。

**参数区间**：`{'dd_trigger_pct': '15-25'}`

**失效条件**：回撤期敞口与盈利期无差异。

**Binance 验证建议**：标注历次 >20% 回撤窗口内的净仓位变化。

---

## 局限性

1. 单账户幸存者偏差，52x 收益不可直接复制。
2. 无 K 线，无法还原主观看图入场逻辑。
3. BitMEX 反向 XBT 与 Binance USDT 线性合约仅行为可类比。

## 持仓回合级指标研究（扩展）

详见 **[持仓回合开平仓指标报告](coolish_sessions_indicator_report.md)**：每个持仓回合对齐 Binance BTCUSDT 多周期指标（本地计算，对齐 TV 参数），含盈利/亏损画像与聚类。

```bash
python -m analysis.coolish_archive.run_all --phase sessions
```

## 后续验证清单（trade-btc）

- [ ] 下载 Binance BTCUSDT 5m/1h 历史 K 线至 `data/marketdata.db`
- [ ] 对 H1/H2/H4 参数网格做样本外回测
- [ ] 纸交易验证 H3 Maker 成交率
- [ ] 对比 H7 减仓 vs 扛单两种风控曲线

*图表与 JSON 输出目录：`analysis\output`*
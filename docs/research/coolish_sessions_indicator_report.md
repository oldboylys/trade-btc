# Coolish 持仓回合：开平仓技术指标与统计汇总

> K 线源：见配置 `klines_source`（`binance`=Binance BTCUSDT；`trades_resample`=成交 lastPx 重采样）  
> 指标参数对齐 TradingView / 项目 `IndicatorPipeline` 默认值；**非** TV 服务器拉取。

## 1. 数据范围

- 持仓回合数：**229**（`XBTUSD`，最短持有 5.0 分钟）
- 指标周期：5m, 15m, 1h, 4h
-  enriched 表：`analysis/output/sessions_enriched.parquet`

## 2. 总体摘要

- 持仓时长中位数：**8.74** 小时
- 胜率（按段内 realised PnL）：**48.03%**
- 段内 PnL 合计：**13.3594 XBT**

## 3. 盈利 vs 亏损：开仓时 1h 指标画像

### 盈利回合（n=110）
- RSI14 中位数：**55.3439**
- EMA20>EMA50 比例：**56.36%**
- MACD hist 中位数：**nan**

### 亏损回合（n=111）
- RSI14 中位数：**53.0582**
- EMA20>EMA50 比例：**63.96%**
- MACD hist 中位数：**nan**

## 4. 多空对比

- **Long**：114 段，胜率 48.25%，持仓中位 6.02h
- **Short**：115 段，胜率 47.83%，持仓中位 18.96h

## 5. 年度演变

| 年 | 回合数 | 胜率% | 开仓RSI中位 | 趋势多头% |
|---|--------|-------|-------------|-----------|
| 2020 | 100 | 51.0 | 55.27 | 79.0 |
| 2021 | 99 | 47.47 | 51.59 | 46.46 |
| 2022 | 20 | 45.0 | 54.8 | 25.0 |
| 2024 | 10 | 30.0 | 54.0 | 80.0 |

## 6. 聚类签名（1h 开仓特征）

- **簇 0**（n=46）：RSI=48.3424, MACD hist=N/A, 趋势多头%=100.0
- **簇 1**（n=74）：RSI=70.615, MACD hist=N/A, 趋势多头%=100.0
- **簇 2**（n=45）：RSI=34.0871, MACD hist=N/A, 趋势多头%=8.89
- **簇 3**（n=23）：RSI=56.3147, MACD hist=N/A, 趋势多头%=60.87
- **簇 4**（n=41）：RSI=54.5596, MACD hist=N/A, 趋势多头%=0.0

## 7. 与 H1–H7 假设对照

- 持仓 >24h 占比：**37.12%**
- 开仓时 1h 趋势多头（EMA20>EMA50）占比：**60.26%**
- 开仓 RSI14 中位数：**54.6**
- 盈利单 RSI 中位 **55.34** vs 亏损单 **53.06**

## 8. 规则化结论（本地统计，非 LLM）

- 共 **229** 个持仓回合纳入分析；中位持仓 **8.7** 小时，支持「波段而非剥头皮」假设（H4）。
- 开仓时约 **60.26%** 处于 1h EMA 多头排列，可与趋势过滤策略（H1）对照验证。
- 最大聚类（簇 1，n=74）平均 RSI 70.615，可作为典型入场状态参考。

## 9. 可视化结论（推荐）

在浏览器中打开：**[session_dashboard.html](../../analysis/output/session_dashboard.html)**  
含 KPI 卡片、文字结论与全套图表。

或运行：`python -m analysis.coolish_archive.session_visualize`

## 10. 图表

![总览仪表盘](../../analysis/output/session_overview_dashboard.png)

![RSI by outcome](../../analysis/output/session_win_loss_rsi.png)
![MACD delta](../../analysis/output/open_vs_close_macd_hist.png)
![Clusters](../../analysis/output/session_cluster_heatmap.png)

## 11. 局限

1. Binance 与 BitMEX 价格/资金费存在差异。
2. 段内 `realisedPnl` 仅覆盖部分成交行，PnL 对齐钱包有残差。
3. 指标按 K 线 `open_time` backward asof，与精确成交毫秒可能有 1 根 K 线误差。

完整 JSON：`analysis/output/session_analytics.json`
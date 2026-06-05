# 策略配置手册

本文档说明如何选择策略、通用配置字段，以及各内置策略的参数含义与调参建议。

## 如何选择策略

### 纸交易 / 实盘入口

```bash
trader --mode paper --strategy btc_multi_indicator
trader --mode paper --strategy btc              # 别名 → btc_multi_indicator
trader --mode paper --strategy btc_multi_indicator_v2
trader --mode paper --strategy btc_v2           # 别名 → 日内 V2
trader --mode paper --strategy volume_profile
trader --mode paper --strategy ict
trader --mode paper --strategy funding_arb
```

- CLI 未指定 `--strategy` 时使用 `config/default.yaml` 中 `strategy.default`（默认 `btc_multi_indicator`）。
- 若 `strategies.<name>.enabled: false`，程序会退出；加 `--force` 可跳过检查。

### 回测

```bash
python -m apps.backtest.main --strategy btc_multi_indicator --start 2024-06-01
python -m apps.backtest.main --strategy volume_profile --start 2024-06-01 --force
python -m apps.backtest.main --strategy ict --start 2024-06-01
```

仅 `kind=kline` 的策略支持回测；`funding_arb` 为轮询型，不走 K 线回测。

---

## 通用字段

各策略在 `config/default.yaml` 的 `strategies.<name>` 下配置：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否允许启动（默认 `true` 仅 btc；VP/ICT 默认 `false`） |
| `symbol` | 交易对，默认 `BTCUSDT` |
| `timeframe` | 主信号周期，通常 `5m` |
| `max_position_usdt` | 最大名义仓位（USDT） |
| `tp_pct` / `sl_pct` | 止盈 / 止损比例（相对开仓价） |

风控 `risk.*` 对所有 K 线策略共用；回测区间见 `backtest.*`。

---

## btc_multi_indicator

多指标 v2：5m 得分 + 1h EMA 趋势过滤，波段持仓（TP/SL 为主，强反向信号才反手）。

| 字段 | 默认 | 说明 |
|------|------|------|
| `trend_timeframe` | `1h` | 趋势过滤周期 |
| `signal_threshold` | `0.65` | 开仓得分阈值 |
| `reversal_threshold` | `0.75` | 反手阈值 |
| `require_1h_trend` | `true` | 必须与 1h 趋势同向 |
| `rsi_long_min/max` | `45` / `68` | 5m 做多 RSI 区间 |
| `rsi_short_min/max` | `32` / `55` | 5m 做空 RSI 区间 |
| `rsi_1h_long_max` | `72` | 1h RSI 过热不追多 |

**调参建议**：两年样本显示得分上限约 **0.60**，`signal_threshold=0.65` 可能长期无开仓。回测/实盘前建议先试 **0.55–0.60**；默认 YAML 未改以免未经确认影响实盘。

---

## btc_multi_indicator_v2（日内）

在波段版评分框架上的**日内取向**变体：更低阈值、5m 趋势门控、1h 软过滤、更紧 TP/SL。

| 字段 | 默认 | 说明 |
|------|------|------|
| `signal_threshold` | `0.48` | 开仓阈值（可提高至 0.52 降频） |
| `reversal_threshold` | `0.62` | 反手阈值 |
| `min_score_edge` | `0.05` | 多空得分最小差 |
| `require_1h_trend` | `false` | 不强制 1h 同向 |
| `require_5m_trend` | `true` | 5m EMA20/50 与方向一致 |
| `use_1h_soft_filter` | `true` | 1h 逆势时需 +0.08 得分 |
| `use_position_pct` / `position_pct` | `true` / `0.30` | 按权益 30% 复利开仓 |
| `tp_pct` / `sl_pct` | `0.015` / `0.01` | 开仓时挂止盈 / 初始止损（相对开仓价） |
| `trail_stop_enabled` | `true` | 持仓中启用移动止损 |
| `trail_levels` | `[0.50,0.25]`, `[0.80,0.40]` | 向 TP 推进 50%→止损抬至 25% 位；推进 80%→抬至 40% 位 |
| `rsi_oversold_max` | `15` | 做多须 5m RSI ≤ 此值 |
| `rsi_overbought_min` | `75` | 做空须 5m RSI ≥ 此值 |
| `vol_spike_ratio` | `1.8` | 相对 20 均量放量阈值 |
| `vol_surge_mult` | `1.25` | 相对上一根 5m 量能跳升倍数 |
| `require_vol_spike_for_entry` | `false` | `true` 时仅放量 K 线可开仓 |
| `trend_penalty_1h` | `0.12` | 1h 逆势软扣分（波段硬扣 0.35） |

```bash
python -m apps.backtest.main --strategy btc_multi_indicator_v2 --force --start 2024-06-01
```

### V2 参数扫回测

批量改参、回测并写入对比表（`reports/sweeps/btc_v2/`）：

```bash
# 网格扫参（2000 组：signal 0.42–0.60、tp/sl 扩区间、trail 开/关；建议 --resume）
python scripts/sweep_btc_v2.py --grid config/sweeps/btc_v2_grid.yaml --resume
python scripts/sweep_btc_v2.py --grid config/sweeps/btc_v2_grid.yaml --max-runs 48 --resume
# RSI / 放量 / 仓位比例 用 --set 单点试探，例如：
python scripts/sweep_btc_v2.py --set vol_spike_ratio=2.0 --set position_pct=0.35 --resume

# 单次改参并追加记录
python scripts/sweep_btc_v2.py --set signal_threshold=0.52 --set vol_spike_ratio=2.0

# 详细 JSON 报告（含 trades / 月度 PnL）
python scripts/run_backtest_report.py --strategy btc_multi_indicator_v2 --set signal_threshold=0.52
```

产出文件：

| 文件 | 说明 |
|------|------|
| `reports/sweeps/btc_v2/runs/{run_id}.json` | 单次完整结果 + `params` 快照 |
| `reports/sweeps/btc_v2/runs.jsonl` | 追加日志（每行 summary + params） |
| `reports/sweeps/btc_v2/leaderboard.csv` | 全量对比表（按净盈亏排序） |
| `reports/sweeps/btc_v2/leaderboard.json` | 同上，JSON 数组 |

网格 YAML 字段：`base.start/end`、`grid.*`（参数列表）、`backtest_overrides.risk`（可选，如对齐 `max_single_order_usdt`）。

**注意**：两年全量单次约 8–9 分钟；192 组网格需数小时，请用 `--max-runs` 或缩短 `end` 试跑。

---

## volume_profile

滚动 Volume Profile：POC / Value Area（VAH/VAL）/ HVN / LVN。

| 字段 | 默认 | 说明 |
|------|------|------|
| `profile_timeframe` | `5m` | 构建 VP 的 K 线周期 |
| `lookback_bars` | `288` | 滚动窗口（5m 约 24h） |
| `value_area_pct` | `0.70` | Value Area 成交量占比 |
| `bin_count` | `50` | 价格分箱数 |
| `tick_size` | `10` | 分箱步长（USDT） |
| `entry_mode` | `val_poc_bounce` | `val_poc_bounce` 或 `hvn_lvn_reversion` |
| `require_trend_1h` | `true` | 1h EMA 趋势过滤 |
| `min_distance_to_poc_pct` | `0.003` | 与 POC 最小距离 |

**逻辑概要**：回踩 VAL/POC 做多、VAH/POC 受阻做空；持仓主要靠 TP/SL，有效突破 VA 可反手。

---

## ict

ICT 风格：1h 结构偏向 + 5m FVG/OB/流动性扫荡 + Premium/Discount。

| 字段 | 默认 | 说明 |
|------|------|------|
| `structure_timeframe` | `1h` | 结构 / 偏向周期 |
| `swing_lookback` | `3` | 摆动点 fractal 半径 |
| `displacement_atr_mult` | `1.5` | OB 位移蜡烛 ATR 倍数 |
| `fvg_min_gap_pct` | `0.001` | 最小 FVG 缺口比例 |
| `ob_max_age_bars` | `48` | OB 最大有效 K 线数 |
| `liquidity_equal_threshold_pct` | `0.0005` | 等高/等低判定阈值 |
| `require_premium_discount` | `true` | Discount 做多 / Premium 做空 |
| `ote_low` / `ote_high` | `0.62` / `0.79` | 斐波那契 OTE 区间 |
| `killzone_enabled` | `false` | 仅在 UTC 窗口内开新仓 |
| `killzone_utc` | 见 YAML | 如 `07:00-10:00` |
| `signal_threshold` | `0.6` | 开仓得分 |
| `reversal_threshold` | `0.75` | 反手得分 + CHoCH |

---

## funding_arb

跨所资金费率套利（`kind=polling`），独立事件循环，非 K 线策略。

| 字段 | 默认 | 说明 |
|------|------|------|
| `min_funding_spread` | `0.0002` | 最小利差 |
| `max_position_usdt` | `5000` | 最大仓位 |

---

## 回测与数据

1. 下载历史 K 线：`python scripts/download_history.py`
2. 回测：`python -m apps.backtest.main --strategy <name> --start YYYY-MM-DD`
3. 配置：`backtest.db_path`、`backtest.warmup_bars`、`backtest.intervals`

---

## 扩展第三方策略

内置注册在 `src/strategies/registry.py` 的 `bootstrap()`：

```python
from src.strategies.registry import register, register_alias

register("my_strategy", MyStrategy.from_config, kind="kline", description="...")
register_alias("my", "my_strategy")
```

策略需实现 `from_config(cfg: dict)`；K 线类建议继承 `KlineStrategy` 并实现 `on_kline`。

可选：在 `pyproject.toml` 增加 `[project.entry-points."trade_btc.strategies"]` 供外部包注册（首版仅内置表 + 上述 API）。

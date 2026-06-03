# trade-btc 使用手册

## 目录

1. [快速开始](#快速开始)
2. [配置说明](#配置说明)
3. [运行模式](#运行模式)
4. [BTC 多指标策略](#btc-多指标策略)
5. [资金费率套利策略](#资金费率套利策略)
6. [风险控制](#风险控制)
7. [回测框架](#回测框架)
8. [常见问题](#常见问题)

---

## 快速开始

### 环境要求

- Python 3.11+
- 约 200MB 磁盘（含 SQLite 数据）

### 安装步骤

```bash
# 1. 克隆仓库
git clone <repo-url>
cd trade-btc

# 2. 安装依赖
pip install -e ".[dev]"

# 3. 配置密钥（纸交易可留空）
cp config/secrets.local.yaml.example config/secrets.local.yaml
# 按需填入 API Key

# 4. 启动纸交易（策略详见 docs/strategies.md）
trader --mode paper --strategy btc_multi_indicator
# 或别名
trader --mode paper --strategy btc

# Volume Profile / ICT（需在 default.yaml 中 enabled: true 或加 --force）
trader --mode paper --strategy volume_profile --force
trader --mode paper --strategy ict --force
```

---

## 配置说明

策略选择与各策略参数见 **[docs/strategies.md](strategies.md)**。

### 主配置 `config/default.yaml`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `mode` | `paper` | 运行模式：paper / testnet / live |
| `exchanges.binance.enabled` | `true` | 是否启用 Binance |
| `exchanges.binance.testnet` | `false` | 是否使用 Testnet |
| `strategies.btc_multi_indicator.signal_threshold` | `0.65` | 开仓信号得分阈值（0~1） |
| `strategies.btc_multi_indicator.reversal_threshold` | `0.75` | 反手信号阈值（高于开仓，减少频繁翻转） |
| `strategies.btc_multi_indicator.require_1h_trend` | `true` | 必须与 1h EMA 趋势同向才开仓/反手 |
| `strategies.btc_multi_indicator.max_position_usdt` | `10000` | 最大持仓名义价值 |
| `strategies.btc_multi_indicator.tp_pct` | `0.05` | 止盈比例（5%，波段） |
| `strategies.btc_multi_indicator.sl_pct` | `0.025` | 止损比例（2.5%） |
| `strategies.btc_multi_indicator.rsi_long_min/max` | `45` / `68` | 5m 做多 RSI 区间（参考 Coolish 盈利单） |
| `strategies.btc_multi_indicator.rsi_1h_long_max` | `72` | 1h RSI 超过此值不追多 |
| `strategies.funding_arb.min_funding_spread` | `0.0002` | 套利最小利差（0.02%） |
| `risk.max_daily_loss_usdt` | `1000` | 日内最大亏损熔断阈值 |
| `risk.max_consecutive_losses` | `5` | 连续亏损次数熔断阈值 |

### 密钥 `config/secrets.local.yaml`（不入库）

```yaml
exchanges:
  binance:
    api_key: "YOUR_BINANCE_API_KEY"
    api_secret: "YOUR_BINANCE_API_SECRET"
  hyperliquid:
    private_key: "0x..."           # EVM 私钥
    wallet_address: "0x..."
  aster:
    api_key: "YOUR_ASTER_API_KEY"
    api_secret: "YOUR_ASTER_API_SECRET"
```

### 环境变量覆盖（优先级最高）

```bash
TRADER_MODE=paper                    # 覆盖运行模式
BINANCE_API_KEY=xxx
BINANCE_API_SECRET=xxx
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_WALLET_ADDRESS=0x...
ASTER_API_KEY=xxx
ASTER_API_SECRET=xxx
```

---

## 运行模式

### paper（纸交易，默认）

- 使用真实行情（Binance WebSocket）
- 不与真实交易所下单，内部用 `PaperExchange` 撮合
- 完整手续费 + 滑点模拟
- API Key 可不配置（只订阅公开行情）

```bash
trader --mode paper --strategy btc --log-level INFO
```

### testnet（测试网）

- 接入 Binance Testnet，真实 API 调用但无真实资金
- 需要 Testnet 专用 API Key（在 testnet.binancefuture.com 申请）
- 配置中设置 `exchanges.binance.testnet: true`

```bash
trader --mode testnet --strategy btc
```

### live（实盘）

- 真实资金交易，启动时会弹出二次确认提示
- API 权限最小化：只开启"合约交易"权限，**禁止开启提现权限**
- 建议先在 paper 和 testnet 充分验证后再切换

```bash
trader --mode live --strategy btc
```

> **紧急停止**：修改 `config/default.yaml` 中 `mode: paper` 即可立即停止真实交易，无需杀进程。

---

## BTC 多指标策略

> v2 参数依据 [Coolish 持仓回合指标研究](research/coolish_sessions_indicator_report.md)：波段持仓、1h 趋势过滤、放宽 TP/SL。

### 指标体系（5m 主信号 + 1h 趋势硬过滤）

| 指标 | 参数 | 权重 | 多头条件 | 空头条件 |
|------|------|------|----------|----------|
| EMA 趋势 | EMA20 vs EMA50 | 0.25 | EMA20 > EMA50 | EMA20 < EMA50 |
| MACD 柱 | 12/26/9 | 0.25 | MACD 柱 > 0 | MACD 柱 < 0 |
| RSI | 14 周期 | 0.20 | RSI 在 45~68（5m） | RSI 在 32~55（5m） |
| 布林带位置 | 20/2σ | 0.15 | 价格 > 中轨 | 价格 < 中轨 |
| 成交量 | MA20 倍率 | 0.15 | 量比 ≥ 1.2（占优方向） | 同上 |

**1h 趋势（`require_1h_trend: true`）**：开多/反手多必须 1h EMA20>EMA50；开空必须 1h 空头排列。1h RSI > 72 时不追多。

### 信号生成逻辑

```
开仓：得分 >= signal_threshold (0.65) 且 1h 趋势同向 且 得分高于反向
反手：得分 >= reversal_threshold (0.75) 且满足同上（更难触发）
持仓中得分回落：不平仓，等待 TP/SL
```

### 下单规则

1. **策略输出目标仓位**（`TargetPosition`），不直接输出订单
2. **执行路由对账**：比较目标仓位与当前实仓，计算 delta
3. **方向反转**：先平掉原有持仓，再开新方向
4. **市价单开仓** + 自动挂止盈/止损条件单

### 止盈止损

- 止盈：`当前价格 × (1 + tp_pct)`（多头），`× (1 - tp_pct)`（空头）
- 止损：`当前价格 × (1 - sl_pct)`（多头），`× (1 + sl_pct)`（空头）
- 先用**交易所原生条件单**（TAKE_PROFIT_MARKET / STOP_MARKET）
- 若条件单被撤，策略侧 `PaperMatchingEngine` 作为备份

---

## 资金费率套利策略

### 套利原理

```
套利净收益 = 高资金费率 - 低资金费率 - 双腿手续费 - 双腿滑点
条件：套利净收益 > min_funding_spread
```

在**高费率平台做空**（收取资金费率）、**低费率平台做多**，形成 Delta 中性组合。

### 执行流程

1. 每 30 秒采集三平台资金费率
2. 计算最大利差，扣除估算手续费（0.08%）+ 滑点（0.1%）
3. 利差 > 阈值则产生套利信号
4. **双腿同步下单**（`asyncio.gather`）
5. 任一腿失败 → 紧急关闭已成交腿
6. 资金费率结算后检查是否需要平仓

### 风险说明

- 套利并非无风险：基差波动、流动性不足、强平均可能造成亏损
- 系统设置了最大同时持仓数（默认 3 组），腿间敞口通过紧急平仓保护

---

## 风险控制

### 触发条件与处置

| 风控项 | 触发阈值 | 处置方式 |
|--------|---------|----------|
| 单笔名义价值 | > 5,000 USDT | 拦截该笔订单 |
| 持仓总名义价值 | > 20,000 USDT | 调整为上限数量 |
| 日内已实现亏损 | > 1,000 USDT | 触发熔断，停止所有开仓 |
| 连续亏损次数 | > 5 次 | 触发熔断 |
| 价格单次跳变 | > ±5% | 拦截下单，等待价格恢复 |
| WebSocket 断线 | — | 仅允许减仓操作（reduce_only） |

### 手动重置熔断

```python
# 在代码中调用
risk_manager.manual_reset_circuit_break()

# 或重启进程（日内亏损会归零）
```

---

## 回测框架

回测与纸交易共用同一套策略参数（`config/default.yaml` 中 `strategies.btc_multi_indicator` 与 `risk` 段），
多周期按 `close_time` 合并回放（同 timestamp 先 1h 后 5m），指标在 `IndicatorPipeline` 中实时计算。

### 1. 下载/更新历史数据

```bash
python -m scripts.download_history --start 2024-06-01
# 或指定周期
python -m scripts.download_history --start 2024-06-01 --intervals 5m,1h
# 仅检查缺口（不下载）
python -m scripts.download_history --start 2024-06-01 --verify-only
```

数据写入 `data/marketdata.db`（与纸交易落盘共用）。脚本会**扫描中间缺口**并逐段填补（不仅补首尾）；支持断点续传，网络中断后重跑即可继续。完成后输出覆盖率验证。

### 2. 运行回测

```bash
python -m apps.backtest.main --config-dir config --strategy btc_multi_indicator --start 2024-06-01
python -m apps.backtest.main --strategy volume_profile --start 2024-06-01 --force
# 可选导出 JSON
python -m apps.backtest.main --start 2024-06-01 --output reports/backtest.json
```

输出示例：

```
回测区间: 2024-06-01 ~ 2026-06-01
总平仓: 87 笔 | 胜率: 54.0% (47W / 40L)
止盈: 31 | 止损: 28 | 反转: 28
平均持仓: 8.5 小时
累计净盈亏: +1,240 USDT | 最大回撤: 8.3%
```

### 回测配置（`config/default.yaml` → `backtest` 段）

| 字段 | 说明 |
|------|------|
| `db_path` | 历史 K 线 SQLite 路径 |
| `start` / `end` | 默认回测区间 |
| `intervals` | 回放周期，策略 v2 需 `5m` + `1h` |
| `warmup_bars` | 正式统计前 5m 指标预热根数 |
| `initial_balance` | 初始资金 |

### 胜率定义

以**完整平仓回合**为一笔交易（与 Web 看板成交记录一致）：

- **胜率** = 盈利笔数 / 总平仓笔数
- **exit_breakdown**：按平仓原因拆分（止盈 / 止损 / 信号反转）
- 样本量较小时置信区间较宽，请同时关注**总交易次数**

### Python API

```python
from src.backtest.runner import BacktestRunner
from src.marketdata.storage import MarketDataStorage
from src.strategies.factory import create_strategy
from src.core.config import load_config
from src.core.models import Exchange

async def run_backtest():
    config = load_config("config")
    storage = MarketDataStorage("data/marketdata.db")
    await storage.connect()
    strategy = create_strategy("btc_multi_indicator", config, check_enabled=False)
    runner = BacktestRunner(storage, strategy, warmup_bars=500, intervals=["5m", "1h"])
    report = await runner.run(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        start_ms=1717200000000,  # 2024-06-01
    )
    print(f"胜率: {report.win_rate * 100:.1f}% ({report.total_trades} 笔)")
    await storage.close()
```

---

## 常见问题

**Q: 纸交易模式下需要配置 API Key 吗？**
A: 不需要。公开行情（K 线/盘口）无需签名。如需测试下单接口，需要 Key，但纸交易下所有下单会走 `PaperExchange`。

**Q: 如何只看行情不交易？**
A: 将 `signal_threshold` 调整到 `1.0`（不可能满足），策略就不会产生信号。

**Q: 熔断后如何恢复？**
A: 熔断只阻止新开仓，不影响已有持仓。第二天会自动重置日内亏损计数（需重启进程），或调用 `risk_manager.manual_reset_circuit_break()`。

**Q: Hyperliquid 签名失败怎么办？**
A: 确保已安装 `eth_account` 和 `eth_abi`（`pip install eth-account eth-abi`），并且私钥格式为 `0x` 开头的十六进制字符串。

**Q: 如何增加新的交易所？**
A: 继承 `src/connectors/base.py` 中的 `IExchange` 并实现所有抽象方法，然后在 `ExecutionRouter` 中注册即可，无需修改策略层。

## 看板
如果还是看不到，可以在 Cursor 中按 Ctrl+Shift+P 打开命令面板，输入 canvas 查找相关命令打开 Canvas 面板。btc-trading-dashboard


#执行回测
python scripts/backtest_analysis.py
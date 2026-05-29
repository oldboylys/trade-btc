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

# 4. 启动纸交易
trader --mode paper --strategy btc
```

---

## 配置说明

### 主配置 `config/default.yaml`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `mode` | `paper` | 运行模式：paper / testnet / live |
| `exchanges.binance.enabled` | `true` | 是否启用 Binance |
| `exchanges.binance.testnet` | `false` | 是否使用 Testnet |
| `strategies.btc_multi_indicator.signal_threshold` | `0.6` | 信号置信度阈值（0~1） |
| `strategies.btc_multi_indicator.max_position_usdt` | `10000` | 最大持仓名义价值 |
| `strategies.btc_multi_indicator.tp_pct` | `0.03` | 止盈比例（3%） |
| `strategies.btc_multi_indicator.sl_pct` | `0.015` | 止损比例（1.5%） |
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
trader --mode paper --strategy btc
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

### 指标体系（5m 主信号 + 1h 趋势过滤）

| 指标 | 参数 | 权重 | 多头条件 | 空头条件 |
|------|------|------|----------|----------|
| EMA 趋势 | EMA20 vs EMA50 | 0.25 | EMA20 > EMA50 | EMA20 < EMA50 |
| MACD 柱 | 12/26/9 | 0.25 | MACD 柱 > 0 | MACD 柱 < 0 |
| RSI | 14 周期 | 0.20 | RSI 在 40~65 | RSI > 70 或 < 35 |
| 布林带位置 | 20/2σ | 0.15 | 价格 > 中轨 | 价格 < 中轨 |
| 成交量 | MA20 倍率 | 0.15 | 量比 ≥ 1.2 | 量比 ≥ 1.2（双向增强） |

**1h 趋势过滤**：若 1h EMA20/50 方向与信号相反，对应得分惩罚 -0.30。

### 信号生成逻辑

```
多头得分 >= signal_threshold (默认 0.6)  →  开多
空头得分 >= signal_threshold             →  开空
两者均低于阈值                            →  持仓不动或平仓
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

### 准备历史数据

系统在纸交易运行时自动将 K 线落盘到 `data/marketdata.db`（SQLite）。也可手动批量下载：

```python
from src.connectors.binance.connector import BinanceConnector
from src.marketdata.storage import MarketDataStorage

async def download():
    storage = MarketDataStorage("data/marketdata.db")
    await storage.connect()
    binance = BinanceConnector()
    await binance.connect()
    klines = await binance.get_klines("BTCUSDT", "5m", limit=1000)
    await storage.save_klines_bulk(klines)
```

### 运行回测

```python
from src.backtest.runner import BacktestRunner
from src.marketdata.storage import MarketDataStorage
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy
from src.core.models import Exchange

async def run_backtest():
    storage = MarketDataStorage("data/marketdata.db")
    await storage.connect()

    strategy = BTCMultiIndicatorStrategy(signal_threshold=0.55)
    runner = BacktestRunner(storage, strategy)

    report = await runner.run(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        interval="5m",
    )
    print(f"收益率: {report.return_pct:.2f}%")
    print(f"最大回撤: {report.max_drawdown:.2f}%")
    print(f"总手续费: {report.total_fee:.2f} USDT")
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

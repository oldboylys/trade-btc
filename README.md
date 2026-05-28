# trade-btc

多交易所加密货币自动交易系统：BTC多指标策略 + 资金费率跨平台套利。

## 架构概览

```
apps/trader/        主程序入口（CLI）
src/
  core/             事件总线、配置、时钟、日志、数据模型、运行模式
  connectors/       Binance / Hyperliquid / Aster 连接器（REST + WS）
  marketdata/       行情订阅、K线聚合、落盘、回放驱动器
  indicators/       多周期指标流水线（EMA/MACD/RSI/BB/ATR）
  strategies/       BTC多指标策略 v1 + 资金费率套利策略
  execution/        执行路由：目标仓位→下单/撤单/止盈止损
  risk/             风控：仓位/单笔/熔断/异常行情/断连保护
  sim/              纸交易撮合引擎、手续费/滑点模型、持仓账本
  backtest/         回测回放框架
config/             配置文件（default.yaml + secrets.local.yaml）
tests/              单元测试 + 集成测试
```

## 快速开始

### 1. 安装依赖

```bash
pip install -e ".[dev]"
```

### 2. 配置

```bash
cp config/secrets.local.yaml.example config/secrets.local.yaml
# 编辑 secrets.local.yaml，填入 API Key（纸交易模式不需要下单权限）
```

### 3. 运行纸交易

```bash
# BTC 多指标策略（纸交易）
trader --mode paper --strategy btc

# 资金费率套利（纸交易）
trader --mode paper --strategy funding_arb
```

### 4. 运行测试

```bash
pytest tests/unit -v
pytest tests/integration -v
```

## 运行模式

| 模式 | 说明 |
|------|------|
| `paper` | 纸交易，使用真实行情但不真实下单 |
| `testnet` | 接入交易所 Testnet，真实下单但无真实资金 |
| `live` | 真实交易（需要明确确认） |

## 风险说明

- 资金费率套利并非无风险：基差波动、流动性不足、强平风险等均可能导致亏损
- 必须先在纸交易/测试网充分验证后再开启实盘
- 密钥权限最小化：建议只开启"合约交易"权限，禁止提现
- 紧急情况：修改 config/default.yaml 中 `mode: paper` 即可关闭真实交易

## 配置说明

主配置：`config/default.yaml`  
密钥（不入库）：`config/secrets.local.yaml`（参考 `secrets.local.yaml.example`）

环境变量覆盖：
- `TRADER_MODE=paper|testnet|live`
- `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- `HYPERLIQUID_PRIVATE_KEY` / `HYPERLIQUID_WALLET_ADDRESS`
- `ASTER_API_KEY` / `ASTER_API_SECRET`

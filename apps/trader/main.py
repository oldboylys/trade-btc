"""交易系统主程序入口."""
from __future__ import annotations

import asyncio
import signal
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

import click

# 将项目根目录加入 Python 路径
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.config import get_config
from src.core.events import get_bus
from src.core.logging import configure_logging, get_logger
from src.core.mode import ModeGuard
from src.core.models import Exchange, TradingMode
from src.core.secrets import load_secrets
from src.risk.manager import RiskConfig, RiskManager


logger = get_logger("trader.main")


async def _run_paper_btc(config: dict) -> None:
    """纸交易 BTC 多指标策略主循环."""
    from src.sim.paper_exchange import PaperExchange
    from src.sim.slippage import FeeModel, SlippageModel
    from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy
    from src.execution.router import ExecutionRouter
    from src.marketdata.feed import MarketDataFeed, EVT_KLINE_CLOSED
    from src.marketdata.storage import MarketDataStorage
    from src.connectors.binance.connector import BinanceConnector

    strat_cfg = config.get("strategies", {}).get("btc_multi_indicator", {})
    risk_cfg_raw = config.get("risk", {})

    # 初始化组件
    paper_ex = PaperExchange(
        initial_balance=Decimal(str(strat_cfg.get("max_position_usdt", 100000))),
        fee_model=FeeModel(),
        slippage_model=SlippageModel(),
    )
    await paper_ex.connect()

    risk = RiskManager(RiskConfig(
        max_position_usdt=Decimal(str(risk_cfg_raw.get("max_position_usdt", 20000))),
        max_single_order_usdt=Decimal(str(risk_cfg_raw.get("max_single_order_usdt", 5000))),
        max_daily_loss_usdt=Decimal(str(risk_cfg_raw.get("max_daily_loss_usdt", 1000))),
    ))

    mode_guard = ModeGuard(TradingMode.PAPER)
    router = ExecutionRouter(paper_ex, risk, mode_guard)

    strategy = BTCMultiIndicatorStrategy(
        symbol=strat_cfg.get("symbol", "BTCUSDT"),
        exchange=Exchange.BINANCE,
        signal_threshold=float(strat_cfg.get("signal_threshold", 0.6)),
        max_position_usdt=Decimal(str(strat_cfg.get("max_position_usdt", 10000))),
        tp_pct=float(strat_cfg.get("tp_pct", 0.03)),
        sl_pct=float(strat_cfg.get("sl_pct", 0.015)),
    )

    storage = MarketDataStorage(config.get("market_data", {}).get("db_path", "data/marketdata.db"))
    feed = MarketDataFeed(storage)

    # 用真实 Binance 行情（只读，不下单）
    secrets = load_secrets()
    binance = BinanceConnector(
        api_key=secrets.binance.api_key,
        api_secret=secrets.binance.api_secret,
        testnet=config.get("exchanges", {}).get("binance", {}).get("testnet", False),
    )
    await binance.connect()
    feed.register_exchange("binance", binance)

    # 订阅行情事件
    bus = get_bus()

    async def on_kline_closed(kline) -> None:
        paper_ex.feed_kline(kline)
        target = strategy.on_kline(kline)
        if target is not None:
            await router.execute(target)
            pos = paper_ex.position_book.get_position(kline.symbol)
            if pos:
                logger.info(
                    "position_snapshot",
                    symbol=kline.symbol,
                    qty=float(pos.qty),
                    entry=float(pos.entry_price),
                    upnl=float(pos.unrealized_pnl),
                    balance=float(paper_ex.balance),
                )

    bus.subscribe(EVT_KLINE_CLOSED, on_kline_closed)

    await feed.start()
    symbol = strat_cfg.get("symbol", "BTCUSDT")
    await feed.subscribe("binance", symbol, "1m")

    logger.info("paper_btc_running", symbol=symbol, mode="paper")
    await get_bus().run()


async def _run_funding_arb(config: dict) -> None:
    """资金费率套利模式."""
    from src.strategies.funding_arb.strategy import FundingArbStrategy
    from src.strategies.funding_arb.executor import FundingArbExecutor
    from src.connectors.binance.connector import BinanceConnector
    from src.core.secrets import load_secrets

    arb_cfg = config.get("strategies", {}).get("funding_arb", {})
    secrets = load_secrets()

    exchanges: dict = {}
    binance = BinanceConnector(
        api_key=secrets.binance.api_key,
        api_secret=secrets.binance.api_secret,
    )
    await binance.connect()
    exchanges["binance"] = binance

    strategy = FundingArbStrategy(
        symbols=["BTCUSDT"],
        min_spread=Decimal(str(arb_cfg.get("min_funding_spread", "0.0002"))),
        max_position_usdt=Decimal(str(arb_cfg.get("max_position_usdt", "5000"))),
    )
    strategy.register_exchange("binance", binance)

    executor = FundingArbExecutor(exchanges=exchanges)
    strategy.set_executor(executor)

    await strategy.start()
    logger.info("funding_arb_running")
    await get_bus().run()


@click.command()
@click.option("--mode", default=None, type=click.Choice(["paper", "testnet", "live"]),
              help="运行模式（覆盖 config 中的 mode）")
@click.option("--strategy", default="btc", type=click.Choice(["btc", "funding_arb"]),
              help="运行策略")
@click.option("--config-dir", default=None, help="配置目录")
@click.option("--log-level", default="INFO", help="日志级别")
def cli(mode: Optional[str], strategy: str, config_dir: Optional[str], log_level: str) -> None:
    """BTC 自动交易系统."""
    import os
    if mode:
        os.environ["TRADER_MODE"] = mode

    cfg = get_config(config_dir)
    running_mode = TradingMode(cfg.get("mode", "paper"))

    configure_logging(
        level=log_level,
        log_file=cfg.get("logging", {}).get("file"),
    )

    logger.info(
        "trader_starting",
        mode=running_mode.value,
        strategy=strategy,
    )

    # 安全检查：live 模式需要明确确认
    if running_mode == TradingMode.LIVE:
        click.confirm(
            "⚠️  You are running in LIVE mode with real funds. Continue?",
            abort=True,
        )

    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        logger.info("shutdown_signal_received")
        loop.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if strategy == "btc":
        coro = _run_paper_btc(cfg) if running_mode == TradingMode.PAPER else _run_paper_btc(cfg)
    else:
        coro = _run_funding_arb(cfg)

    try:
        loop.run_until_complete(coro)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.close()
        logger.info("trader_stopped")


if __name__ == "__main__":
    cli()

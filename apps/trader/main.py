"""交易系统主程序入口."""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

import click

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.config import get_config
from src.core.events import get_bus
from src.core.logging import configure_logging, get_logger
from src.core.models import TradingMode
from src.strategies.factory import create_strategy, get_strategy_kind
from src.strategies.registry import bootstrap, list_cli_choices, resolve_name
from src.strategies.runner import KlineStrategyRunner

logger = get_logger("trader.main")


async def _run_funding_arb(config: dict) -> None:
    """资金费率套利模式."""
    from decimal import Decimal

    from src.connectors.binance.connector import BinanceConnector
    from src.core.secrets import load_secrets
    from src.strategies.funding_arb.executor import FundingArbExecutor
    from src.strategies.funding_arb.strategy import FundingArbStrategy

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
        max_position_usdt=Decimal(str(arb_cfg.get("max_position_usdt", 5000))),
    )
    strategy.register_exchange("binance", binance)

    executor = FundingArbExecutor(exchanges=exchanges)
    strategy.set_executor(executor)

    await strategy.start()
    logger.info("funding_arb_running")
    await get_bus().run()


def _default_strategy_name(config: dict) -> str:
    return config.get("strategy", {}).get("default", "btc_multi_indicator")


@click.command()
@click.option("--mode", default=None, type=click.Choice(["paper", "testnet", "live"]),
              help="运行模式（覆盖 config 中的 mode）")
@click.option("--strategy", default=None, help="运行策略（见 docs/strategies.md）")
@click.option("--force", is_flag=True, help="忽略 strategies.<name>.enabled=false")
@click.option("--config-dir", default=None, help="配置目录")
@click.option("--log-level", default="INFO", help="日志级别")
def cli(
    mode: Optional[str],
    strategy: Optional[str],
    force: bool,
    config_dir: Optional[str],
    log_level: str,
) -> None:
    """BTC 自动交易系统."""
    import os

    bootstrap()
    if mode:
        os.environ["TRADER_MODE"] = mode

    cfg = get_config(config_dir)
    running_mode = TradingMode(cfg.get("mode", "paper"))
    strategy_name = strategy or _default_strategy_name(cfg)
    resolved = resolve_name(strategy_name)

    configure_logging(
        level=log_level,
        log_file=cfg.get("logging", {}).get("file"),
    )

    logger.info(
        "trader_starting",
        mode=running_mode.value,
        strategy=resolved,
    )

    if running_mode == TradingMode.LIVE:
        click.confirm(
            "⚠️  You are running in LIVE mode with real funds. Continue?",
            abort=True,
        )

    strat_cfg = cfg.get("strategies", {}).get(resolved, {})
    if not strat_cfg:
        raise click.ClickException(f"No config section strategies.{resolved}")

    if not force and not strat_cfg.get("enabled", True):
        raise click.ClickException(
            f"Strategy '{resolved}' is disabled (strategies.{resolved}.enabled=false). "
            "Use --force to override."
        )

    kind = get_strategy_kind(resolved)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if kind == "polling":
        if resolved != "funding_arb":
            create_strategy(resolved, cfg, check_enabled=not force)
        coro = _run_funding_arb(cfg)
    else:
        create_strategy(resolved, cfg, check_enabled=not force)
        coro = KlineStrategyRunner(resolved, cfg).run()

    try:
        loop.run_until_complete(coro)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.close()
        logger.info("trader_stopped")


if __name__ == "__main__":
    cli()

"""交易系统主程序入口."""
from __future__ import annotations

import asyncio
import datetime
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
from src.core.telegram import init_notifier
from src.risk.manager import RiskConfig, RiskManager
from src.web.status_store import init_store
from src.web.server import start_server


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
    tg_cfg = config.get("telegram", {})
    web_cfg = config.get("web", {})

    # 初始化状态存储
    store = init_store(mode="paper", strategy="btc_multi_indicator", symbol=strat_cfg.get("symbol", "BTCUSDT"))

    # 初始化 Telegram 通知器
    secrets = load_secrets()
    tg_token = secrets.telegram.bot_token or tg_cfg.get("bot_token", "")
    tg_chat = secrets.telegram.chat_id or tg_cfg.get("chat_id", "")
    tg_enabled = tg_cfg.get("enabled", True) and bool(tg_token) and bool(tg_chat)
    notifier = init_notifier(token=tg_token, chat_id=tg_chat, enabled=tg_enabled)

    if tg_enabled:
        logger.info("telegram_notifier_enabled", chat_id=tg_chat)
    else:
        logger.info("telegram_notifier_disabled")

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
    router = ExecutionRouter(paper_ex, risk, mode_guard, notifier=notifier)

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
    binance = BinanceConnector(
        api_key=secrets.binance.api_key,
        api_secret=secrets.binance.api_secret,
        testnet=config.get("exchanges", {}).get("binance", {}).get("testnet", False),
    )
    await binance.connect()
    feed.register_exchange("binance", binance)

    # 把 notifier 和 store 注入 position_book
    paper_ex.position_book.set_notifier(notifier)
    paper_ex.position_book.set_store(store)

    # 风控参数写入 store
    store.daily_loss_limit = float(risk_cfg_raw.get("max_daily_loss_usdt", 1000))
    store.max_position_usdt = float(strat_cfg.get("max_position_usdt", 10000))

    # 订阅行情事件
    bus = get_bus()

    async def on_kline_closed(kline) -> None:
        paper_ex.feed_kline(kline)

        # 更新状态存储：行情价格
        store.mark_price = float(kline.close)
        store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
        store.balance = float(paper_ex.balance)
        store.unrealized_pnl = float(paper_ex.position_book.total_unrealized_pnl())
        store.daily_realized_pnl = float(paper_ex.position_book.daily_realized_pnl)
        store.total_fee = float(paper_ex.position_book.total_fee)

        # 更新持仓状态
        pos_entry = paper_ex.position_book.get_position(kline.symbol)
        if pos_entry and pos_entry.qty > 0:
            store.has_position = True
            store.pos_side = pos_entry.side.value
            store.pos_qty = float(pos_entry.qty)
            store.pos_entry_price = float(pos_entry.entry_price)
            store.pos_mark_price = float(pos_entry.mark_price)
            store.pos_upnl = float(pos_entry.unrealized_pnl)
        else:
            store.has_position = False

        target = strategy.on_kline(kline)
        if target is not None:
            await router.execute(target)
            pos_entry = paper_ex.position_book.get_position(kline.symbol)
            if pos_entry:
                logger.info(
                    "position_snapshot",
                    symbol=kline.symbol,
                    qty=float(pos_entry.qty),
                    entry=float(pos_entry.entry_price),
                    upnl=float(pos_entry.unrealized_pnl),
                    balance=float(paper_ex.balance),
                )

    bus.subscribe(EVT_KLINE_CLOSED, on_kline_closed)

    # bookTicker 实时更新 mark price（kline WS 被代理拦截时的兜底）
    from src.marketdata.feed import EVT_ORDERBOOK
    _ob_count = 0

    async def on_orderbook(ob) -> None:
        nonlocal _ob_count
        _ob_count += 1
        if ob.bids and ob.asks:
            mid = (float(ob.bids[0][0]) + float(ob.asks[0][0])) / 2
            store.mark_price = round(mid, 2)
            store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
            if _ob_count == 1:
                logger.info("orderbook_price_first", price=store.mark_price)

    bus.subscribe(EVT_ORDERBOOK, on_orderbook)

    # 每次 tick 同步直接更新价格（绕过 bus，确保实时显示）
    _tick_count = 0

    def on_tick_sync(kline) -> None:
        nonlocal _tick_count
        _tick_count += 1
        store.mark_price = float(kline.close)
        store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
        if _tick_count == 1:
            logger.info("kline_tick_first", symbol=kline.symbol, price=float(kline.close))
        elif _tick_count % 120 == 0:
            logger.info("kline_tick_heartbeat", count=_tick_count, price=float(kline.close))

    await feed.start()
    symbol = strat_cfg.get("symbol", "BTCUSDT")
    await feed.subscribe("binance", symbol, "1m", on_tick=on_tick_sync)

    logger.info("paper_btc_running", symbol=symbol, mode="paper")

    # ── 系统启动通知 ──────────────────────────────────────
    start_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    if tg_enabled:
        await notifier._send(
            f"✅ <b>【连接成功】BTC 纸交易系统已启动</b>\n"
            f"品种：{symbol}\n"
            f"策略：BTC 多指标 v1\n"
            f"行情来源：Binance fapi 实时 1m K线\n"
            f"启动时间：{start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC+8\n"
            f"开仓/平仓将实时推送至此"
        )

    # ── 每小时持仓状态推送 ────────────────────────────────
    async def _hourly_status_task() -> None:
        """每小时发送一次持仓状态到 Telegram."""
        while True:
            await asyncio.sleep(3600)
            now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M UTC+8")
            bal = paper_ex.balance
            upnl = paper_ex.position_book.total_unrealized_pnl()
            total_realized = paper_ex.position_book.daily_realized_pnl
            all_pos = paper_ex.position_book.get_all_positions()

            if all_pos:
                pos = all_pos[0]
                pos_lines = (
                    f"持仓方向：{pos.side.value}\n"
                    f"持仓数量：{float(pos.qty):.4f} BTC\n"
                    f"开仓均价：${float(pos.entry_price):,.2f}\n"
                    f"当前标价：${float(pos.mark_price):,.2f}\n"
                    f"浮动盈亏：{'+' if upnl >= 0 else ''}{float(upnl):,.2f} USDT\n"
                )
            else:
                pos_lines = "当前无持仓\n"

            msg = (
                f"📈 <b>【每小时状态】{symbol}</b>\n"
                f"时间：{now}\n"
                f"{'─'*24}\n"
                f"{pos_lines}"
                f"{'─'*24}\n"
                f"可用余额：${float(bal):,.2f} USDT\n"
                f"今日已实现 PnL：{'+' if total_realized >= 0 else ''}{float(total_realized):,.2f} USDT\n"
                f"<i>模式：纸交易 PAPER</i>"
            )
            if tg_enabled:
                await notifier._send(msg)
            logger.info("hourly_status_sent", balance=float(bal), upnl=float(upnl))

    # ── 启动 Web Dashboard 服务 ────────────────────────────
    web_host = web_cfg.get("host", "127.0.0.1")
    web_port = int(web_cfg.get("port", 8080))
    logger.info("dashboard_server_starting", host=web_host, port=web_port)
    try:
        web_runner = await start_server(host=web_host, port=web_port)
    except Exception as exc:
        logger.error("dashboard_server_failed", error=str(exc), exc_info=True)
        web_runner = None

    # ── 启动时同步拉取一次价格，确认 REST 可达 ────────────────
    try:
        mp = await binance.get_mark_price(symbol)
        store.mark_price = float(mp.mark_price)
        store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
        logger.info("mark_price_init", price=float(mp.mark_price))
    except Exception as exc:
        logger.warning("mark_price_init_failed", error=str(exc))

    # ── REST 轮询兜底：WebSocket kline 不通时通过 REST 驱动策略 ──
    _last_kline_open_time: int = 0

    async def _rest_kline_poll_task() -> None:
        nonlocal _last_kline_open_time
        while True:
            await asyncio.sleep(10)
            try:
                # 取最新两根已闭合的 1m K线
                klines = await binance.get_klines(symbol, "1m", limit=2)
                if not klines:
                    continue
                # 取倒数第二根（最新已闭合）
                closed_kline = klines[-2] if len(klines) >= 2 else klines[-1]
                # 价格用最新一根的收盘价（含当前未闭合）
                latest = klines[-1]
                store.mark_price = float(latest.close)
                store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")

                # 只有新闭合的 K 线才触发策略
                if closed_kline.open_time != _last_kline_open_time and closed_kline.is_closed:
                    _last_kline_open_time = closed_kline.open_time
                    logger.info("rest_kline_closed", symbol=symbol, close=float(closed_kline.close),
                                open_time=closed_kline.open_time)
                    bus = get_bus()
                    await bus.publish("kline_closed", closed_kline)
            except Exception as exc:
                logger.warning("rest_kline_poll_error", error=str(exc))

    # ── 启动后台任务并运行主循环 ──────────────────────────
    hourly_task = asyncio.create_task(_hourly_status_task())
    price_poll_task = asyncio.create_task(_rest_kline_poll_task())

    try:
        await get_bus().run()
    finally:
        for t in (hourly_task, price_poll_task):
            t.cancel()
        if web_runner is not None:
            await web_runner.cleanup()
        # 断开通知
        stop_time = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        if tg_enabled:
            bal = paper_ex.balance
            upnl = paper_ex.position_book.total_unrealized_pnl()
            realized = paper_ex.position_book.daily_realized_pnl
            await notifier._send(
                f"🔴 <b>【断开连接】BTC 纸交易系统已停止</b>\n"
                f"停止时间：{stop_time} UTC+8\n"
                f"{'─'*24}\n"
                f"可用余额：${float(bal):,.2f} USDT\n"
                f"今日已实现 PnL：{'+' if realized >= 0 else ''}{float(realized):,.2f} USDT\n"
                f"浮动盈亏：{'+' if upnl >= 0 else ''}{float(upnl):,.2f} USDT\n"
                f"运行时长：{str(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8) - start_time).split('.')[0]}"
            )
        await notifier.close()


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

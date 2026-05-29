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
        logger.info("kline_closed_received", interval=kline.interval,
                    close=float(kline.close), open_time=kline.open_time)
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

    # ── 获取聚合器引用（feed.subscribe 已创建） ────────────────
    from src.core.models import Exchange as _Exchange
    _agg = feed._get_aggregator(symbol, _Exchange.BINANCE)

    # ── 现货K线去重状态（WS 和 REST 共享，避免重复发布同一根K线） ──
    _spot_state: dict = {"last_ts": 0, "ws_alive": False}

    # ── 策略指标预热：通过 WS API / REST 批量拉取历史K线 ─────────
    # 1m=5h / 5m=41h / 1h=6天 历史数据
    logger.info("pipeline_warmup_starting")
    _warmup_total = 0
    for _wm_iv, _wm_limit in [("1h", 150), ("5m", 500), ("1m", 350)]:
        try:
            _wm_klines = await asyncio.wait_for(
                binance.get_spot_klines(symbol, _wm_iv, limit=_wm_limit),
                timeout=30,
            )
            # 跳过最后一根（当前未闭合K线），避免脏数据进入指标缓冲
            for _kl in _wm_klines[:-1]:
                strategy._pipeline.feed(_kl)
                _warmup_total += 1
            logger.info("pipeline_warmup_ok", interval=_wm_iv, bars=len(_wm_klines) - 1)
        except Exception as _exc:
            logger.warning("pipeline_warmup_failed", interval=_wm_iv, error=str(_exc))
    logger.info("pipeline_warmup_done", total=_warmup_total)

    # ── 现货K线 WebSocket（绕过被代理拦截的期货kline流） ──────────
    def _spot_kline_ws_cb(kline) -> None:
        """现货1m kline回调：实时更新价格 + 经聚合器生成5m/1h K线事件."""
        on_tick_sync(kline)
        if kline.is_closed and kline.open_time > _spot_state["last_ts"]:
            _spot_state["last_ts"] = kline.open_time
            _spot_state["ws_alive"] = True
            # 通过聚合器：自动触发 EVT_KLINE_CLOSED（含5m/1h聚合结果）
            _agg.feed(kline)

    await binance.subscribe_spot_klines(symbol, "1m", callback=_spot_kline_ws_cb)

    logger.info("paper_btc_running", symbol=symbol, mode="paper")

    # ── 系统启动通知 ──────────────────────────────────────
    start_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    if tg_enabled:
        await notifier._send(
            f"✅ <b>【连接成功】BTC 纸交易系统已启动</b>\n"
            f"品种：{symbol}\n"
            f"策略：BTC 多指标 v1\n"
            f"行情来源：现货 stream.binance.com（1m/5m/1h）\n"
            f"指标预热：{_warmup_total} 根历史K线已载入\n"
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

    # ── 现货REST轮询兜底：现货WS不通时通过REST驱动策略（每20秒） ──
    async def _spot_kline_poll_task() -> None:
        """现货K线REST轮询，WS活跃时仅更新价格，WS断开时兜底驱动策略."""
        while True:
            await asyncio.sleep(20)
            try:
                klines = await binance.get_spot_klines(symbol, "1m", limit=3)
                if not klines:
                    continue
                # 始终用最新K线的收盘价更新看板价格
                store.mark_price = float(klines[-1].close)
                store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")

                if _spot_state.get("ws_alive"):
                    # WS 正常工作，REST 只负责价格兜底，不重复推送K线
                    continue

                # WS 不工作：用REST兜底驱动策略
                closed = klines[-2] if len(klines) >= 2 else None
                if closed and closed.is_closed and closed.open_time > _spot_state["last_ts"]:
                    _spot_state["last_ts"] = closed.open_time
                    logger.info("spot_rest_kline_closed", close=float(closed.close),
                                open_time=closed.open_time)
                    _agg.feed(closed)  # 聚合生成5m/1h → 自动触发 EVT_KLINE_CLOSED
            except Exception as exc:
                logger.warning("spot_kline_poll_error", error=str(exc))

    # ── 启动后台任务并运行主循环 ──────────────────────────
    hourly_task = asyncio.create_task(_hourly_status_task())
    price_poll_task = asyncio.create_task(_spot_kline_poll_task())

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

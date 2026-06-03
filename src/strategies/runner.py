"""K 线策略纸交易运行器（从 trader main 抽出）."""
from __future__ import annotations

import asyncio
import atexit
import datetime
import signal
from decimal import Decimal
from typing import Any, Optional

from src.core.events import get_bus
from src.core.logging import get_logger
from src.core.mode import ModeGuard
from src.core.models import Exchange, TradingMode
from src.core.secrets import load_secrets
from src.core.telegram import init_notifier, send_message_sync
from src.execution.router import ExecutionRouter
from src.marketdata.feed import EVT_KLINE_CLOSED, EVT_ORDERBOOK, MarketDataFeed
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig, RiskManager
from src.sim.paper_exchange import PaperExchange
from src.sim.slippage import FeeModel, SlippageModel
from src.strategies.factory import create_strategy
from src.strategies.kline_base import KlineStrategy
from src.strategies.registry import resolve_name
from src.web.server import start_server
from src.web.status_store import init_store

logger = get_logger("strategies.runner")

_STRATEGY_LABELS = {
    "btc_multi_indicator": "BTC 多指标波段（Coolish）",
    "btc_multi_indicator_v2": "BTC 多指标日内 V2",
    "volume_profile": "Volume Profile",
    "ict": "ICT",
}


class KlineStrategyRunner:
    """纸交易：行情订阅、预热、on_kline → ExecutionRouter."""

    def __init__(self, strategy_name: str, config: dict[str, Any]) -> None:
        self.strategy_name = resolve_name(strategy_name)
        self.config = config
        self.strat_cfg = config.get("strategies", {}).get(self.strategy_name, {})

    async def run(self) -> None:
        from src.connectors.binance.connector import BinanceConnector

        strat_cfg = self.strat_cfg
        risk_cfg_raw = self.config.get("risk", {})
        tg_cfg = self.config.get("telegram", {})
        web_cfg = self.config.get("web", {})
        symbol = strat_cfg.get("symbol", "BTCUSDT")

        strategy: KlineStrategy = create_strategy(
            self.strategy_name, self.config, check_enabled=False,
        )
        label = _STRATEGY_LABELS.get(self.strategy_name, self.strategy_name)

        store = init_store(
            mode="paper",
            strategy=self.strategy_name,
            symbol=symbol,
        )

        secrets = load_secrets()
        tg_token = secrets.telegram.bot_token or tg_cfg.get("bot_token", "")
        tg_chat = secrets.telegram.chat_id or tg_cfg.get("chat_id", "")
        tg_enabled = tg_cfg.get("enabled", True) and bool(tg_token) and bool(tg_chat)
        notifier = init_notifier(token=tg_token, chat_id=tg_chat, enabled=tg_enabled)

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

        storage = MarketDataStorage(
            self.config.get("market_data", {}).get("db_path", "data/marketdata.db"),
        )
        feed = MarketDataFeed(storage)

        binance = BinanceConnector(
            api_key=secrets.binance.api_key,
            api_secret=secrets.binance.api_secret,
            testnet=self.config.get("exchanges", {}).get("binance", {}).get("testnet", False),
        )
        await binance.connect()
        feed.register_exchange("binance", binance)

        paper_ex.position_book.set_notifier(notifier)
        paper_ex.position_book.set_store(store)

        store.daily_loss_limit = float(risk_cfg_raw.get("max_daily_loss_usdt", 1000))
        store.max_position_usdt = float(strat_cfg.get("max_position_usdt", 10000))
        store.strategy_version = getattr(strategy, "name", self.strategy_name)
        snap_fn = getattr(strategy.__class__, "config_snapshot", None)
        if callable(snap_fn):
            store.strategy_config = snap_fn(**self._snapshot_kwargs(strategy, strat_cfg))

        bus = get_bus()

        async def on_kline_closed(kline) -> None:
            logger.info(
                "kline_closed_received",
                interval=kline.interval,
                close=float(kline.close),
                open_time=kline.open_time,
            )
            paper_ex.feed_kline(kline)
            store.mark_price = float(kline.close)
            store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
            store.balance = float(paper_ex.balance)
            store.unrealized_pnl = float(paper_ex.position_book.total_unrealized_pnl())
            store.daily_realized_pnl = float(paper_ex.position_book.daily_realized_pnl)
            store.total_fee = float(paper_ex.position_book.total_fee)

            pos_entry = paper_ex.position_book.get_position(kline.symbol)
            current_side = pos_entry.side if pos_entry and pos_entry.qty > 0 else None
            if pos_entry and pos_entry.qty > 0:
                store.has_position = True
                store.pos_side = pos_entry.side.value
                store.pos_qty = float(pos_entry.qty)
                store.pos_entry_price = float(pos_entry.entry_price)
                store.pos_mark_price = float(pos_entry.mark_price)
                store.pos_upnl = float(pos_entry.unrealized_pnl)
            else:
                store.has_position = False

            target = strategy.on_kline(kline, position_side=current_side)
            tfs = {strategy.primary_tf, getattr(strategy, "trend_tf", None)}
            if kline.interval in tfs - {None}:
                store.strategy_live = strategy.get_signal_state(position_side=current_side)
            if target is not None:
                if target.tp_price and target.sl_price:
                    paper_ex.position_book.set_tp_sl(
                        kline.symbol,
                        float(target.tp_price),
                        float(target.sl_price or 0),
                    )
                await router.execute(target)
                store.balance = float(paper_ex.balance)
                store.unrealized_pnl = float(paper_ex.position_book.total_unrealized_pnl())
                store.daily_realized_pnl = float(paper_ex.position_book.daily_realized_pnl)
                store.total_fee = float(paper_ex.position_book.total_fee)

        bus.subscribe(EVT_KLINE_CLOSED, on_kline_closed)

        _ob_count = 0

        async def on_orderbook(ob) -> None:
            nonlocal _ob_count
            _ob_count += 1
            if ob.bids and ob.asks:
                mid = (float(ob.bids[0][0]) + float(ob.asks[0][0])) / 2
                store.mark_price = round(mid, 2)
                store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
                paper_ex.position_book.update_mark_price(ob.symbol, Decimal(str(mid)))
                if store.has_position:
                    pos = paper_ex.position_book.get_position(ob.symbol)
                    if pos:
                        store.pos_mark_price = round(mid, 2)
                        store.pos_upnl = float(pos.unrealized_pnl)
                        store.unrealized_pnl = float(paper_ex.position_book.total_unrealized_pnl())

        bus.subscribe(EVT_ORDERBOOK, on_orderbook)

        _tick_count = 0

        def on_tick_sync(kline) -> None:
            nonlocal _tick_count
            _tick_count += 1
            store.mark_price = float(kline.close)
            store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")

        await feed.start()
        await feed.subscribe("binance", symbol, "1m", on_tick=on_tick_sync)

        from src.core.models import Exchange as _Exchange

        _agg = feed._get_aggregator(symbol, _Exchange.BINANCE)
        _spot_state: dict = {"last_ts": 0, "ws_alive": False}

        _warmup_total = await self._warmup_pipeline(strategy, binance, symbol)
        store.strategy_live = strategy.get_signal_state(position_side=None)

        def _spot_kline_ws_cb(kline) -> None:
            on_tick_sync(kline)
            if kline.is_closed and kline.open_time > _spot_state["last_ts"]:
                _spot_state["last_ts"] = kline.open_time
                _spot_state["ws_alive"] = True
                _agg.feed(kline)

        await binance.subscribe_spot_klines(symbol, "1m", callback=_spot_kline_ws_cb)

        logger.info("paper_kline_running", symbol=symbol, strategy=self.strategy_name)

        start_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        if tg_enabled:
            await notifier._send(
                f"✅ <b>【连接成功】纸交易系统已启动</b>\n"
                f"品种：{symbol}\n"
                f"策略：{label}\n"
                f"行情来源：现货 stream.binance.com（1m/5m/1h）\n"
                f"指标预热：{_warmup_total} 根历史K线已载入\n"
                f"启动时间：{start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC+8\n"
            )

        _hourly_interval = int(tg_cfg.get("hourly_interval_sec", 3600))
        _disconnect_sent = False

        async def _send_hourly_status() -> None:
            now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime(
                "%Y-%m-%d %H:%M UTC+8",
            )
            bal = paper_ex.balance
            upnl = paper_ex.position_book.total_unrealized_pnl()
            total_realized = paper_ex.position_book.daily_realized_pnl
            all_pos = paper_ex.position_book.get_all_positions()
            btc_price = store.mark_price
            if all_pos:
                pos = all_pos[0]
                pos_lines = (
                    f"持仓方向：{pos.side.value}\n"
                    f"持仓数量：{float(pos.qty):.4f} BTC\n"
                    f"开仓均价：${float(pos.entry_price):,.2f}\n"
                )
            else:
                pos_lines = "当前无持仓\n"
            msg = (
                f"📈 <b>【每小时状态】{symbol}</b>\n"
                f"时间：{now}\n"
                f"BTC 现价：${btc_price:,.2f}\n"
                f"{pos_lines}"
                f"可用余额：${float(bal):,.2f} USDT\n"
            )
            if tg_enabled:
                await notifier._send(msg)

        async def _hourly_status_task() -> None:
            await _send_hourly_status()
            while True:
                await asyncio.sleep(_hourly_interval)
                await _send_hourly_status()

        def _build_disconnect_msg() -> str:
            stop_time = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime(
                "%Y-%m-%d %H:%M:%S",
            )
            return (
                f"🔴 <b>【断开连接】纸交易系统已停止</b>\n"
                f"策略：{label}\n"
                f"停止时间：{stop_time} UTC+8\n"
                f"BTC 现价：${store.mark_price:,.2f}\n"
            )

        async def _send_disconnect_notification() -> None:
            nonlocal _disconnect_sent
            if _disconnect_sent or not tg_enabled:
                return
            _disconnect_sent = True
            await notifier._send(_build_disconnect_msg())

        def _atexit_disconnect() -> None:
            nonlocal _disconnect_sent
            if _disconnect_sent or not tg_enabled:
                return
            if send_message_sync(tg_token, tg_chat, _build_disconnect_msg()):
                _disconnect_sent = True

        atexit.register(_atexit_disconnect)

        web_host = web_cfg.get("host", "127.0.0.1")
        web_port = int(web_cfg.get("port", 8080))
        try:
            web_runner = await start_server(host=web_host, port=web_port)
        except Exception as exc:
            logger.error("dashboard_server_failed", error=str(exc))
            web_runner = None

        async def _spot_kline_poll_task() -> None:
            while True:
                await asyncio.sleep(20)
                try:
                    klines = await binance.get_spot_klines(symbol, "1m", limit=3)
                    if not klines:
                        continue
                    store.mark_price = float(klines[-1].close)
                    store.price_updated_at = datetime.datetime.now().strftime("%H:%M:%S")
                    if _spot_state.get("ws_alive"):
                        continue
                    closed = klines[-2] if len(klines) >= 2 else None
                    if closed and closed.is_closed and closed.open_time > _spot_state["last_ts"]:
                        _spot_state["last_ts"] = closed.open_time
                        _agg.feed(closed)
                except Exception as exc:
                    logger.warning("spot_kline_poll_error", error=str(exc))

        hourly_task = asyncio.create_task(_hourly_status_task())
        price_poll_task = asyncio.create_task(_spot_kline_poll_task())

        def _request_shutdown(signum: int | None = None) -> None:
            bus.stop()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, _request_shutdown, sig)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda s, f, _sig=sig: _request_shutdown(_sig))

        try:
            await bus.run()
        finally:
            for t in (hourly_task, price_poll_task):
                t.cancel()
            if web_runner is not None:
                await web_runner.cleanup()
            await _send_disconnect_notification()
            await notifier.close()

    @staticmethod
    async def _warmup_pipeline(strategy: KlineStrategy, binance, symbol: str) -> int:
        pipeline = getattr(strategy, "_pipeline", None)
        if pipeline is None:
            return 0
        total = 0
        intervals = list(pipeline.intervals)
        limits = {"1h": 150, "5m": 500, "1m": 350}
        for iv in intervals:
            limit = limits.get(iv, 200)
            try:
                klines = await asyncio.wait_for(
                    binance.get_spot_klines(symbol, iv, limit=limit),
                    timeout=30,
                )
                for kl in klines[:-1]:
                    pipeline.feed(kl)
                    total += 1
            except Exception as exc:
                logger.warning("pipeline_warmup_failed", interval=iv, error=str(exc))
        return total

    @staticmethod
    def _snapshot_kwargs(strategy: KlineStrategy, strat_cfg: dict) -> dict:
        base = {
            "primary_tf": strat_cfg.get("timeframe", strategy.primary_tf),
            "trend_tf": strat_cfg.get("trend_timeframe", getattr(strategy, "trend_tf", "1h")),
            "tp_pct": float(strat_cfg.get("tp_pct", strategy.tp_pct)),
            "sl_pct": float(strat_cfg.get("sl_pct", strategy.sl_pct)),
        }
        for key in (
            "signal_threshold", "reversal_threshold", "require_1h_trend",
            "rsi_long_min", "rsi_long_max", "rsi_short_min", "rsi_short_max",
            "rsi_1h_long_max", "entry_mode", "require_trend_1h", "killzone_enabled",
        ):
            if key in strat_cfg:
                base[key] = strat_cfg[key]
        return base

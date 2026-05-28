"""Hyperliquid 永续合约连接器（REST + WebSocket）.

Hyperliquid 使用 EVM 签名（eth_account），下单接口是 JSON-RPC 风格。
文档参考: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Callable, Optional

import aiohttp

from src.connectors.base import ExchangeError, IExchange
from src.connectors.utils import RateLimiter, with_retry
from src.core.logging import get_logger
from src.core.models import (
    AccountBalance, Exchange, Fill, FundingRate, Kline,
    MarkPrice, Order, OrderBook, OrderSide, OrderStatus,
    OrderType, Position, PositionSide, Symbol,
)

logger = get_logger("connector.hyperliquid")

REST_URL = "https://api.hyperliquid.xyz"
WS_URL = "wss://api.hyperliquid.xyz/ws"


def _parse_hl_status(status: str) -> OrderStatus:
    mapping = {
        "open": OrderStatus.OPEN,
        "filled": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "triggered": OrderStatus.FILLED,
    }
    return mapping.get(status, OrderStatus.PENDING)


class HyperliquidConnector(IExchange):
    """Hyperliquid 连接器."""

    def __init__(
        self,
        private_key: str = "",
        wallet_address: str = "",
    ) -> None:
        self.private_key = private_key
        self.wallet_address = wallet_address.lower()
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_tasks: list[asyncio.Task] = []
        self._connected = False
        self._limiter = RateLimiter(rate=300, per_seconds=60.0)
        self._order_callbacks: list[Callable[[Order], None]] = []
        self._position_callbacks: list[Callable[[Position], None]] = []
        self._coin_to_idx: dict[str, int] = {}

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._connected = True
        # 获取 coin index 映射
        await self._fetch_meta()
        logger.info("hyperliquid_connected")

    async def disconnect(self) -> None:
        for task in self._ws_tasks:
            task.cancel()
        if self._session:
            await self._session.close()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _fetch_meta(self) -> None:
        data = await self._post_info({"type": "meta"})
        for i, u in enumerate(data.get("universe", [])):
            self._coin_to_idx[u["name"]] = i

    def _coin(self, symbol: str) -> str:
        """将 BTCUSDT 转换为 HL coin name（BTC）."""
        return symbol.replace("USDT", "").replace("-PERP", "")

    async def _post_info(self, payload: dict) -> dict:
        await self._limiter.acquire()
        assert self._session
        async with self._session.post(f"{REST_URL}/info", json=payload) as resp:
            return await resp.json()

    async def _post_exchange(self, action: dict, nonce: int | None = None) -> dict:
        """签名并提交交易动作（需要 private_key）."""
        await self._limiter.acquire()
        if not self.private_key:
            raise ExchangeError("No private key configured for Hyperliquid")

        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
        except ImportError:
            raise ExchangeError("eth_account not installed; pip install eth-account")

        ts = nonce or int(time.time() * 1000)
        payload = {
            "action": action,
            "nonce": ts,
            "signature": self._sign_action(action, ts),
            "vaultAddress": None,
        }
        assert self._session
        async with self._session.post(f"{REST_URL}/exchange", json=payload) as resp:
            data = await resp.json()
            if isinstance(data, dict) and data.get("status") == "err":
                raise ExchangeError(data.get("response", "unknown error"))
            return data

    def _sign_action(self, action: dict, nonce: int) -> dict:
        """使用 EIP-712 签名 Hyperliquid action."""
        try:
            from eth_account import Account
            import eth_abi
        except ImportError:
            raise ExchangeError("eth_account/eth_abi not installed")

        # 简化版签名（实际需要按 HL 文档实现 EIP-712）
        import hashlib
        msg = json.dumps({"action": action, "nonce": nonce}, sort_keys=True)
        msg_hash = hashlib.sha256(msg.encode()).hexdigest()
        account = Account.from_key(self.private_key)
        signed = account.sign_message(
            __import__("eth_account.messages", fromlist=["encode_defunct"]).encode_defunct(
                hexstr=msg_hash
            )
        )
        return {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}

    # -------- REST: 账户 --------

    async def get_balance(self, asset: str = "USDT") -> AccountBalance:
        data = await self._post_info({
            "type": "clearinghouseState",
            "user": self.wallet_address,
        })
        margin = data.get("marginSummary", {})
        return AccountBalance(
            exchange=Exchange.HYPERLIQUID,
            asset=asset,
            total=Decimal(str(margin.get("accountValue", "0"))),
            available=Decimal(str(margin.get("withdrawable", "0"))),
            unrealized_pnl=Decimal(str(margin.get("totalUnrealizedPnl", "0"))),
        )

    async def get_position(
        self, symbol: str, side: PositionSide = PositionSide.BOTH
    ) -> Optional[Position]:
        positions = await self.get_positions()
        coin = self._coin(symbol)
        for p in positions:
            if p.symbol == coin:
                return p
        return None

    async def get_positions(self) -> list[Position]:
        data = await self._post_info({
            "type": "clearinghouseState",
            "user": self.wallet_address,
        })
        result = []
        for p in data.get("assetPositions", []):
            pos = p.get("position", {})
            szi = Decimal(str(pos.get("szi", "0")))
            if szi == 0:
                continue
            result.append(Position(
                symbol=pos.get("coin", ""),
                exchange=Exchange.HYPERLIQUID,
                side=PositionSide.LONG if szi > 0 else PositionSide.SHORT,
                qty=abs(szi),
                entry_price=Decimal(str(pos.get("entryPx", "0"))),
                mark_price=Decimal("0"),
                unrealized_pnl=Decimal(str(pos.get("unrealizedPnl", "0"))),
                leverage=int(pos.get("leverage", {}).get("value", 1)),
            ))
        return result

    async def place_order(self, order: Order) -> Order:
        coin = self._coin(order.symbol)
        is_buy = order.side == OrderSide.BUY
        limit_px = str(order.price) if order.price else self._get_limit_px(order)

        action = {
            "type": "order",
            "orders": [{
                "a": self._coin_to_idx.get(coin, 0),
                "b": is_buy,
                "p": limit_px,
                "s": str(order.qty),
                "r": order.reduce_only,
                "t": {"limit": {"tif": "Ioc"}} if order.order_type == OrderType.MARKET
                     else {"limit": {"tif": "Gtc"}},
                "c": order.client_order_id or str(uuid.uuid4()),
            }],
            "grouping": "na",
        }

        data = await with_retry(lambda: self._post_exchange(action))
        resp = data.get("response", {}).get("data", {}).get("statuses", [{}])[0]
        if "filled" in resp:
            order.status = OrderStatus.FILLED
            order.filled_qty = Decimal(str(resp["filled"].get("totalSz", order.qty)))
            order.avg_fill_price = Decimal(str(resp["filled"].get("avgPx", "0")))
        elif "resting" in resp:
            order.status = OrderStatus.OPEN
            order.exchange_order_id = str(resp["resting"].get("oid", ""))
        else:
            order.status = OrderStatus.REJECTED
        return order

    def _get_limit_px(self, order: Order) -> str:
        # 市价单用极端价格
        return "999999" if order.side == OrderSide.BUY else "1"

    async def cancel_order(self, symbol: str, order_id: str) -> Order:
        coin = self._coin(symbol)
        action = {
            "type": "cancel",
            "cancels": [{"a": self._coin_to_idx.get(coin, 0), "o": int(order_id)}],
        }
        await self._post_exchange(action)
        return Order(
            client_order_id=order_id,
            exchange=Exchange.HYPERLIQUID,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Decimal("0"),
            status=OrderStatus.CANCELED,
        )

    async def get_order(self, symbol: str, order_id: str) -> Order:
        data = await self._post_info({
            "type": "orderStatus",
            "user": self.wallet_address,
            "oid": int(order_id),
        })
        status_str = data.get("status", "unknown")
        o = data.get("order", {}).get("order", {})
        return Order(
            client_order_id=o.get("cloid", order_id),
            exchange=Exchange.HYPERLIQUID,
            symbol=symbol,
            side=OrderSide.BUY if o.get("side") == "B" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Decimal(str(o.get("sz", "0"))),
            price=Decimal(str(o.get("limitPx", "0"))),
            filled_qty=Decimal(str(o.get("origSz", "0"))) - Decimal(str(o.get("sz", "0"))),
            status=_parse_hl_status(status_str),
            exchange_order_id=order_id,
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        data = await self._post_info({
            "type": "openOrders",
            "user": self.wallet_address,
        })
        orders = []
        for o in data:
            coin = o.get("coin", "")
            if symbol and self._coin(symbol) != coin:
                continue
            orders.append(Order(
                client_order_id=o.get("cloid", ""),
                exchange=Exchange.HYPERLIQUID,
                symbol=coin + "USDT",
                side=OrderSide.BUY if o.get("side") == "B" else OrderSide.SELL,
                order_type=OrderType.LIMIT,
                qty=Decimal(str(o.get("sz", "0"))),
                price=Decimal(str(o.get("limitPx", "0"))),
                status=OrderStatus.OPEN,
                exchange_order_id=str(o.get("oid", "")),
            ))
        return orders

    async def get_symbol_info(self, symbol: str) -> Symbol:
        coin = self._coin(symbol)
        data = await self._post_info({"type": "meta"})
        for u in data.get("universe", []):
            if u["name"] == coin:
                return Symbol(
                    base=coin,
                    quote="USDT",
                    exchange=Exchange.HYPERLIQUID,
                    raw_symbol=coin,
                    tick_size=Decimal(str(u.get("szDecimals", "3"))),
                    lot_size=Decimal("0.001"),
                    min_qty=Decimal("0.001"),
                )
        raise ExchangeError(f"Symbol {symbol} not found in Hyperliquid")

    async def get_klines(
        self, symbol: str, interval: str,
        start_ms: int | None = None, end_ms: int | None = None,
        limit: int = 500,
    ) -> list[Kline]:
        coin = self._coin(symbol)
        interval_map = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        interval_min = interval_map.get(interval, 1)
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": f"{interval_min}",
                "startTime": start_ms or (int(time.time() * 1000) - limit * interval_min * 60000),
                "endTime": end_ms or int(time.time() * 1000),
            },
        }
        data = await self._post_info(payload)
        result = []
        for c in data:
            result.append(Kline(
                symbol=symbol,
                exchange=Exchange.HYPERLIQUID,
                interval=interval,
                open_time=int(c["t"]),
                close_time=int(c["t"]) + interval_min * 60000 - 1,
                open=Decimal(str(c["o"])),
                high=Decimal(str(c["h"])),
                low=Decimal(str(c["l"])),
                close=Decimal(str(c["c"])),
                volume=Decimal(str(c["v"])),
                quote_volume=Decimal("0"),
                num_trades=int(c.get("n", 0)),
                is_closed=True,
            ))
        return result

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        coin = self._coin(symbol)
        data = await self._post_info({"type": "l2Book", "coin": coin})
        levels = data.get("levels", [[], []])
        bids = [(Decimal(str(b["px"])), Decimal(str(b["sz"]))) for b in levels[0][:depth]]
        asks = [(Decimal(str(a["px"])), Decimal(str(a["sz"]))) for a in levels[1][:depth]]
        return OrderBook(
            symbol=symbol, exchange=Exchange.HYPERLIQUID,
            ts_ms=int(time.time() * 1000),
            bids=bids, asks=asks,
        )

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        coin = self._coin(symbol)
        data = await self._post_info({"type": "metaAndAssetCtxs"})
        meta, ctxs = data[0], data[1]
        for i, u in enumerate(meta.get("universe", [])):
            if u["name"] == coin and i < len(ctxs):
                ctx = ctxs[i]
                return FundingRate(
                    symbol=symbol,
                    exchange=Exchange.HYPERLIQUID,
                    rate=Decimal(str(ctx.get("funding", "0"))),
                    mark_price=Decimal(str(ctx.get("markPx", "0"))),
                    next_funding_time_ms=0,
                    ts_ms=int(time.time() * 1000),
                )
        raise ExchangeError(f"Symbol {symbol} not found")

    async def get_mark_price(self, symbol: str) -> MarkPrice:
        fr = await self.get_funding_rate(symbol)
        return MarkPrice(
            symbol=symbol, exchange=Exchange.HYPERLIQUID,
            mark_price=fr.mark_price or Decimal("0"),
            index_price=fr.index_price or Decimal("0"),
            ts_ms=fr.ts_ms,
        )

    async def subscribe_klines(self, symbol, interval, callback) -> None:
        coin = self._coin(symbol)
        sub = {"method": "subscribe", "subscription": {"type": "candle", "coin": coin, "interval": interval}}
        task = asyncio.create_task(
            self._ws_stream(sub, self._candle_handler(symbol, interval, callback))
        )
        self._ws_tasks.append(task)

    def _candle_handler(self, symbol, interval, callback):
        def handler(msg):
            if msg.get("channel") == "candle":
                c = msg.get("data", {})
                callback(Kline(
                    symbol=symbol, exchange=Exchange.HYPERLIQUID,
                    interval=interval,
                    open_time=int(c.get("t", 0)),
                    close_time=int(c.get("T", 0)),
                    open=Decimal(str(c.get("o", 0))),
                    high=Decimal(str(c.get("h", 0))),
                    low=Decimal(str(c.get("l", 0))),
                    close=Decimal(str(c.get("c", 0))),
                    volume=Decimal(str(c.get("v", 0))),
                    quote_volume=Decimal("0"),
                    num_trades=int(c.get("n", 0)),
                    is_closed=c.get("x", False),
                ))
        return handler

    async def subscribe_orderbook(self, symbol, callback) -> None:
        coin = self._coin(symbol)
        sub = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}}
        task = asyncio.create_task(self._ws_stream(sub, self._book_handler(symbol, callback)))
        self._ws_tasks.append(task)

    def _book_handler(self, symbol, callback):
        def handler(msg):
            if msg.get("channel") == "l2Book":
                d = msg.get("data", {})
                lvls = d.get("levels", [[], []])
                callback(OrderBook(
                    symbol=symbol, exchange=Exchange.HYPERLIQUID,
                    ts_ms=int(time.time() * 1000),
                    bids=[(Decimal(str(b["px"])), Decimal(str(b["sz"]))) for b in lvls[0]],
                    asks=[(Decimal(str(a["px"])), Decimal(str(a["sz"]))) for a in lvls[1]],
                ))
        return handler

    async def subscribe_trades(self, symbol, callback) -> None:
        coin = self._coin(symbol)
        sub = {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}
        task = asyncio.create_task(self._ws_stream(sub, lambda msg: [
            callback(Fill(
                fill_id=str(t.get("tid", "")),
                order_id="",
                exchange=Exchange.HYPERLIQUID,
                symbol=symbol,
                side=OrderSide.BUY if t.get("side") == "B" else OrderSide.SELL,
                price=Decimal(str(t.get("px", 0))),
                qty=Decimal(str(t.get("sz", 0))),
                ts_ms=int(t.get("time", 0)),
            ))
            for t in (msg.get("data") if isinstance(msg.get("data"), list) else [])
        ]))
        self._ws_tasks.append(task)

    async def subscribe_orders(self, callback) -> None:
        self._order_callbacks.append(callback)
        sub = {"method": "subscribe", "subscription": {"type": "orderUpdates", "user": self.wallet_address}}
        task = asyncio.create_task(self._ws_stream(sub, self._order_handler))
        self._ws_tasks.append(task)

    def _order_handler(self, msg: dict) -> None:
        if msg.get("channel") == "orderUpdates":
            for upd in msg.get("data", []):
                o = upd.get("order", {})
                order = Order(
                    client_order_id=o.get("cloid", ""),
                    exchange=Exchange.HYPERLIQUID,
                    symbol=o.get("coin", "") + "USDT",
                    side=OrderSide.BUY if o.get("side") == "B" else OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    qty=Decimal(str(o.get("origSz", "0"))),
                    filled_qty=Decimal(str(o.get("origSz", "0"))) - Decimal(str(o.get("sz", "0"))),
                    status=_parse_hl_status(upd.get("status", "open")),
                    exchange_order_id=str(o.get("oid", "")),
                )
                for cb in self._order_callbacks:
                    cb(order)

    async def subscribe_positions(self, callback) -> None:
        self._position_callbacks.append(callback)

    async def subscribe_funding_rate(self, symbol, callback) -> None:
        coin = self._coin(symbol)
        sub = {"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": coin}}
        task = asyncio.create_task(self._ws_stream(sub, lambda msg: callback(FundingRate(
            symbol=symbol, exchange=Exchange.HYPERLIQUID,
            rate=Decimal(str(msg.get("data", {}).get("funding", "0"))),
            ts_ms=int(time.time() * 1000),
        )) if msg.get("channel") == "activeAssetCtx" else None))
        self._ws_tasks.append(task)

    async def _ws_stream(self, sub: dict, handler: Callable, reconnect_delay: float = 5.0) -> None:
        import websockets  # type: ignore
        while self._connected:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    await ws.send(json.dumps(sub))
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            result = handler(msg)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as exc:
                            logger.warning("hl_ws_msg_error", error=str(exc))
            except Exception as exc:
                logger.warning("hl_ws_disconnected", error=str(exc))
                if self._connected:
                    await asyncio.sleep(reconnect_delay)

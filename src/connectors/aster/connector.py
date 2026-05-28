"""Aster 永续合约连接器（REST + WebSocket）.

Aster (fapi.aster.finance) 接口风格类似 Binance，使用 HMAC-SHA256 签名。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from typing import Callable, Optional
from urllib.parse import urlencode

import aiohttp

from src.connectors.base import ExchangeError, IExchange
from src.connectors.utils import RateLimiter, with_retry
from src.core.logging import get_logger
from src.core.models import (
    AccountBalance, Exchange, Fill, FundingRate, Kline,
    MarkPrice, Order, OrderBook, OrderSide, OrderStatus,
    OrderType, Position, PositionSide, Symbol,
)

logger = get_logger("connector.aster")

REST_BASE = "https://fapi.aster.finance"
WS_BASE = "wss://fapi.aster.finance/ws"


def _parse_status(s: str) -> OrderStatus:
    m = {
        "NEW": OrderStatus.OPEN,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.EXPIRED,
    }
    return m.get(s, OrderStatus.PENDING)


class AsterConnector(IExchange):
    """Aster 永续合约连接器（Binance 兼容接口）."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_tasks: list[asyncio.Task] = []
        self._connected = False
        self._limiter = RateLimiter(rate=600, per_seconds=60.0)
        self._order_callbacks: list[Callable[[Order], None]] = []
        self._position_callbacks: list[Callable[[Position], None]] = []

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": self.api_key}
        )
        self._connected = True
        logger.info("aster_connected")

    async def disconnect(self) -> None:
        for t in self._ws_tasks:
            t.cancel()
        if self._session:
            await self._session.close()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(sorted(params.items()))
        params["signature"] = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return params

    async def _get(self, path: str, params: dict | None = None, signed: bool = False) -> dict | list:
        await self._limiter.acquire()
        p = params or {}
        if signed:
            p = self._sign(p)
        assert self._session
        async with self._session.get(f"{REST_BASE}{path}", params=p) as resp:
            data = await resp.json()
            if isinstance(data, dict) and data.get("code", 0) and int(data["code"]) < 0:
                raise ExchangeError(data.get("msg", "error"), code=data.get("code"))
            return data

    async def _post(self, path: str, params: dict, signed: bool = True) -> dict:
        await self._limiter.acquire()
        p = self._sign(params) if signed else params
        assert self._session
        async with self._session.post(f"{REST_BASE}{path}", data=p) as resp:
            data = await resp.json()
            if isinstance(data, dict) and data.get("code", 0) and int(data.get("code", 200)) < 0:
                raise ExchangeError(data.get("msg", "error"), code=data.get("code"))
            return data

    async def _delete(self, path: str, params: dict, signed: bool = True) -> dict:
        await self._limiter.acquire()
        p = self._sign(params) if signed else params
        assert self._session
        async with self._session.delete(f"{REST_BASE}{path}", params=p) as resp:
            data = await resp.json()
            if isinstance(data, dict) and data.get("code", 0) and int(data.get("code", 200)) < 0:
                raise ExchangeError(data.get("msg", "error"), code=data.get("code"))
            return data

    async def get_balance(self, asset: str = "USDT") -> AccountBalance:
        data = await self._get("/fapi/v2/account", signed=True)
        for a in data.get("assets", []):
            if a["asset"] == asset:
                return AccountBalance(
                    exchange=Exchange.ASTER, asset=asset,
                    total=Decimal(a["marginBalance"]),
                    available=Decimal(a["availableBalance"]),
                    unrealized_pnl=Decimal(a["unrealizedProfit"]),
                )
        return AccountBalance(exchange=Exchange.ASTER, asset=asset,
                              total=Decimal("0"), available=Decimal("0"))

    async def get_position(self, symbol: str, side=PositionSide.BOTH) -> Optional[Position]:
        positions = await self.get_positions()
        for p in positions:
            if p.symbol == symbol:
                return p
        return None

    async def get_positions(self) -> list[Position]:
        data = await self._get("/fapi/v2/positionRisk", signed=True)
        result = []
        for p in data:
            qty = Decimal(p["positionAmt"])
            if qty == 0:
                continue
            result.append(Position(
                symbol=p["symbol"], exchange=Exchange.ASTER,
                side=PositionSide.LONG if qty > 0 else PositionSide.SHORT,
                qty=abs(qty),
                entry_price=Decimal(p["entryPrice"]),
                mark_price=Decimal(p["markPrice"]),
                unrealized_pnl=Decimal(p["unRealizedProfit"]),
                leverage=int(p.get("leverage", 1)),
            ))
        return result

    async def place_order(self, order: Order) -> Order:
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        type_map = {
            OrderType.MARKET: "MARKET", OrderType.LIMIT: "LIMIT",
            OrderType.STOP_MARKET: "STOP_MARKET",
            OrderType.TAKE_PROFIT_MARKET: "TAKE_PROFIT_MARKET",
        }
        params: dict = {
            "symbol": order.symbol, "side": side,
            "type": type_map.get(order.order_type, "MARKET"),
            "quantity": str(order.qty),
            "newClientOrderId": order.client_order_id or str(uuid.uuid4()),
        }
        if order.price and order.order_type == OrderType.LIMIT:
            params["price"] = str(order.price)
            params["timeInForce"] = "GTC"
        if order.stop_price:
            params["stopPrice"] = str(order.stop_price)
        if order.reduce_only:
            params["reduceOnly"] = "true"
        data = await with_retry(lambda: self._post("/fapi/v1/order", params))
        order.exchange_order_id = str(data.get("orderId", ""))
        order.status = _parse_status(data.get("status", "NEW"))
        if data.get("avgPrice", "0") != "0":
            order.avg_fill_price = Decimal(data["avgPrice"])
        order.filled_qty = Decimal(data.get("executedQty", "0"))
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> Order:
        data = await self._delete("/fapi/v1/order",
                                  {"symbol": symbol, "origClientOrderId": order_id})
        return Order(
            client_order_id=order_id, exchange=Exchange.ASTER,
            symbol=symbol, side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Decimal(data.get("origQty", "0")),
            status=_parse_status(data.get("status", "CANCELED")),
        )

    async def get_order(self, symbol: str, order_id: str) -> Order:
        data = await self._get("/fapi/v1/order",
                               {"symbol": symbol, "origClientOrderId": order_id}, signed=True)
        return Order(
            client_order_id=data.get("clientOrderId", ""),
            exchange=Exchange.ASTER, symbol=data["symbol"],
            side=OrderSide.BUY if data["side"] == "BUY" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Decimal(data.get("origQty", "0")),
            filled_qty=Decimal(data.get("executedQty", "0")),
            status=_parse_status(data.get("status", "NEW")),
            exchange_order_id=str(data.get("orderId", "")),
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params = {"symbol": symbol} if symbol else {}
        data = await self._get("/fapi/v1/openOrders", params, signed=True)
        return [
            Order(
                client_order_id=d.get("clientOrderId", ""),
                exchange=Exchange.ASTER, symbol=d["symbol"],
                side=OrderSide.BUY if d["side"] == "BUY" else OrderSide.SELL,
                order_type=OrderType.LIMIT,
                qty=Decimal(d.get("origQty", "0")),
                status=_parse_status(d.get("status", "NEW")),
                exchange_order_id=str(d.get("orderId", "")),
            )
            for d in data
        ]

    async def get_symbol_info(self, symbol: str) -> Symbol:
        data = await self._get("/fapi/v1/exchangeInfo")
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                return Symbol(
                    base=s["baseAsset"], quote=s["quoteAsset"],
                    exchange=Exchange.ASTER, raw_symbol=symbol,
                )
        raise ExchangeError(f"Symbol {symbol} not found in Aster")

    async def get_klines(self, symbol, interval, start_ms=None, end_ms=None, limit=500) -> list[Kline]:
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        data = await self._get("/fapi/v1/klines", params)
        return [
            Kline(
                symbol=symbol, exchange=Exchange.ASTER, interval=interval,
                open_time=int(d[0]), close_time=int(d[6]),
                open=Decimal(d[1]), high=Decimal(d[2]),
                low=Decimal(d[3]), close=Decimal(d[4]),
                volume=Decimal(d[5]), quote_volume=Decimal(d[7]),
                num_trades=int(d[8]), is_closed=True,
            )
            for d in data
        ]

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        data = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": depth})
        return OrderBook(
            symbol=symbol, exchange=Exchange.ASTER,
            ts_ms=int(time.time() * 1000),
            bids=[(Decimal(b[0]), Decimal(b[1])) for b in data["bids"]],
            asks=[(Decimal(a[0]), Decimal(a[1])) for a in data["asks"]],
        )

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return FundingRate(
            symbol=symbol, exchange=Exchange.ASTER,
            rate=Decimal(data.get("lastFundingRate", "0")),
            mark_price=Decimal(data.get("markPrice", "0")),
            index_price=Decimal(data.get("indexPrice", "0")),
            next_funding_time_ms=int(data.get("nextFundingTime", 0)),
            ts_ms=int(time.time() * 1000),
        )

    async def get_mark_price(self, symbol: str) -> MarkPrice:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return MarkPrice(
            symbol=symbol, exchange=Exchange.ASTER,
            mark_price=Decimal(data.get("markPrice", "0")),
            index_price=Decimal(data.get("indexPrice", "0")),
            ts_ms=int(time.time() * 1000),
        )

    async def subscribe_klines(self, symbol, interval, callback) -> None:
        stream = f"{symbol.lower()}@kline_{interval}"
        task = asyncio.create_task(
            self._ws_stream(f"{WS_BASE}/{stream}", self._kline_handler(symbol, interval, callback))
        )
        self._ws_tasks.append(task)

    def _kline_handler(self, symbol, interval, callback):
        def handler(msg):
            k = msg.get("k", {})
            callback(Kline(
                symbol=symbol, exchange=Exchange.ASTER, interval=interval,
                open_time=k["t"], close_time=k["T"],
                open=Decimal(k["o"]), high=Decimal(k["h"]),
                low=Decimal(k["l"]), close=Decimal(k["c"]),
                volume=Decimal(k["v"]), quote_volume=Decimal(k["q"]),
                num_trades=k["n"], is_closed=k["x"],
            ))
        return handler

    async def subscribe_orderbook(self, symbol, callback) -> None:
        stream = f"{symbol.lower()}@bookTicker"
        task = asyncio.create_task(
            self._ws_stream(f"{WS_BASE}/{stream}", self._book_handler(symbol, callback))
        )
        self._ws_tasks.append(task)

    def _book_handler(self, symbol, callback):
        def handler(msg):
            callback(OrderBook(
                symbol=symbol, exchange=Exchange.ASTER,
                ts_ms=int(time.time() * 1000),
                bids=[(Decimal(msg["b"]), Decimal(msg["B"]))],
                asks=[(Decimal(msg["a"]), Decimal(msg["A"]))],
            ))
        return handler

    async def subscribe_trades(self, symbol, callback) -> None:
        stream = f"{symbol.lower()}@aggTrade"
        task = asyncio.create_task(
            self._ws_stream(f"{WS_BASE}/{stream}", lambda msg: callback(Fill(
                fill_id=str(msg["a"]), order_id="",
                exchange=Exchange.ASTER, symbol=symbol,
                side=OrderSide.BUY if not msg["m"] else OrderSide.SELL,
                price=Decimal(msg["p"]), qty=Decimal(msg["q"]),
                ts_ms=msg["T"],
            )))
        )
        self._ws_tasks.append(task)

    async def subscribe_orders(self, callback) -> None:
        self._order_callbacks.append(callback)

    async def subscribe_positions(self, callback) -> None:
        self._position_callbacks.append(callback)

    async def subscribe_funding_rate(self, symbol, callback) -> None:
        stream = f"{symbol.lower()}@markPrice@1s"
        task = asyncio.create_task(
            self._ws_stream(f"{WS_BASE}/{stream}", lambda msg: callback(FundingRate(
                symbol=symbol, exchange=Exchange.ASTER,
                rate=Decimal(msg.get("r", "0")),
                mark_price=Decimal(msg.get("p", "0")),
                ts_ms=int(time.time() * 1000),
            )))
        )
        self._ws_tasks.append(task)

    async def _ws_stream(self, url: str, handler: Callable, reconnect_delay=5.0) -> None:
        import websockets  # type: ignore
        while self._connected:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            handler(msg)
                        except Exception as exc:
                            logger.warning("aster_ws_msg_error", error=str(exc))
            except Exception as exc:
                logger.warning("aster_ws_disconnected", url=url, error=str(exc))
                if self._connected:
                    await asyncio.sleep(reconnect_delay)

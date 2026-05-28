"""Binance USDT永续合约连接器（REST + WebSocket）."""
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

from src.connectors.base import (
    ExchangeError, IExchange, InsufficientMarginError,
    OrderRejectedError, RateLimitError,
)
from src.connectors.utils import RateLimiter, with_retry
from src.core.logging import get_logger
from src.core.models import (
    AccountBalance, Exchange, Fill, FundingRate, Kline,
    MarkPrice, Order, OrderBook, OrderSide, OrderStatus,
    OrderType, Position, PositionSide, Symbol,
)

logger = get_logger("connector.binance")

REST_BASE = "https://fapi.binance.com"
WS_BASE = "wss://fstream.binance.com"
REST_BASE_TESTNET = "https://testnet.binancefuture.com"
WS_BASE_TESTNET = "wss://stream.binancefuture.com"


def _side(side: OrderSide) -> str:
    return "BUY" if side == OrderSide.BUY else "SELL"


def _order_type(ot: OrderType) -> str:
    mapping = {
        OrderType.MARKET: "MARKET",
        OrderType.LIMIT: "LIMIT",
        OrderType.STOP_MARKET: "STOP_MARKET",
        OrderType.TAKE_PROFIT_MARKET: "TAKE_PROFIT_MARKET",
    }
    return mapping.get(ot, "MARKET")


def _parse_order_status(s: str) -> OrderStatus:
    mapping = {
        "NEW": OrderStatus.OPEN,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.EXPIRED,
    }
    return mapping.get(s, OrderStatus.PENDING)


class BinanceConnector(IExchange):
    """Binance USDT永续合约连接器."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self._rest_base = REST_BASE_TESTNET if testnet else REST_BASE
        self._ws_base = WS_BASE_TESTNET if testnet else WS_BASE
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_tasks: list[asyncio.Task] = []
        self._connected = False
        self._limiter = RateLimiter(rate=1200, per_seconds=60.0)
        self._listen_key: Optional[str] = None
        self._order_callbacks: list[Callable[[Order], None]] = []
        self._position_callbacks: list[Callable[[Position], None]] = []

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": self.api_key}
        )
        self._connected = True
        logger.info("binance_connected", testnet=(self._rest_base != REST_BASE))

    async def disconnect(self) -> None:
        for task in self._ws_tasks:
            task.cancel()
        if self._session:
            await self._session.close()
        self._connected = False
        logger.info("binance_disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -------- REST helpers --------

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(sorted(params.items()))
        params["signature"] = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return params

    async def _get(self, path: str, params: dict | None = None, signed: bool = False) -> dict:
        await self._limiter.acquire()
        p = params or {}
        if signed:
            p = self._sign(p)
        assert self._session
        async with self._session.get(f"{self._rest_base}{path}", params=p) as resp:
            data = await resp.json()
            if isinstance(data, dict) and "code" in data and data["code"] != 200:
                self._raise_error(data)
            return data

    async def _post(self, path: str, params: dict, signed: bool = True) -> dict:
        await self._limiter.acquire()
        p = self._sign(params) if signed else params
        assert self._session
        async with self._session.post(f"{self._rest_base}{path}", data=p) as resp:
            data = await resp.json()
            if isinstance(data, dict) and "code" in data and int(data.get("code", 200)) < 0:
                self._raise_error(data)
            return data

    async def _delete(self, path: str, params: dict, signed: bool = True) -> dict:
        await self._limiter.acquire()
        p = self._sign(params) if signed else params
        assert self._session
        async with self._session.delete(f"{self._rest_base}{path}", params=p) as resp:
            data = await resp.json()
            if isinstance(data, dict) and "code" in data and int(data.get("code", 200)) < 0:
                self._raise_error(data)
            return data

    def _raise_error(self, data: dict) -> None:
        code = data.get("code", 0)
        msg = data.get("msg", "unknown")
        if code in (-2019, -1003):
            raise InsufficientMarginError(msg, code=code)
        if code == -1015:
            raise RateLimitError(msg, code=code)
        raise ExchangeError(msg, code=code, raw=str(data))

    # -------- IExchange REST --------

    async def get_balance(self, asset: str = "USDT") -> AccountBalance:
        data = await self._get("/fapi/v2/account", signed=True)
        for a in data.get("assets", []):
            if a["asset"] == asset:
                return AccountBalance(
                    exchange=Exchange.BINANCE,
                    asset=asset,
                    total=Decimal(a["marginBalance"]),
                    available=Decimal(a["availableBalance"]),
                    unrealized_pnl=Decimal(a["unrealizedProfit"]),
                )
        return AccountBalance(
            exchange=Exchange.BINANCE, asset=asset,
            total=Decimal("0"), available=Decimal("0"),
        )

    async def get_position(
        self, symbol: str, side: PositionSide = PositionSide.BOTH
    ) -> Optional[Position]:
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
            side = PositionSide.LONG if qty > 0 else PositionSide.SHORT
            liq = Decimal(p.get("liquidationPrice", "0"))
            result.append(Position(
                symbol=p["symbol"],
                exchange=Exchange.BINANCE,
                side=side,
                qty=abs(qty),
                entry_price=Decimal(p["entryPrice"]),
                mark_price=Decimal(p["markPrice"]),
                liquidation_price=liq if liq > 0 else None,
                leverage=int(p.get("leverage", 1)),
                unrealized_pnl=Decimal(p["unRealizedProfit"]),
            ))
        return result

    async def place_order(self, order: Order) -> Order:
        params: dict = {
            "symbol": order.symbol,
            "side": _side(order.side),
            "type": _order_type(order.order_type),
            "quantity": str(order.qty),
            "newClientOrderId": order.client_order_id or str(uuid.uuid4()),
            "newOrderRespType": "RESULT",
        }
        if order.price and order.order_type == OrderType.LIMIT:
            params["price"] = str(order.price)
            params["timeInForce"] = "GTC"
        if order.stop_price:
            params["stopPrice"] = str(order.stop_price)
        if order.reduce_only:
            params["reduceOnly"] = "true"

        data = await with_retry(lambda: self._post("/fapi/v1/order", params))
        order.exchange_order_id = str(data["orderId"])
        order.status = _parse_order_status(data["status"])
        order.client_order_id = data.get("clientOrderId", order.client_order_id)
        if data.get("avgPrice"):
            order.avg_fill_price = Decimal(data["avgPrice"])
        if data.get("executedQty"):
            order.filled_qty = Decimal(data["executedQty"])
        logger.info(
            "order_placed",
            symbol=order.symbol,
            side=order.side.value,
            qty=float(order.qty),
            oid=order.exchange_order_id,
        )
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> Order:
        data = await self._delete(
            "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": order_id},
        )
        order = Order(
            client_order_id=order_id,
            exchange=Exchange.BINANCE,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Decimal(data.get("origQty", "0")),
            status=_parse_order_status(data["status"]),
            exchange_order_id=str(data["orderId"]),
        )
        return order

    async def get_order(self, symbol: str, order_id: str) -> Order:
        data = await self._get(
            "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": order_id},
            signed=True,
        )
        return self._parse_order(data)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._get("/fapi/v1/openOrders", params, signed=True)
        return [self._parse_order(d) for d in data]

    def _parse_order(self, data: dict) -> Order:
        return Order(
            client_order_id=data.get("clientOrderId", ""),
            exchange=Exchange.BINANCE,
            symbol=data["symbol"],
            side=OrderSide.BUY if data["side"] == "BUY" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Decimal(data.get("origQty", "0")),
            price=Decimal(data["price"]) if data.get("price", "0") != "0" else None,
            status=_parse_order_status(data["status"]),
            filled_qty=Decimal(data.get("executedQty", "0")),
            exchange_order_id=str(data.get("orderId", "")),
        )

    async def get_symbol_info(self, symbol: str) -> Symbol:
        data = await self._get("/fapi/v1/exchangeInfo")
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                tick = Decimal("0.1")
                lot = Decimal("0.001")
                min_qty = Decimal("0.001")
                for f in s.get("filters", []):
                    if f["filterType"] == "PRICE_FILTER":
                        tick = Decimal(f["tickSize"])
                    elif f["filterType"] == "LOT_SIZE":
                        lot = Decimal(f["stepSize"])
                        min_qty = Decimal(f["minQty"])
                return Symbol(
                    base=s["baseAsset"],
                    quote=s["quoteAsset"],
                    exchange=Exchange.BINANCE,
                    raw_symbol=symbol,
                    tick_size=tick,
                    lot_size=lot,
                    min_qty=min_qty,
                )
        raise ExchangeError(f"Symbol {symbol} not found")

    async def get_klines(
        self, symbol: str, interval: str,
        start_ms: int | None = None, end_ms: int | None = None,
        limit: int = 500,
    ) -> list[Kline]:
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        data = await self._get("/fapi/v1/klines", params)
        return [
            Kline(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                interval=interval,
                open_time=int(d[0]),
                close_time=int(d[6]),
                open=Decimal(d[1]),
                high=Decimal(d[2]),
                low=Decimal(d[3]),
                close=Decimal(d[4]),
                volume=Decimal(d[5]),
                quote_volume=Decimal(d[7]),
                num_trades=int(d[8]),
                is_closed=True,
            )
            for d in data
        ]

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        data = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": depth})
        return OrderBook(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            ts_ms=int(time.time() * 1000),
            bids=[(Decimal(b[0]), Decimal(b[1])) for b in data["bids"]],
            asks=[(Decimal(a[0]), Decimal(a[1])) for a in data["asks"]],
        )

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return FundingRate(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            rate=Decimal(data["lastFundingRate"]),
            mark_price=Decimal(data["markPrice"]),
            index_price=Decimal(data["indexPrice"]),
            next_funding_time_ms=int(data["nextFundingTime"]),
            ts_ms=int(time.time() * 1000),
        )

    async def get_mark_price(self, symbol: str) -> MarkPrice:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return MarkPrice(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            mark_price=Decimal(data["markPrice"]),
            index_price=Decimal(data["indexPrice"]),
            ts_ms=int(time.time() * 1000),
        )

    # -------- WebSocket --------

    async def subscribe_klines(
        self, symbol: str, interval: str,
        callback: Callable[[Kline], None],
    ) -> None:
        stream = f"{symbol.lower()}@kline_{interval}"
        task = asyncio.create_task(
            self._ws_stream(f"{self._ws_base}/ws/{stream}", self._kline_handler(symbol, interval, callback))
        )
        self._ws_tasks.append(task)

    def _kline_handler(self, symbol: str, interval: str, callback):
        def handler(msg: dict) -> None:
            k = msg.get("k", {})
            kline = Kline(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                interval=interval,
                open_time=k["t"],
                close_time=k["T"],
                open=Decimal(k["o"]),
                high=Decimal(k["h"]),
                low=Decimal(k["l"]),
                close=Decimal(k["c"]),
                volume=Decimal(k["v"]),
                quote_volume=Decimal(k["q"]),
                num_trades=k["n"],
                is_closed=k["x"],
            )
            callback(kline)
        return handler

    async def subscribe_orderbook(
        self, symbol: str, callback: Callable[[OrderBook], None],
    ) -> None:
        stream = f"{symbol.lower()}@bookTicker"
        task = asyncio.create_task(
            self._ws_stream(
                f"{self._ws_base}/ws/{stream}",
                self._book_handler(symbol, callback),
            )
        )
        self._ws_tasks.append(task)

    def _book_handler(self, symbol: str, callback):
        def handler(msg: dict) -> None:
            ob = OrderBook(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                ts_ms=int(time.time() * 1000),
                bids=[(Decimal(msg["b"]), Decimal(msg["B"]))],
                asks=[(Decimal(msg["a"]), Decimal(msg["A"]))],
            )
            callback(ob)
        return handler

    async def subscribe_trades(self, symbol: str, callback: Callable) -> None:
        stream = f"{symbol.lower()}@aggTrade"
        task = asyncio.create_task(
            self._ws_stream(
                f"{self._ws_base}/ws/{stream}",
                lambda msg: callback(Fill(
                    fill_id=str(msg["a"]),
                    order_id="",
                    exchange=Exchange.BINANCE,
                    symbol=symbol,
                    side=OrderSide.BUY if not msg["m"] else OrderSide.SELL,
                    price=Decimal(msg["p"]),
                    qty=Decimal(msg["q"]),
                    ts_ms=msg["T"],
                )),
            )
        )
        self._ws_tasks.append(task)

    async def subscribe_orders(self, callback: Callable[[Order], None]) -> None:
        self._order_callbacks.append(callback)
        await self._ensure_user_stream()

    async def subscribe_positions(self, callback: Callable[[Position], None]) -> None:
        self._position_callbacks.append(callback)
        await self._ensure_user_stream()

    async def subscribe_funding_rate(self, symbol: str, callback: Callable) -> None:
        stream = f"{symbol.lower()}@markPrice@1s"
        task = asyncio.create_task(
            self._ws_stream(
                f"{self._ws_base}/ws/{stream}",
                lambda msg: callback(FundingRate(
                    symbol=symbol,
                    exchange=Exchange.BINANCE,
                    rate=Decimal(msg.get("r", "0")),
                    mark_price=Decimal(msg.get("p", "0")),
                    next_funding_time_ms=int(msg.get("T", 0)),
                    ts_ms=int(time.time() * 1000),
                )),
            )
        )
        self._ws_tasks.append(task)

    async def _ensure_user_stream(self) -> None:
        if self._listen_key:
            return
        data = await self._post("/fapi/v1/listenKey", {})
        self._listen_key = data["listenKey"]
        task = asyncio.create_task(
            self._ws_stream(
                f"{self._ws_base}/ws/{self._listen_key}",
                self._user_stream_handler,
            )
        )
        self._ws_tasks.append(task)
        # Keep-alive: 每29分钟刷新 listenKey
        asyncio.create_task(self._keepalive_listen_key())

    def _user_stream_handler(self, msg: dict) -> None:
        event = msg.get("e")
        if event == "ORDER_TRADE_UPDATE":
            o = msg.get("o", {})
            order = Order(
                client_order_id=o.get("c", ""),
                exchange=Exchange.BINANCE,
                symbol=o["s"],
                side=OrderSide.BUY if o["S"] == "BUY" else OrderSide.SELL,
                order_type=OrderType.LIMIT,
                qty=Decimal(o.get("q", "0")),
                filled_qty=Decimal(o.get("z", "0")),
                avg_fill_price=Decimal(o["ap"]) if o.get("ap", "0") != "0" else None,
                status=_parse_order_status(o["X"]),
                exchange_order_id=str(o["i"]),
            )
            for cb in self._order_callbacks:
                cb(order)
        elif event == "ACCOUNT_UPDATE":
            for p_data in msg.get("a", {}).get("P", []):
                qty = Decimal(p_data.get("pa", "0"))
                if qty == 0:
                    continue
                pos = Position(
                    symbol=p_data["s"],
                    exchange=Exchange.BINANCE,
                    side=PositionSide.LONG if qty > 0 else PositionSide.SHORT,
                    qty=abs(qty),
                    entry_price=Decimal(p_data.get("ep", "0")),
                    mark_price=Decimal("0"),
                    unrealized_pnl=Decimal(p_data.get("up", "0")),
                )
                for cb in self._position_callbacks:
                    cb(pos)

    async def _keepalive_listen_key(self) -> None:
        while self._connected and self._listen_key:
            await asyncio.sleep(29 * 60)
            try:
                assert self._session
                async with self._session.put(
                    f"{self._rest_base}/fapi/v1/listenKey",
                    data={"listenKey": self._listen_key},
                ) as resp:
                    pass
            except Exception as exc:
                logger.warning("listenkey_keepalive_failed", error=str(exc))

    async def _ws_stream(
        self, url: str, handler: Callable, reconnect_delay: float = 5.0
    ) -> None:
        import websockets  # type: ignore
        while self._connected:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.debug("ws_connected", url=url)
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            handler(msg)
                        except Exception as exc:
                            logger.warning("ws_msg_error", error=str(exc))
            except Exception as exc:
                logger.warning("ws_disconnected", url=url, error=str(exc))
                if self._connected:
                    await asyncio.sleep(reconnect_delay)

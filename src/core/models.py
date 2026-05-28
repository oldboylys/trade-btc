"""统一数据模型：Symbol、Order、Fill、Position、FundingRate、Kline 等."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Exchange(str, Enum):
    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"
    ASTER = "aster"
    SIM = "sim"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT_MARKET = "take_profit_market"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"  # 单向持仓模式


class TradingMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


@dataclass
class Symbol:
    base: str          # e.g. BTC
    quote: str         # e.g. USDT
    exchange: Exchange
    raw_symbol: str    # 原始交易所符号

    tick_size: Decimal = Decimal("0.1")     # 价格步进
    lot_size: Decimal = Decimal("0.001")    # 数量步进
    min_qty: Decimal = Decimal("0.001")     # 最小下单量
    min_notional: Decimal = Decimal("5")    # 最小名义价值

    @property
    def name(self) -> str:
        return f"{self.base}{self.quote}"


@dataclass
class Kline:
    symbol: str
    exchange: Exchange
    interval: str          # 1m / 5m / 15m / 1h
    open_time: int         # ms
    close_time: int        # ms
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    num_trades: int
    is_closed: bool = True


@dataclass
class Trade:
    symbol: str
    exchange: Exchange
    trade_id: str
    price: Decimal
    qty: Decimal
    ts_ms: int
    is_buyer_maker: bool


@dataclass
class OrderBook:
    symbol: str
    exchange: Exchange
    ts_ms: int
    bids: list[tuple[Decimal, Decimal]] = field(default_factory=list)  # (price, qty)
    asks: list[tuple[Decimal, Decimal]] = field(default_factory=list)

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[Decimal]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


@dataclass
class Order:
    client_order_id: str
    exchange: Exchange
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: Decimal
    price: Optional[Decimal] = None       # limit price
    stop_price: Optional[Decimal] = None  # 条件单触发价
    exchange_order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Optional[Decimal] = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    reduce_only: bool = False
    position_side: PositionSide = PositionSide.BOTH
    fee: Decimal = Decimal("0")
    fee_asset: str = "USDT"
    metadata: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED, OrderStatus.CANCELED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED
        )

    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty


@dataclass
class Fill:
    fill_id: str
    order_id: str          # client_order_id
    exchange: Exchange
    symbol: str
    side: OrderSide
    price: Decimal
    qty: Decimal
    ts_ms: int
    fee: Decimal = Decimal("0")
    fee_asset: str = "USDT"
    is_maker: bool = False


@dataclass
class Position:
    symbol: str
    exchange: Exchange
    side: PositionSide
    qty: Decimal              # 合约张数/BTC数量（正数）
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Optional[Decimal] = None
    leverage: int = 1
    margin: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")

    @property
    def notional(self) -> Decimal:
        return self.qty * self.mark_price

    def update_pnl(self, mark_price: Decimal) -> None:
        self.mark_price = mark_price
        if self.side == PositionSide.LONG:
            self.unrealized_pnl = (mark_price - self.entry_price) * self.qty
        elif self.side == PositionSide.SHORT:
            self.unrealized_pnl = (self.entry_price - mark_price) * self.qty


@dataclass
class FundingRate:
    symbol: str
    exchange: Exchange
    rate: Decimal             # 当期资金费率
    predicted_rate: Optional[Decimal] = None
    next_funding_time_ms: int = 0
    interval_hours: int = 8
    mark_price: Optional[Decimal] = None
    index_price: Optional[Decimal] = None
    ts_ms: int = 0


@dataclass
class MarkPrice:
    symbol: str
    exchange: Exchange
    mark_price: Decimal
    index_price: Decimal
    ts_ms: int


@dataclass
class AccountBalance:
    exchange: Exchange
    asset: str
    total: Decimal
    available: Decimal
    margin_used: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")


# -------- 信号与策略输出 --------

class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"   # 平仓/观望


@dataclass
class TargetPosition:
    """策略输出的目标仓位."""
    symbol: str
    exchange: Exchange
    direction: SignalDirection
    target_qty: Decimal          # 目标数量（0 = 平仓）
    confidence: float = 0.0      # 信号置信度 0-1
    tp_price: Optional[Decimal] = None
    sl_price: Optional[Decimal] = None
    reason: str = ""
    ts_ms: int = 0

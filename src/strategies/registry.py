"""策略注册表：名称 → 实现类与运行类型."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Type

StrategyKind = Literal["kline", "polling"]


@dataclass(frozen=True)
class StrategyEntry:
    name: str
    kind: StrategyKind
    factory: Callable[[dict], object]
    description: str = ""


_REGISTRY: dict[str, StrategyEntry] = {}
_ALIASES: dict[str, str] = {}


def register(
    name: str,
    factory: Callable[[dict], object],
    *,
    kind: StrategyKind = "kline",
    description: str = "",
) -> None:
    _REGISTRY[name] = StrategyEntry(name=name, kind=kind, factory=factory, description=description)


def register_alias(alias: str, target: str) -> None:
    _ALIASES[alias] = target


def resolve_name(name: str) -> str:
    return _ALIASES.get(name, name)


def get_entry(name: str) -> StrategyEntry:
    resolved = resolve_name(name)
    if resolved not in _REGISTRY:
        known = sorted(set(_REGISTRY) | set(_ALIASES))
        raise KeyError(f"Unknown strategy '{name}'. Available: {known}")
    return _REGISTRY[resolved]


def list_strategies() -> list[str]:
    return sorted(_REGISTRY.keys())


def list_kline_strategies() -> list[str]:
    return [n for n, e in _REGISTRY.items() if e.kind == "kline"]


def list_cli_choices() -> list[str]:
    """CLI 可选名：正式名 + 别名."""
    choices = set(_REGISTRY.keys()) | set(_ALIASES.keys())
    return sorted(choices)


def bootstrap() -> None:
    """注册内置策略（幂等）."""
    if _REGISTRY:
        return

    from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy
    from src.strategies.btc_multi_indicator.intraday import BTCMultiIndicatorIntradayStrategy
    from src.strategies.btc_multi_indicator.scalp import BTC1mScalpStrategy
    from src.strategies.funding_arb.strategy import FundingArbStrategy

    register(
        "btc_multi_indicator",
        BTCMultiIndicatorStrategy.from_config,
        kind="kline",
        description="BTC 多指标波段（5m+1h，Coolish）",
    )
    register_alias("btc", "btc_multi_indicator")

    register(
        "btc_multi_indicator_v2",
        BTCMultiIndicatorIntradayStrategy.from_config,
        kind="kline",
        description="BTC 多指标日内 V2（更高频、紧 TP/SL）",
    )
    register_alias("btc_v2", "btc_multi_indicator_v2")

    register(
        "btc_1m_scalp",
        BTC1mScalpStrategy.from_config,
        kind="kline",
        description="BTC 1m 剥头皮（小段反弹、紧 TP/SL）",
    )
    register_alias("btc_scalp", "btc_1m_scalp")

    register(
        "funding_arb",
        lambda cfg: _create_funding_arb(cfg),
        kind="polling",
        description="跨所资金费率套利",
    )

    from src.strategies.volume_profile.strategy import VolumeProfileStrategy
    from src.strategies.ict.strategy import ICTStrategy

    register(
        "volume_profile",
        VolumeProfileStrategy.from_config,
        kind="kline",
        description="Volume Profile（POC/VA/HVN/LVN）",
    )
    register(
        "ict",
        ICTStrategy.from_config,
        kind="kline",
        description="ICT（FVG/OB/结构/流动性）",
    )


def _create_funding_arb(cfg: dict) -> object:
    from decimal import Decimal
    from src.strategies.funding_arb.strategy import FundingArbStrategy

    return FundingArbStrategy(
        symbols=["BTCUSDT"],
        min_spread=Decimal(str(cfg.get("min_funding_spread", "0.0002"))),
        max_position_usdt=Decimal(str(cfg.get("max_position_usdt", "5000"))),
    )

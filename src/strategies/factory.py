"""策略工厂：从全局配置构造策略实例."""
from __future__ import annotations

from typing import Any

from src.strategies.registry import StrategyKind, bootstrap, get_entry, resolve_name


def create_strategy(name: str, config: dict[str, Any], *, check_enabled: bool = True) -> Any:
    """
    根据策略名与 config 构造实例。
    config 为完整 load_config() 结果；策略参数取自 config['strategies'][name]。
    """
    bootstrap()
    resolved = resolve_name(name)
    entry = get_entry(resolved)
    strat_cfg = config.get("strategies", {}).get(resolved, {})
    if not strat_cfg:
        raise ValueError(f"No config section strategies.{resolved}")

    if check_enabled and not strat_cfg.get("enabled", True):
        raise RuntimeError(
            f"Strategy '{resolved}' is disabled (strategies.{resolved}.enabled=false). "
            "Use --force to override."
        )

    return entry.factory(strat_cfg)


def get_strategy_kind(name: str) -> StrategyKind:
    bootstrap()
    return get_entry(resolve_name(name)).kind


def is_enabled(name: str, config: dict[str, Any]) -> bool:
    resolved = resolve_name(name)
    return bool(config.get("strategies", {}).get(resolved, {}).get("enabled", True))

"""配置加载：default.yaml + secrets.local.yaml，支持环境变量覆盖."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    if config_dir is None:
        config_dir = Path(__file__).parent.parent.parent / "config"
    config_dir = Path(config_dir)

    default_path = config_dir / "default.yaml"
    secrets_path = config_dir / "secrets.local.yaml"

    cfg: dict[str, Any] = {}
    if default_path.exists():
        with open(default_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    if secrets_path.exists():
        with open(secrets_path, encoding="utf-8") as f:
            secrets = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, secrets)

    # 环境变量覆盖：TRADER_MODE / TRADER_BINANCE_API_KEY 等
    if mode := os.environ.get("TRADER_MODE"):
        cfg["mode"] = mode

    return cfg


_cached: dict[str, Any] | None = None


def get_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    global _cached
    if _cached is None:
        _cached = load_config(config_dir)
    return _cached


def reload_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    global _cached
    _cached = load_config(config_dir)
    return _cached

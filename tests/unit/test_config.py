"""测试配置加载与密钥管理."""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.core.config import load_config
from src.core.secrets import load_secrets


def test_load_default_config():
    cfg = load_config()
    assert "mode" in cfg
    assert cfg["mode"] in ("paper", "testnet", "live")


def test_config_env_override(tmp_path):
    default = {"mode": "paper", "exchanges": {}}
    with open(tmp_path / "default.yaml", "w") as f:
        yaml.dump(default, f)
    os.environ["TRADER_MODE"] = "testnet"
    try:
        cfg = load_config(tmp_path)
        assert cfg["mode"] == "testnet"
    finally:
        del os.environ["TRADER_MODE"]


def test_secrets_from_env():
    os.environ["BINANCE_API_KEY"] = "test_key"
    os.environ["BINANCE_API_SECRET"] = "test_secret"
    try:
        secrets = load_secrets()
        assert secrets.binance.api_key == "test_key"
        assert secrets.binance.api_secret == "test_secret"
        assert secrets.binance.is_configured()
    finally:
        del os.environ["BINANCE_API_KEY"]
        del os.environ["BINANCE_API_SECRET"]


def test_secrets_from_yaml(tmp_path):
    data = {
        "exchanges": {
            "binance": {"api_key": "yaml_key", "api_secret": "yaml_secret"}
        }
    }
    with open(tmp_path / "secrets.local.yaml", "w") as f:
        yaml.dump(data, f)
    secrets = load_secrets(tmp_path)
    assert secrets.binance.api_key == "yaml_key"


def test_empty_secrets_not_configured():
    secrets = load_secrets(Path("/nonexistent"))
    assert not secrets.binance.is_configured()
    assert not secrets.hyperliquid.is_configured()

"""安全密钥管理：从 secrets.local.yaml / 环境变量读取，禁止明文硬编码."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class BinanceSecrets:
    api_key: str = ""
    api_secret: str = ""

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


@dataclass
class HyperliquidSecrets:
    private_key: str = ""
    wallet_address: str = ""

    def is_configured(self) -> bool:
        return bool(self.private_key and self.wallet_address)

    @property
    def masked_key(self) -> str:
        if len(self.private_key) < 8:
            return "***"
        return self.private_key[:4] + "..." + self.private_key[-4:]


@dataclass
class AsterSecrets:
    api_key: str = ""
    api_secret: str = ""

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


@dataclass
class TelegramSecrets:
    bot_token: str = "7811254208:AAGbd0NZUZC_nv5B2IwiqyOTV5IdP7-_Sys"
    chat_id: str = "7902172509"

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def get(self, key: str, default: str = "") -> str:
        return getattr(self, key, default)


@dataclass
class AllSecrets:
    binance: BinanceSecrets
    hyperliquid: HyperliquidSecrets
    aster: AsterSecrets
    telegram: TelegramSecrets


def load_secrets(config_dir: str | Path | None = None) -> AllSecrets:
    """
    密钥优先级：
    1. 环境变量（BINANCE_API_KEY / BINANCE_API_SECRET 等）
    2. config/secrets.local.yaml
    3. 空默认值（纸交易模式可以工作）
    """
    if config_dir is None:
        config_dir = Path(__file__).parent.parent.parent / "config"
    config_dir = Path(config_dir)

    raw: dict = {}
    raw_tg: dict = {}
    secrets_path = config_dir / "secrets.local.yaml"
    if secrets_path.exists():
        with open(secrets_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw = data.get("exchanges", {})
        raw_tg = data.get("telegram", {})

    def _get(section: str, key: str, env_var: str) -> str:
        env_val = os.environ.get(env_var, "")
        if env_val:
            return env_val
        return raw.get(section, {}).get(key, "")

    def _get_tg(key: str, env_var: str) -> str:
        env_val = os.environ.get(env_var, "")
        if env_val:
            return env_val
        return raw_tg.get(key, "")

    return AllSecrets(
        binance=BinanceSecrets(
            api_key=_get("binance", "api_key", "BINANCE_API_KEY"),
            api_secret=_get("binance", "api_secret", "BINANCE_API_SECRET"),
        ),
        hyperliquid=HyperliquidSecrets(
            private_key=_get("hyperliquid", "private_key", "HYPERLIQUID_PRIVATE_KEY"),
            wallet_address=_get("hyperliquid", "wallet_address", "HYPERLIQUID_WALLET_ADDRESS"),
        ),
        aster=AsterSecrets(
            api_key=_get("aster", "api_key", "ASTER_API_KEY"),
            api_secret=_get("aster", "api_secret", "ASTER_API_SECRET"),
        ),
        telegram=TelegramSecrets(
            bot_token=_get_tg("bot_token", "TELEGRAM_BOT_TOKEN"),
            chat_id=_get_tg("chat_id", "TELEGRAM_CHAT_ID"),
        ),
    )

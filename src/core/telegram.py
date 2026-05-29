"""Telegram 机器人通知模块：异步发送开仓/平仓/风控告警消息."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

from src.core.logging import get_logger

logger = get_logger("core.telegram")

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    异步 Telegram 通知器。
    所有发送操作均为 fire-and-forget（不阻塞策略主循环）。
    """

    def __init__(self, token: str, chat_id: str, enabled: bool = True) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)
        self._session: Optional[object] = None  # aiohttp.ClientSession

    async def _get_session(self):
        if self._session is None or self._session.closed:  # type: ignore[union-attr]
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            import aiohttp
            session = await self._get_session()
            url = _SEND_URL.format(token=self.token)
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("telegram_send_failed", status=resp.status, body=body[:200])
        except Exception as exc:
            logger.warning("telegram_error", error=str(exc))

    def send_nowait(self, text: str) -> None:
        """Fire-and-forget，在事件循环中调度发送，不阻塞调用方."""
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self._send(text))
        except Exception as exc:
            logger.warning("telegram_schedule_error", error=str(exc))

    # ── 格式化消息 ──────────────────────────────────────────

    def notify_open(
        self,
        symbol: str,
        direction: str,
        qty: float,
        price: float,
        notional: float,
        tp_price: Optional[float],
        sl_price: Optional[float],
    ) -> None:
        emoji = "🟢" if "多" in direction or "LONG" in direction else "🔴"
        tp_str = f"${tp_price:,.2f}" if tp_price else "—"
        sl_str = f"${sl_price:,.2f}" if sl_price else "—"
        text = (
            f"{emoji} <b>【开仓】{symbol}</b>\n"
            f"方向：{direction}\n"
            f"数量：{qty:.4f} BTC\n"
            f"开仓价：<b>${price:,.2f}</b>\n"
            f"名义仓位：${notional:,.0f} USDT\n"
            f"止盈挂单：{tp_str}\n"
            f"止损挂单：{sl_str}\n"
            f"<i>模式：纸交易 PAPER</i>"
        )
        self.send_nowait(text)

    def notify_close(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
        close_price: float,
        gross_pnl: float,
        fee: float,
        net_pnl: float,
        total_realized: float,
    ) -> None:
        result = "盈利 ✅" if net_pnl >= 0 else "亏损 ❌"
        sign = "+" if net_pnl >= 0 else ""
        text = (
            f"📊 <b>【平仓结算】{symbol}</b>  {result}\n"
            f"方向：{side}\n"
            f"数量：{qty:.4f} BTC\n"
            f"开仓价：${entry_price:,.2f}\n"
            f"平仓价：<b>${close_price:,.2f}</b>\n"
            f"毛盈亏：{sign}{gross_pnl:,.2f} USDT\n"
            f"手续费：-{fee:.2f} USDT\n"
            f"净盈亏：<b>{sign}{net_pnl:,.2f} USDT</b>\n"
            f"累计已实现 PnL：{total_realized:,.2f} USDT\n"
            f"<i>模式：纸交易 PAPER</i>"
        )
        self.send_nowait(text)

    def notify_risk(self, event_type: str, detail: str) -> None:
        text = (
            f"⚠️ <b>【风控告警】{event_type}</b>\n"
            f"{detail}"
        )
        self.send_nowait(text)

    def notify_system(self, message: str) -> None:
        text = f"ℹ️ <b>【系统通知】</b>\n{message}"
        self.send_nowait(text)

    async def close(self) -> None:
        if self._session and not self._session.closed:  # type: ignore[union-attr]
            await self._session.close()


# 全局单例
_notifier: Optional[TelegramNotifier] = None


def init_notifier(token: str, chat_id: str, enabled: bool = True) -> TelegramNotifier:
    global _notifier
    _notifier = TelegramNotifier(token=token, chat_id=chat_id, enabled=enabled)
    return _notifier


def get_notifier() -> Optional[TelegramNotifier]:
    return _notifier

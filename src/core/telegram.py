"""Telegram 机器人通知模块：异步发送开仓/平仓/风控告警消息."""
from __future__ import annotations

import asyncio
import json
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from src.core.logging import get_logger

logger = get_logger("core.telegram")

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tg_sender")


def _send_sync(token: str, chat_id: str, text: str) -> None:
    """同步发送（在线程池中执行，不阻塞事件循环）."""
    url = _SEND_URL.format(token=token)
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # 代理环境下代理 CA 不在 certifi 信任链内，对通知流量跳过证书校验
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
        body = resp.read()
        result = json.loads(body)
        if not result.get("ok"):
            logger.warning("telegram_send_failed", description=result.get("description", ""))


class TelegramNotifier:
    """
    异步 Telegram 通知器（基于线程池 + urllib，兼容 Windows）。
    所有发送操作均为 fire-and-forget，不阻塞策略主循环。
    """

    def __init__(self, token: str, chat_id: str, enabled: bool = True) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)

    async def _send(self, text: str) -> None:
        """异步发送（线程池执行，await 会等待完成）."""
        if not self.enabled:
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, _send_sync, self.token, self.chat_id, text)
            logger.info("telegram_sent", chars=len(text))
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
        pass  # 线程池由模块级 _executor 管理，进程退出时自动清理


# 全局单例
_notifier: Optional[TelegramNotifier] = None


def init_notifier(token: str, chat_id: str, enabled: bool = True) -> TelegramNotifier:
    global _notifier
    _notifier = TelegramNotifier(token=token, chat_id=chat_id, enabled=enabled)
    return _notifier


def get_notifier() -> Optional[TelegramNotifier]:
    return _notifier


def send_message_sync(token: str, chat_id: str, text: str) -> bool:
    """同步发送（用于 atexit / 信号处理等无法 await 的场景）."""
    if not token or not chat_id:
        return False
    try:
        _send_sync(token, chat_id, text)
        logger.info("telegram_sent_sync", chars=len(text))
        return True
    except Exception as exc:
        logger.warning("telegram_sync_error", error=str(exc))
        return False

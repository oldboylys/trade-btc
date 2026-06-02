"""由统计结果生成策略假设卡片."""
from __future__ import annotations

from typing import Any


def build_hypothesis_cards(tier1: dict[str, Any], regime: dict[str, Any]) -> list[dict[str, Any]]:
    liq = tier1.get("liquidity", {})
    pos = tier1.get("position", {})
    limit_pct = tier1.get("limit_order_pct") or 0
    buy_pct = tier1.get("buy_side_pct") or 50
    partial_pct = tier1.get("partial_fill_rate_pct") or 0
    hold_med = pos.get("hold_hours_median") or 0
    hold_p90 = pos.get("hold_hours_p90") or 0
    pct_24h = pos.get("pct_hold_over_24h") or 0
    maker_pct = liq.get("added_liquidity_pct") or 0
    dd_verdict = regime.get("drawdown_exposure_behavior", {}).get("verdict", "insufficient_data")
    withdraw = regime.get("withdrawal_alignment", {})
    funding = tier1.get("funding", {})

    cards = [
        _card_h1(limit_pct, partial_pct, hold_med, hold_p90),
        _card_h2(buy_pct, tier1.get("yearly_breakdown", {})),
        _card_h3(maker_pct, liq),
        _card_h4(hold_med, hold_p90, pct_24h),
        _card_h5(withdraw, regime.get("equity_summary", {})),
        _card_h6(funding),
        _card_h7(dd_verdict, regime.get("drawdown_exposure_behavior", {})),
    ]
    return cards


def _card_h1(limit_pct: float, partial_pct: float, hold_med: float, hold_p90: float) -> dict:
    evidence = (
        f"限价单占比约 {limit_pct:.1f}%；分批成交（PartiallyFilled）约 {partial_pct:.1f}%；"
        f"持仓中位 {hold_med:.1f}h、P90 {hold_p90:.1f}h。"
    )
    confidence = "中"
    if limit_pct > 60 and partial_pct > 20 and hold_med > 4:
        confidence = "中-高"
    elif limit_pct < 40:
        confidence = "低"
    return {
        "id": "H1",
        "name": "BTC 趋势跟随 + 限价分批建仓",
        "evidence": evidence,
        "rule_draft": (
            "1h EMA 趋势过滤；5m/15m 突破或回踩入场；"
            "分 2~4 笔阶梯限价建仓，单笔不超过权益 2~5%。"
        ),
        "param_range": {
            "trend_tf": "1h",
            "entry_tf": "5m-15m",
            "ladder_legs": "2-4",
            "per_leg_pct_equity": "0.5-2.5",
        },
        "invalidation": "限价占比低且持仓中位 < 1h 时，假设不成立。",
        "validation_next": "用 Binance BTCUSDT 5m/1h 回测：突破回踩 + 分批限价成交模型。",
        "confidence": confidence,
    }


def _card_h2(buy_pct: float, yearly: dict) -> dict:
    by_year = yearly.get("by_year", [])
    recent_buy = [r["buy_pct"] for r in by_year if r["year"] >= 2023]
    avg_recent = sum(recent_buy) / len(recent_buy) if recent_buy else buy_pct
    evidence = f"BTC 成交 Buy 占比约 {buy_pct:.1f}%；2023+ 年均 Buy 约 {avg_recent:.1f}%。"
    confidence = "高" if buy_pct > 55 else ("中" if buy_pct > 50 else "低")
    return {
        "id": "H2",
        "name": "多头为主、空头择时",
        "evidence": evidence,
        "rule_draft": "默认只做多；1h 趋势转空且权益回撤 > 10% 时允许对冲空单。",
        "param_range": {"long_bias": ">55% buys", "short_trigger_dd_pct": "10-20"},
        "invalidation": "多年 Buy/Sell 接近 50/50 且无 regime 差异。",
        "validation_next": "分牛熊子样本回测纯多 vs 多+择时空。",
        "confidence": confidence,
    }


def _card_h3(maker_pct: float, liq: dict) -> dict:
    evidence = (
        f"AddedLiquidity (Maker) 约 {maker_pct:.1f}%；"
        f"RemovedLiquidity (Taker) 约 {liq.get('removed_liquidity_pct', 0):.1f}%。"
    )
    confidence = "高" if maker_pct > 55 else ("中" if maker_pct > 45 else "低")
    return {
        "id": "H3",
        "name": "Maker 优先执行",
        "evidence": evidence,
        "rule_draft": "优先 Post-Only 限价；相对 mid 偏移 5~20 bps；超时 30~120s 撤单。",
        "param_range": {"post_only": True, "offset_bps": "5-20", "cancel_sec": "30-120"},
        "invalidation": "Taker 占比 > 55%。",
        "validation_next": "纸交易统计 maker fill rate 与滑点。",
        "confidence": confidence,
    }


def _card_h4(hold_med: float, hold_p90: float, pct_24h: float) -> dict:
    evidence = (
        f"持仓中位 {hold_med:.1f}h、P90 {hold_p90:.1f}h；"
        f">{24}h 持仓占比 {pct_24h:.1f}%。"
    )
    scalp = hold_med < 1 and pct_24h < 30
    confidence = "低" if scalp else ("高" if hold_med > 12 else "中")
    return {
        "id": "H4",
        "name": "波段持有（非剥头皮）",
        "evidence": evidence,
        "rule_draft": "TP 3~8%、SL 1.5~3%；禁止日内 >3 次反手。",
        "param_range": {"tp_pct": "3-8", "sl_pct": "1.5-3", "max_flips_per_day": "1-3"},
        "invalidation": "持仓中位 < 1h 且高频反手。",
        "validation_next": "对比不同 min_hold_hours 下回测夏普。",
        "confidence": confidence,
    }


def _card_h5(withdraw: dict, equity_summary: dict) -> dict:
    near_peak = withdraw.get("withdrawals_near_equity_peak_pct", 0)
    evidence = (
        f"完成出金 {withdraw.get('withdrawal_count', 0)} 笔、合计约 "
        f"{withdraw.get('withdrawal_total_xbt', 0):.2f} XBT；"
        f"峰值附近出金占比 {near_peak:.1f}%。"
    )
    confidence = "中" if near_peak > 40 else "低"
    return {
        "id": "H5",
        "name": "盈利后系统性降风险",
        "evidence": evidence,
        "rule_draft": "权益达滚动高点 95% 后减仓 20~30% 或收紧 trailing stop。",
        "param_range": {"peak_threshold": "0.95", "trim_pct": "20-30"},
        "invalidation": "出金与权益高点无时间关联（注意出金≠可交易规则）。",
        "validation_next": "回测 equity peak trailing de-risk 规则。",
        "confidence": confidence,
    }


def _card_h6(funding: dict) -> dict:
    events = funding.get("funding_events", 0)
    evidence = f"资金费事件 {events} 笔；累计约 {funding.get('funding_total_xbt', 0)} XBT。"
    confidence = "低-中" if events > 100 else "低"
    return {
        "id": "H6",
        "name": "资金费意识持仓",
        "evidence": evidence,
        "rule_draft": "|funding rate| > 0.01% 时缩短持有或反向套利对冲。",
        "param_range": {"funding_threshold": "0.01-0.05"},
        "invalidation": "资金费绝对值占盈亏可忽略。",
        "validation_next": "叠加 funding 序列与持仓方向交叉表。",
        "confidence": confidence,
    }


def _card_h7(verdict: str, dd_behavior: dict) -> dict:
    evidence = (
        f"最大回撤窗口行为：{verdict}；"
        f"净敞口变化 sum={dd_behavior.get('net_signed_home_sum', 'N/A')}；"
        f"主导方向 {dd_behavior.get('predominant_side', 'N/A')}。"
    )
    if verdict == "reduce_exposure":
        rule = "回撤期主动减仓；入场仓位减半、SL 收紧至 1~1.5%。"
        confidence = "中"
    elif verdict == "hold_through":
        rule = "回撤期扛单；宽 SL 3~5%、小仓位 ≤1% 权益/笔。"
        confidence = "中"
    else:
        rule = "需更多数据区分扛单/止损风格。"
        confidence = "低"
    return {
        "id": "H7",
        "name": "回撤期敞口管理",
        "evidence": evidence,
        "rule_draft": rule,
        "param_range": {"dd_trigger_pct": "15-25"},
        "invalidation": "回撤期敞口与盈利期无差异。",
        "validation_next": "标注历次 >20% 回撤窗口内的净仓位变化。",
        "confidence": confidence,
    }

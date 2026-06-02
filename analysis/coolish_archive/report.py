"""生成 Markdown 报告与图表."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def write_outputs(
    output_dir: Path,
    tier1: dict[str, Any],
    regime: dict[str, Any],
    cards: list[dict[str, Any]],
    equity: pd.DataFrame,
    daily_exposure: pd.DataFrame,
    hold_sessions: pd.DataFrame,
    sanity: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "sanity": sanity,
        "tier1": tier1,
        "regime": regime,
        "hypotheses": cards,
    }
    json_path = output_dir / "summary_stats.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    _plot_equity(equity, output_dir)
    _plot_hold_cdf(hold_sessions, output_dir)
    _plot_yearly_buy(tier1, output_dir)
    _plot_maker(tier1, output_dir)
    if not daily_exposure.empty:
        _plot_exposure(daily_exposure, output_dir)

    report_path = _REPO_ROOT / "docs" / "research" / "coolish_archive_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render_markdown(tier1, regime, cards, sanity, output_dir),
        encoding="utf-8",
    )
    return report_path, json_path


def _plot_equity(equity: pd.DataFrame, out: Path) -> None:
    if equity.empty or "adjustedWealthMultipleVsBaseline" not in equity.columns:
        return
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(equity["timestamp"], equity["adjustedWealthMultipleVsBaseline"], lw=0.8)
    ax.set_title("Adjusted Wealth Multiple vs Baseline")
    ax.set_ylabel("Multiple")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "equity_multiple.png", dpi=120)
    plt.close(fig)


def _plot_hold_cdf(sessions: pd.DataFrame, out: Path) -> None:
    if sessions.empty or "hold_hours" not in sessions.columns:
        return
    h = sessions["hold_hours"].clip(upper=sessions["hold_hours"].quantile(0.99))
    fig, ax = plt.subplots(figsize=(8, 4))
    sorted_h = h.sort_values()
    y = range(1, len(sorted_h) + 1)
    ax.plot(sorted_h, [v / len(sorted_h) * 100 for v in y])
    ax.set_xlabel("Hold hours")
    ax.set_ylabel("CDF %")
    ax.set_title("Position Hold Duration CDF")
    fig.tight_layout()
    fig.savefig(out / "hold_duration_cdf.png", dpi=120)
    plt.close(fig)


def _plot_yearly_buy(tier1: dict, out: Path) -> None:
    rows = tier1.get("yearly_breakdown", {}).get("by_year", [])
    if not rows:
        return
    years = [r["year"] for r in rows]
    buys = [r["buy_pct"] for r in rows]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(years, buys, color="steelblue")
    ax.axhline(50, color="gray", ls="--", lw=0.8)
    ax.set_title("BTC Trades Buy Side % by Year")
    ax.set_ylabel("Buy %")
    fig.tight_layout()
    fig.savefig(out / "yearly_buy_pct.png", dpi=120)
    plt.close(fig)


def _plot_maker(tier1: dict, out: Path) -> None:
    liq = tier1.get("liquidity", {})
    if not liq:
        return
    labels = ["Maker", "Taker"]
    vals = [liq.get("added_liquidity_pct", 0), liq.get("removed_liquidity_pct", 0)]
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.pie(vals, labels=labels, autopct="%1.1f%%")
    ax.set_title("Liquidity Role (BTC trades)")
    fig.tight_layout()
    fig.savefig(out / "maker_taker_pie.png", dpi=120)
    plt.close(fig)


def _plot_exposure(daily: pd.DataFrame, out: Path) -> None:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"], utc=True)
    side_map = {"Long": 1, "Short": -1, "Flat": 0}
    d["side_num"] = d["exposure_side"].map(side_map).fillna(0)
    fig, ax = plt.subplots(figsize=(12, 3))
    ax.fill_between(d["date"], 0, d["side_num"], alpha=0.4, step="mid")
    ax.set_title("Daily Exposure Side (XBTUSD proxy)")
    ax.set_yticks([-1, 0, 1])
    ax.set_yticklabels(["Short", "Flat", "Long"])
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "daily_exposure.png", dpi=120)
    plt.close(fig)


def _render_markdown(
    tier1: dict,
    regime: dict,
    cards: list[dict],
    sanity: dict,
    output_dir: Path,
) -> str:
    lines = [
        "# Coolish 公开账本策略洞察研究报告",
        "",
        "> 数据来源：[BTC-Trading-Since-2020](https://github.com/bwjoke/BTC-Trading-Since-2020)  ",
        "> 分析模块：`analysis/coolish_archive/`（独立研究，未改动交易策略代码）",
        "",
        "## 执行摘要",
        "",
        "本报告从真实 BitMEX 账本（约 6 年、17 万+ 成交）反向归纳 **行为特征** 与 **可尝试策略假设**。"
        "结论为假设性质，需在外部 OHLCV（如 Binance）上回测验证，不构成实盘建议。",
        "",
        f"- 加载成交行数：{sanity.get('trade_rows_loaded')}（manifest 期望 {sanity.get('trade_rows_manifest')}）",
        f"- 时间范围：{sanity.get('first_time')} → {sanity.get('last_time')}",
        f"- 最新 adjusted wealth 倍数：{regime.get('equity_summary', {}).get('latest_multiple')}x",
        f"- 最大回撤（相对峰值）：{regime.get('equity_summary', {}).get('max_drawdown_pct')}%",
        "",
        "## Tier 1：直接观测",
        "",
        "### 品种集中度",
        "",
        "| Symbol | 名义占比 % |",
        "|--------|------------|",
    ]
    for sym, pct in list(tier1.get("symbol_concentration_pct", {}).items())[:10]:
        lines.append(f"| {sym} | {pct} |")

    lines.extend(
        [
            "",
            f"- BTC 相关成交占比（按 USD foreignNotional）：**{tier1.get('btc_notional_share_pct')}%**",
            f"- XBTUSD 占比：**{tier1.get('xbtusd_notional_share_pct')}%**",
            f"- Buy / Sell：**{tier1.get('buy_side_pct')}% / {tier1.get('sell_side_pct')}%**",
            f"- 限价单占比：**{tier1.get('limit_order_pct')}%**",
            f"- Maker (AddedLiquidity)：**{tier1.get('liquidity', {}).get('added_liquidity_pct')}%**",
            f"- 分批成交占比：**{tier1.get('partial_fill_rate_pct')}%**",
            "",
            "### 持仓周期（XBTUSD 重建）",
            "",
        ]
    )
    pos = tier1.get("position", {})
    for k, v in pos.items():
        lines.append(f"- {k}: {v}")

    size = tier1.get("size_vs_equity", {})
    if size:
        lines.extend(["", "### 单笔规模 vs 权益", ""])
        for k, v in size.items():
            lines.append(f"- {k}: {v}")

    lines.extend(["", "## Tier 2：权益与分周期", ""])
    eqs = regime.get("equity_summary", {})
    for k, v in eqs.items():
        lines.append(f"- {k}: {v}")

    lines.extend(["", "### 主要回撤片段（Top）", ""])
    for ep in regime.get("drawdown_episodes", [])[:5]:
        lines.append(
            f"- {ep['max_drawdown_pct']}% @ {ep['start'][:10]} → 恢复约 {ep.get('recovery_days')} 天"
        )

    w = regime.get("withdrawal_alignment", {})
    lines.extend(
        [
            "",
            "### 出金与权益高点",
            "",
            f"- 出金笔数：{w.get('withdrawal_count')}；合计约 {w.get('withdrawal_total_xbt')} XBT",
            f"- 峰值 95% 附近出金占比：{w.get('withdrawals_near_equity_peak_pct')}%",
            "",
            "### 回撤期敞口行为",
            "",
            f"- 判定：**{regime.get('drawdown_exposure_behavior', {}).get('verdict')}**",
            "",
            "## 图表",
            "",
            f"![权益倍数](../../analysis/output/equity_multiple.png)",
            f"![持仓 CDF](../../analysis/output/hold_duration_cdf.png)",
            f"![年度 Buy%](../../analysis/output/yearly_buy_pct.png)",
            f"![Maker/Taker](../../analysis/output/maker_taker_pie.png)",
            "",
            "## 策略假设卡片（H1–H7）",
            "",
        ]
    )

    for c in cards:
        lines.extend(
            [
                f"### {c['id']}：{c['name']}（置信度：{c['confidence']}）",
                "",
                f"**证据**：{c['evidence']}",
                "",
                f"**规则草案**：{c['rule_draft']}",
                "",
                f"**参数区间**：`{c['param_range']}`",
                "",
                f"**失效条件**：{c['invalidation']}",
                "",
                f"**Binance 验证建议**：{c['validation_next']}",
                "",
                "---",
                "",
            ]
        )

    lines.extend(
        [
            "## 局限性",
            "",
            "1. 单账户幸存者偏差，52x 收益不可直接复制。",
            "2. 无 K 线，无法还原主观看图入场逻辑。",
            "3. BitMEX 反向 XBT 与 Binance USDT 线性合约仅行为可类比。",
            "",
            "## 后续验证清单（trade-btc）",
            "",
            "- [ ] 下载 Binance BTCUSDT 5m/1h 历史 K 线至 `data/marketdata.db`",
            "- [ ] 对 H1/H2/H4 参数网格做样本外回测",
            "- [ ] 纸交易验证 H3 Maker 成交率",
            "- [ ] 对比 H7 减仓 vs 扛单两种风控曲线",
            "",
            f"*图表与 JSON 输出目录：`{output_dir.relative_to(_REPO_ROOT)}`*",
        ]
    )
    return "\n".join(lines)

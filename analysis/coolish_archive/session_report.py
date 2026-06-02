"""生成持仓回合指标研究报告与图表."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _plot_win_loss_rsi(enriched: pd.DataFrame, out: Path) -> None:
    if "open_1h_rsi14" not in enriched.columns:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    for label, color in [("win", "green"), ("loss", "red"), ("flat", "gray")]:
        sub = enriched[enriched["outcome"] == label]
        if not sub.empty:
            ax.hist(sub["open_1h_rsi14"].dropna(), bins=30, alpha=0.5, label=label, color=color)
    ax.set_xlabel("Open 1h RSI14")
    ax.set_title("RSI at Session Open by Outcome")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "session_win_loss_rsi.png", dpi=120)
    plt.close(fig)


def _plot_macd_delta(enriched: pd.DataFrame, out: Path) -> None:
    col = "delta_1h_macd_hist"
    if col not in enriched.columns:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = enriched["outcome"].map({"win": "green", "loss": "red", "flat": "gray"})
    ax.scatter(enriched["hold_hours"], enriched[col], c=colors, alpha=0.5, s=12)
    ax.set_xlabel("Hold hours")
    ax.set_ylabel("Delta 1h MACD hist")
    ax.set_title("MACD hist change vs hold time")
    fig.tight_layout()
    fig.savefig(out / "open_vs_close_macd_hist.png", dpi=120)
    plt.close(fig)


def _plot_cluster_heatmap(analytics: dict, out: Path) -> None:
    clusters = analytics.get("clusters", {}).get("clusters")
    if not clusters:
        return
    df = pd.DataFrame(clusters).set_index("cluster_id")
    num_cols = [c for c in df.columns if c not in ("count", "trend_bullish_pct")]
    if not num_cols:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    im = ax.imshow(df[num_cols].astype(float).values, aspect="auto", cmap="RdYlGn")
    ax.set_xticks(range(len(num_cols)))
    ax.set_xticklabels(num_cols, rotation=45, ha="right")
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels([f"C{i}" for i in df.index])
    ax.set_title("Session clusters (1h open features, normalized rows)")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(out / "session_cluster_heatmap.png", dpi=120)
    plt.close(fig)


def write_session_report(
    enriched: pd.DataFrame,
    analytics: dict[str, Any],
    cfg: Any,
) -> Path:
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    _plot_win_loss_rsi(enriched, out)
    _plot_macd_delta(enriched, out)
    _plot_cluster_heatmap(analytics, out)

    report_path = _REPO_ROOT / "docs" / "research" / "coolish_sessions_indicator_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(enriched, analytics, cfg), encoding="utf-8")
    return report_path


def _render_report(enriched: pd.DataFrame, analytics: dict, cfg: Any) -> str:
    s = analytics.get("summary", {})
    wl = analytics.get("win_loss_profile", {})
    clusters = analytics.get("clusters", {})
    hyp = analytics.get("h_hypothesis_checks", {})
    yearly = analytics.get("yearly_evolution", [])

    lines = [
        "# Coolish 持仓回合：开平仓技术指标与统计汇总",
        "",
        "> K 线源：见配置 `klines_source`（`binance`=Binance BTCUSDT；`trades_resample`=成交 lastPx 重采样）  ",
        "> 指标参数对齐 TradingView / 项目 `IndicatorPipeline` 默认值；**非** TV 服务器拉取。",
        "",
        "## 1. 数据范围",
        "",
        f"- 持仓回合数：**{analytics.get('session_count', 0)}**（`{cfg.sessions.primary_symbol}`，"
        f"最短持有 {cfg.sessions.min_hold_minutes} 分钟）",
        f"- 指标周期：{', '.join(cfg.sessions.intervals)}",
        f"-  enriched 表：`analysis/output/sessions_enriched.parquet`",
        "",
        "## 2. 总体摘要",
        "",
        f"- 持仓时长中位数：**{s.get('hold_hours_median', 'N/A'):.2f}** 小时",
        f"- 胜率（按段内 realised PnL）：**{s.get('win_rate_pct', 'N/A'):.2f}%**",
        f"- 段内 PnL 合计：**{s.get('total_pnl_xbt', 0):.4f} XBT**",
        "",
        "## 3. 盈利 vs 亏损：开仓时 1h 指标画像",
        "",
    ]

    for label, title in [("win", "盈利回合"), ("loss", "亏损回合")]:
        p = wl.get(label, {})
        if not p:
            continue
        lines.append(f"### {title}（n={p.get('count', 0)}）")
        if "rsi14" in p:
            lines.append(f"- RSI14 中位数：**{p['rsi14']['median']}**")
        if "trend_bullish_pct" in p:
            lines.append(f"- EMA20>EMA50 比例：**{p['trend_bullish_pct']}%**")
        if "macd_hist" in p:
            lines.append(f"- MACD hist 中位数：**{p['macd_hist']['median']}**")
        lines.append("")

    lines.extend(["## 4. 多空对比", ""])
    for side, data in analytics.get("long_short_profile", {}).items():
        lines.append(
            f"- **{side}**：{data.get('count')} 段，胜率 {data.get('win_rate_pct')}%，"
            f"持仓中位 {data.get('median_hold_hours')}h"
        )

    lines.extend(["", "## 5. 年度演变", "", "| 年 | 回合数 | 胜率% | 开仓RSI中位 | 趋势多头% |", "|---|--------|-------|-------------|-----------|"])
    for row in yearly:
        lines.append(
            f"| {row.get('year')} | {row.get('sessions')} | {row.get('win_rate_pct')} | "
            f"{row.get('open_rsi14_median', '-')} | {row.get('trend_bullish_pct', '-')} |"
        )

    lines.extend(["", "## 6. 聚类签名（1h 开仓特征）", ""])
    if isinstance(clusters, dict) and clusters.get("clusters"):
        for c in clusters["clusters"]:
            lines.append(
                f"- **簇 {c['cluster_id']}**（n={c['count']}）："
                f"RSI={c.get('rsi14', 'N/A')}, MACD hist={c.get('macd_hist', 'N/A')}, "
                f"趋势多头%={c.get('trend_bullish_pct', 'N/A')}"
            )
    else:
        lines.append(f"- {clusters.get('error', '聚类未执行')}")

    lines.extend([
        "",
        "## 7. 与 H1–H7 假设对照",
        "",
        f"- 持仓 >24h 占比：**{hyp.get('pct_hold_over_24h')}%**",
        f"- 开仓时 1h 趋势多头（EMA20>EMA50）占比：**{hyp.get('open_trend_bullish_pct')}%**",
        f"- 开仓 RSI14 中位数：**{hyp.get('open_rsi14_median')}**",
    ])
    if "rsi14_median_win" in hyp:
        lines.append(
            f"- 盈利单 RSI 中位 **{hyp['rsi14_median_win']}** vs 亏损单 **{hyp['rsi14_median_loss']}**"
        )

    lines.extend([
        "",
        "## 8. 规则化结论（本地统计，非 LLM）",
        "",
        _narrative_summary(enriched, analytics),
        "",
        "## 9. 图表",
        "",
        "![RSI by outcome](../../analysis/output/session_win_loss_rsi.png)",
        "![MACD delta](../../analysis/output/open_vs_close_macd_hist.png)",
        "![Clusters](../../analysis/output/session_cluster_heatmap.png)",
        "",
        "## 10. 局限",
        "",
        "1. Binance 与 BitMEX 价格/资金费存在差异。",
        "2. 段内 `realisedPnl` 仅覆盖部分成交行，PnL 对齐钱包有残差。",
        "3. 指标按 K 线 `open_time` backward asof，与精确成交毫秒可能有 1 根 K 线误差。",
        "",
        "完整 JSON：`analysis/output/session_analytics.json`",
    ])
    return "\n".join(lines)


def _narrative_summary(enriched: pd.DataFrame, analytics: dict) -> str:
    n = analytics.get("session_count", 0)
    s = analytics.get("summary", {})
    hyp = analytics.get("h_hypothesis_checks", {})
    bullets = [
        f"共 **{n}** 个持仓回合纳入分析；中位持仓 **{s.get('hold_hours_median', 0):.1f}** 小时，"
        f"支持「波段而非剥头皮」假设（H4）。",
    ]
    tb = hyp.get("open_trend_bullish_pct")
    if tb is not None:
        bullets.append(
            f"开仓时约 **{tb}%** 处于 1h EMA 多头排列，可与趋势过滤策略（H1）对照验证。"
        )
    win_r = hyp.get("rsi14_median_win")
    loss_r = hyp.get("rsi14_median_loss")
    if win_r is not None and loss_r is not None:
        if win_r > loss_r + 3:
            bullets.append(
                f"盈利回合开仓 RSI 明显高于亏损回合（{win_r} vs {loss_r}），"
                "倾向在偏强/非超卖区做多或顺势。"
            )
        elif win_r < loss_r - 3:
            bullets.append(
                f"盈利回合开仓 RSI 低于亏损回合（{win_r} vs {loss_r}），"
                "可能偏抄底或反转逻辑。"
            )
    clusters = analytics.get("clusters", {}).get("clusters", [])
    if clusters:
        largest = max(clusters, key=lambda x: x["count"])
        bullets.append(
            f"最大聚类（簇 {largest['cluster_id']}，n={largest['count']}）"
            f"平均 RSI {largest.get('rsi14')}，可作为典型入场状态参考。"
        )
    return "\n".join(f"- {b}" for b in bullets)

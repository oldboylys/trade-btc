"""生成持仓回合分析可视化结论（图表 + HTML 仪表盘）."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.coolish_archive.settings import AnalysisConfig

_REPO = Path(__file__).resolve().parent.parent.parent

# 中文显示（Windows 常见字体）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def build_all_visuals(cfg: AnalysisConfig) -> Path:
    cfg.ensure_output()
    out = cfg.output_dir
    enriched = pd.read_parquet(out / "sessions_enriched.parquet")
    with open(out / "session_analytics.json", encoding="utf-8") as f:
        analytics = json.load(f)

    _plot_overview_dashboard(enriched, analytics, out)
    _plot_outcome_pie(analytics, out)
    _plot_win_loss_indicators(analytics, out)
    _plot_yearly_trends(analytics, out)
    _plot_long_short(analytics, out)
    _plot_clusters(analytics, out)
    _plot_hold_distribution(enriched, out)
    _plot_open_rsi_by_outcome(enriched, out)
    _plot_trend_bullish_breakdown(enriched, out)

    html_path = out / "session_dashboard.html"
    html_path.write_text(_render_html(analytics, enriched), encoding="utf-8")
    return html_path


def _plot_overview_dashboard(df: pd.DataFrame, analytics: dict, out: Path) -> None:
    s = analytics["summary"]
    fig = plt.figure(figsize=(14, 9))
    fig.suptitle("Coolish XBTUSD 持仓回合 — 可视化总览", fontsize=14, fontweight="bold")

    # 1 胜率饼图
    ax1 = fig.add_subplot(2, 3, 1)
    oc = df["outcome"].value_counts()
    colors = {"win": "#2ecc71", "loss": "#e74c3c", "flat": "#95a5a6"}
    ax1.pie(
        oc.values,
        labels=[f"{k}\n{v}" for k, v in zip(oc.index, oc.values)],
        autopct="%1.1f%%",
        colors=[colors.get(x, "#3498db") for x in oc.index],
        startangle=90,
    )
    ax1.set_title("盈亏分布")

    # 2 持仓时长
    ax2 = fig.add_subplot(2, 3, 2)
    h = df["hold_hours"].clip(upper=df["hold_hours"].quantile(0.95))
    ax2.hist(h, bins=35, color="#3498db", edgecolor="white")
    ax2.axvline(s["hold_hours_median"], color="red", ls="--", label=f"中位 {s['hold_hours_median']:.1f}h")
    ax2.set_xlabel("持仓小时")
    ax2.set_title("持仓时长分布")
    ax2.legend(fontsize=8)

    # 3 年度胜率
    ax3 = fig.add_subplot(2, 3, 3)
    yearly = analytics.get("yearly_evolution", [])
    if yearly:
        years = [str(r["year"]) for r in yearly]
        wr = [r["win_rate_pct"] for r in yearly]
        bars = ax3.bar(years, wr, color="#9b59b6")
        ax3.axhline(50, color="gray", ls="--", lw=0.8)
        ax3.set_ylabel("胜率 %")
        ax3.set_title("年度胜率")
        for b, v in zip(bars, wr):
            ax3.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.0f}%", ha="center", fontsize=8)

    # 4 多空胜率
    ax4 = fig.add_subplot(2, 3, 4)
    ls = analytics.get("long_short_profile", {})
    sides = list(ls.keys())
    wrs = [ls[s]["win_rate_pct"] for s in sides]
    holds = [ls[s]["median_hold_hours"] for s in sides]
    x = np.arange(len(sides))
    ax4.bar(x - 0.2, wrs, 0.4, label="胜率%", color="#1abc9c")
    ax4.bar(x + 0.2, holds, 0.4, label="持仓中位(h)", color="#e67e22")
    ax4.set_xticks(x)
    ax4.set_xticklabels(sides)
    ax4.set_title("多空对比")
    ax4.legend(fontsize=8)

    # 5 聚类规模
    ax5 = fig.add_subplot(2, 3, 5)
    clusters = analytics.get("clusters", {}).get("clusters", [])
    if clusters:
        ids = [f"C{c['cluster_id']}" for c in clusters]
        cnt = [c["count"] for c in clusters]
        ax5.barh(ids, cnt, color="#34495e")
        ax5.set_xlabel("回合数")
        ax5.set_title("入场形态聚类（1h）")

    # 6 关键数字
    ax6 = fig.add_subplot(2, 3, 6)
    ax6.axis("off")
    hyp = analytics.get("h_hypothesis_checks", {})
    text = (
        f"回合总数: {analytics['session_count']}\n"
        f"胜率: {s['win_rate_pct']:.1f}%\n"
        f"持仓中位: {s['hold_hours_median']:.1f} 小时\n"
        f">24h 持仓: {hyp.get('pct_hold_over_24h', 0):.1f}%\n"
        f"开仓趋势多头: {hyp.get('open_trend_bullish_pct', 0):.1f}%\n"
        f"开仓 RSI 中位: {hyp.get('open_rsi14_median', 0):.1f}\n"
        f"盈利 RSI / 亏损 RSI:\n"
        f"  {hyp.get('rsi14_median_win', '-')} / {hyp.get('rsi14_median_loss', '-')}"
    )
    ax6.text(0.05, 0.95, text, va="top", fontsize=10,
             bbox=dict(boxstyle="round", facecolor="#ecf0f1"))

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out / "session_overview_dashboard.png", dpi=130)
    plt.close(fig)


def _plot_outcome_pie(analytics: dict, out: Path) -> None:
    wl = analytics.get("win_loss_profile", {})
    labels, sizes = [], []
    for k in ("win", "loss", "flat"):
        if k in wl:
            labels.append(f"{'盈利' if k=='win' else '亏损' if k=='loss' else '持平'}\n({wl[k]['count']})")
            sizes.append(wl[k]["count"])
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.pie(sizes, labels=labels, autopct="%1.1f%%",
           colors=["#27ae60", "#c0392b", "#7f8c8d"], startangle=140)
    ax.set_title("持仓回合盈亏结构")
    fig.tight_layout()
    fig.savefig(out / "session_outcome_pie.png", dpi=120)
    plt.close(fig)


def _plot_win_loss_indicators(analytics: dict, out: Path) -> None:
    wl = analytics.get("win_loss_profile", {})
    metrics = ["rsi14", "bb_pct", "vol_ratio"]
    labels_cn = ["RSI14", "BB位置", "量比"]
    win_vals, loss_vals = [], []
    for m in metrics:
        win_vals.append(wl.get("win", {}).get(m, {}).get("median", 0) or 0)
        loss_vals.append(wl.get("loss", {}).get(m, {}).get("median", 0) or 0)
    x = np.arange(len(metrics))
    w = 0.35
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - w / 2, win_vals, w, label="盈利单开仓", color="#27ae60")
    ax.bar(x + w / 2, loss_vals, w, label="亏损单开仓", color="#c0392b")
    ax.set_xticks(x)
    ax.set_xticklabels(labels_cn)
    ax.set_title("开仓时 1h 指标：盈利 vs 亏损（中位数）")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "session_win_loss_indicators.png", dpi=120)
    plt.close(fig)


def _plot_yearly_trends(analytics: dict, out: Path) -> None:
    yearly = analytics.get("yearly_evolution", [])
    if not yearly:
        return
    years = [r["year"] for r in yearly]
    fig, ax1 = plt.subplots(figsize=(10, 4))
    ax2 = ax1.twinx()
    ax1.bar([str(y) for y in years], [r["sessions"] for r in yearly], alpha=0.4, color="#3498db", label="回合数")
    ax2.plot([str(y) for y in years], [r["win_rate_pct"] for r in yearly], "o-", color="#e74c3c", lw=2, label="胜率%")
    ax2.plot([str(y) for y in years], [r["trend_bullish_pct"] for r in yearly], "s--", color="#27ae60", label="趋势多头%")
    ax1.set_ylabel("回合数")
    ax2.set_ylabel("%")
    ax1.set_title("年度：回合量、胜率、趋势多头占比")
    lines1, lab1 = ax1.get_legend_handles_labels()
    lines2, lab2 = ax2.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, lab1 + lab2, loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "session_yearly_trends.png", dpi=120)
    plt.close(fig)


def _plot_long_short(analytics: dict, out: Path) -> None:
    ls = analytics.get("long_short_profile", {})
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    for ax, key, title in zip(axes, ["win_rate_pct", "median_hold_hours"], ["胜率 %", "持仓中位 (h)"]):
        names = list(ls.keys())
        vals = [ls[n][key] for n in names]
        ax.bar(names, vals, color=["#2980b9", "#8e44ad"])
        ax.set_title(title)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.1f}", ha="center", va="bottom")
    fig.suptitle("多头 vs 空头 持仓回合")
    fig.tight_layout()
    fig.savefig(out / "session_long_short.png", dpi=120)
    plt.close(fig)


def _plot_clusters(analytics: dict, out: Path) -> None:
    clusters = analytics.get("clusters", {}).get("clusters", [])
    if not clusters:
        return
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    ids = [c["cluster_id"] for c in clusters]
    names = [f"簇{c}" for c in ids]
    axes[0].bar(names, [c["count"] for c in clusters], color="#2c3e50")
    axes[0].set_title("各簇回合数")
    axes[1].bar(names, [c.get("rsi14", 0) for c in clusters], color="#e67e22")
    axes[1].axhline(50, color="gray", ls="--", lw=0.8)
    axes[1].set_title("开仓 RSI14")
    axes[2].bar(names, [c.get("trend_bullish_pct", 0) for c in clusters], color="#16a085")
    axes[2].set_title("趋势多头 %")
    fig.suptitle("五种典型入场形态（KMeans 1h 特征）")
    fig.tight_layout()
    fig.savefig(out / "session_clusters_detail.png", dpi=120)
    plt.close(fig)


def _plot_hold_distribution(df: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    for outcome, color in [("win", "#27ae60"), ("loss", "#c0392b")]:
        sub = df[df["outcome"] == outcome]["hold_hours"].clip(upper=500)
        if len(sub):
            ax.hist(sub, bins=30, alpha=0.55, label=outcome, color=color)
    ax.set_xlabel("持仓小时")
    ax.set_title("盈利 / 亏损 回合的持仓时长")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "session_hold_by_outcome.png", dpi=120)
    plt.close(fig)


def _plot_open_rsi_by_outcome(df: pd.DataFrame, out: Path) -> None:
    if "open_1h_rsi14" not in df.columns:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    data = [df.loc[df["outcome"] == o, "open_1h_rsi14"].dropna() for o in ("win", "loss", "flat")]
    bp = ax.boxplot(data, tick_labels=["盈利", "亏损", "持平"], patch_artist=True)
    for patch, c in zip(bp["boxes"], ["#a9dfbf", "#f5b7b1", "#d5d8dc"]):
        patch.set_facecolor(c)
    ax.axhline(50, color="gray", ls="--", lw=0.8)
    ax.set_ylabel("开仓 1h RSI14")
    ax.set_title("开仓 RSI 分布（按盈亏）")
    fig.tight_layout()
    fig.savefig(out / "session_rsi_boxplot.png", dpi=120)
    plt.close(fig)


def _plot_trend_bullish_breakdown(df: pd.DataFrame, out: Path) -> None:
    if "open_1h_trend_bullish" not in df.columns:
        return
    ct = pd.crosstab(
        df["outcome"].map({"win": "盈利", "loss": "亏损", "flat": "持平"}),
        df["open_1h_trend_bullish"].map({1.0: "EMA多头", 0.0: "非多头"}),
        normalize="index",
    ) * 100
    fig, ax = plt.subplots(figsize=(6, 4))
    ct.plot(kind="bar", stacked=True, ax=ax, color=["#5dade2", "#f1948a"])
    ax.set_ylabel("占比 %")
    ax.set_title("盈亏分组 × 开仓趋势状态")
    ax.legend(title="1h 趋势", loc="upper right")
    plt.xticks(rotation=0)
    fig.tight_layout()
    fig.savefig(out / "session_trend_outcome.png", dpi=120)
    plt.close(fig)


def _render_html(analytics: dict, df: pd.DataFrame) -> str:
    s = analytics["summary"]
    hyp = analytics.get("h_hypothesis_checks", {})
    clusters = analytics.get("clusters", {}).get("clusters", [])
    cluster_lines = "".join(
        f"<li><b>簇{c['cluster_id']}</b>（{c['count']} 段）：RSI≈{c.get('rsi14','—')}，"
        f"趋势多头 {c.get('trend_bullish_pct', 0)}% — {_cluster_label(c)}</li>"
        for c in clusters
    )

    imgs = [
        ("总览仪表盘", "session_overview_dashboard.png"),
        ("盈亏结构", "session_outcome_pie.png"),
        ("盈利 vs 亏损 指标", "session_win_loss_indicators.png"),
        ("年度演变", "session_yearly_trends.png"),
        ("多空对比", "session_long_short.png"),
        ("聚类详情", "session_clusters_detail.png"),
        ("RSI 箱线图", "session_rsi_boxplot.png"),
        ("持仓时长", "session_hold_by_outcome.png"),
        ("趋势×盈亏", "session_trend_outcome.png"),
        ("RSI 直方图", "session_win_loss_rsi.png"),
    ]

    gallery = "\n".join(
        f'<figure class="card"><img src="{fn}" alt="{title}"/><figcaption>{title}</figcaption></figure>'
        for title, fn in imgs
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Coolish 持仓回合 — 可视化结论</title>
  <style>
    :root {{ --bg:#0f1419; --card:#1a2332; --text:#e7ecf3; --muted:#8b9cb3; --accent:#3d9cf5; --win:#2ecc71; --loss:#e74c3c; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: "Microsoft YaHei", system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 24px; line-height: 1.6; }}
    h1 {{ font-size: 1.6rem; margin-bottom: 8px; }}
    .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 24px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 28px; }}
    .kpi {{ background: var(--card); border-radius: 10px; padding: 16px; border-left: 3px solid var(--accent); }}
    .kpi .val {{ font-size: 1.5rem; font-weight: 700; }}
    .kpi .lbl {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; }}
    .conclusions {{ background: var(--card); border-radius: 12px; padding: 20px 24px; margin-bottom: 28px; }}
    .conclusions h2 {{ margin-top: 0; font-size: 1.1rem; }}
    .conclusions ul {{ margin: 0; padding-left: 1.2rem; }}
    .conclusions li {{ margin: 8px 0; }}
    .gallery {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
    .card {{ background: var(--card); border-radius: 10px; overflow: hidden; margin: 0; }}
    .card img {{ width: 100%; display: block; }}
    .card figcaption {{ padding: 10px 12px; font-size: 0.85rem; color: var(--muted); }}
    .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; margin-right: 6px; }}
    .tag-win {{ background: rgba(46,204,113,.2); color: var(--win); }}
    .tag-loss {{ background: rgba(231,76,60,.2); color: var(--loss); }}
  </style>
</head>
<body>
  <h1>Coolish 持仓回合 · 可视化结论</h1>
  <p class="sub">XBTUSD · {analytics['session_count']} 个持仓回合 · 指标源：成交 lastPx 重采样 K 线 · 本地计算（对齐 TV 参数）</p>

  <div class="kpis">
    <div class="kpi"><div class="val">{s['win_rate_pct']:.1f}%</div><div class="lbl">胜率</div></div>
    <div class="kpi"><div class="val">{s['hold_hours_median']:.1f}h</div><div class="lbl">持仓中位</div></div>
    <div class="kpi"><div class="val">{hyp.get('open_trend_bullish_pct', 0):.0f}%</div><div class="lbl">开仓趋势多头</div></div>
    <div class="kpi"><div class="val">{hyp.get('open_rsi14_median', 0):.1f}</div><div class="lbl">开仓 RSI 中位</div></div>
    <div class="kpi"><div class="val">{hyp.get('pct_hold_over_24h', 0):.0f}%</div><div class="lbl">持仓 &gt;24h</div></div>
    <div class="kpi"><div class="val">{s['total_pnl_xbt']:.2f}</div><div class="lbl">段内 PnL (XBT)</div></div>
  </div>

  <section class="conclusions">
    <h2>核心结论</h2>
    <ul>
      <li><span class="tag tag-win">波段</span>中位持仓约 <b>{s['hold_hours_median']:.1f} 小时</b>，{hyp.get('pct_hold_over_24h', 0):.1f}% 回合超过 24 小时 — 非高频剥头皮风格。</li>
      <li><span class="tag tag-win">趋势</span>约 <b>{hyp.get('open_trend_bullish_pct', 0):.0f}%</b> 回合在 1h EMA20&gt;EMA50 时开仓，偏趋势跟随而非逆势抄底为主。</li>
      <li><span class="tag">RSI</span>盈利单开仓 RSI 中位 <b>{hyp.get('rsi14_median_win', '—')}</b>，亏损单 <b>{hyp.get('rsi14_median_loss', '—')}</b> — 差异不大，单靠 RSI 阈值难以区分胜负。</li>
      <li><span class="tag">多空</span>多头中位持仓 <b>{analytics['long_short_profile']['Long']['median_hold_hours']:.1f}h</b>，空头 <b>{analytics['long_short_profile']['Short']['median_hold_hours']:.1f}h</b> — 空头更偏长线持有。</li>
      <li><span class="tag tag-loss">聚类</span>最大入场簇（n={max(clusters, key=lambda x: x['count'])['count'] if clusters else 0}）RSI 偏高（≈70），属「趋势延伸/偏热」区入场。</li>
    </ul>
    <h2>五种典型入场形态</h2>
    <ul>{cluster_lines or '<li>无聚类数据</li>'}</ul>
  </section>

  <section class="gallery">
    {gallery}
  </section>

  <p class="sub" style="margin-top:32px">数据表：<code>sessions_enriched.parquet</code> · 统计：<code>session_analytics.json</code> · 生成：<code>python -m analysis.coolish_archive.session_visualize</code></p>
</body>
</html>"""


def _cluster_label(c: dict) -> str:
    rsi = c.get("rsi14", 50)
    tb = c.get("trend_bullish_pct", 0)
    if tb > 80 and rsi > 60:
        return "趋势多头延伸"
    if tb < 20 and rsi < 40:
        return "逆势/空头环境"
    if tb < 30:
        return "非趋势或做空倾向"
    if c.get("vol_ratio", 0) > 5:
        return "高成交量异动"
    return "中性/震荡区"


def main() -> int:
    cfg = AnalysisConfig.load()
    path = build_all_visuals(cfg)
    print(f"可视化仪表盘: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""持仓回合指标标注全流程."""
from __future__ import annotations

import asyncio

from analysis.coolish_archive.enrich_sessions import enrich_sessions, export_enriched
from analysis.coolish_archive.indicator_series import build_all_indicator_series
from analysis.coolish_archive.ingest import load_trades
from analysis.coolish_archive.klines_download import download_klines
from analysis.coolish_archive.trades_klines import build_klines_from_trades
from analysis.coolish_archive.session_analytics import run_session_analytics, write_analytics_json
from analysis.coolish_archive.session_builder import build_sessions
from analysis.coolish_archive.session_report import write_session_report
from analysis.coolish_archive.session_visualize import build_all_visuals
from analysis.coolish_archive.settings import AnalysisConfig


def run_sessions_pipeline(
    cfg: AnalysisConfig,
    *,
    skip_download: bool = False,
    skip_indicators: bool = False,
) -> None:
    cfg.ensure_output()
    cfg.ensure_indicators_dir()

    trades_for_klines = None
    if not skip_download:
        if cfg.sessions.klines_source == "trades_resample":
            print("K线源: 成交 lastPx 重采样（非 Binance）...")
            trades_for_klines = load_trades(cfg)
            counts = build_klines_from_trades(trades_for_klines, cfg)
        else:
            print("下载 Binance K 线...")
            try:
                counts = asyncio.run(download_klines(cfg))
            except Exception as e:
                print(f"  Binance 下载失败 ({e})，回退到成交重采样...")
                trades_for_klines = load_trades(cfg)
                counts = build_klines_from_trades(trades_for_klines, cfg)
        print("K线:", counts)

    if not skip_indicators:
        print("批量计算指标...")
        build_all_indicator_series(cfg)

    print("构建持仓回合...")
    trades = trades_for_klines if trades_for_klines is not None else load_trades(cfg)
    sessions = build_sessions(trades, cfg)
    print(f"  回合数: {len(sessions)}")
    if sessions.empty:
        print("无 session，退出")
        return

    print("对齐开平仓指标...")
    enriched = enrich_sessions(sessions, cfg)
    path = export_enriched(enriched, cfg)
    print(f"  已写入: {path}")

    print("统计与聚类...")
    analytics = run_session_analytics(enriched, cfg)
    write_analytics_json(analytics, cfg)

    print("生成报告...")
    report = write_session_report(enriched, analytics, cfg)
    print(f"  报告: {report}")

    print("生成可视化结论...")
    dash = build_all_visuals(cfg)
    print(f"  仪表盘: {dash}")

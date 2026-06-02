#!/usr/bin/env python3
"""一键运行 Coolish 账本分析流水线."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from analysis.coolish_archive.behavior import compute_tier1_stats
from analysis.coolish_archive.hypotheses import build_hypothesis_cards
from analysis.coolish_archive.ingest import load_equity, load_orders, load_trades, load_wallet, sanity_check
from analysis.coolish_archive.positions import rebuild_positions
from analysis.coolish_archive.regimes import compute_regime_stats
from analysis.coolish_archive.report import write_outputs
from analysis.coolish_archive.run_sessions import run_sessions_pipeline
from analysis.coolish_archive.settings import AnalysisConfig


def run_archive(cfg: AnalysisConfig) -> None:
    print("加载成交...")
    trades = load_trades(cfg)
    orders = load_orders(cfg)
    wallet = load_wallet(cfg)
    equity = load_equity(cfg)

    sanity = sanity_check(cfg, trades)
    print("Sanity:", sanity)

    pos_stats = rebuild_positions(trades)
    tier1 = compute_tier1_stats(trades, orders, wallet, equity, pos_stats, cfg.btc_symbols)
    regime = compute_regime_stats(cfg, equity, wallet, pos_stats.daily_exposure, tier1)
    cards = build_hypothesis_cards(tier1, regime)

    report_path, json_path = write_outputs(
        cfg.output_dir, tier1, regime, cards, equity,
        pos_stats.daily_exposure, pos_stats.hold_sessions, sanity,
    )
    print(f"报告: {report_path}")
    print(f"JSON: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Coolish 账本分析")
    parser.add_argument(
        "--phase",
        choices=["archive", "sessions", "all"],
        default="archive",
        help="archive=原汇总；sessions=持仓回合指标；all=两者",
    )
    parser.add_argument("--skip-download", action="store_true", help="跳过 K 线下载")
    parser.add_argument("--skip-indicators", action="store_true", help="跳过指标重算")
    args = parser.parse_args()

    cfg = AnalysisConfig.load()
    cfg.ensure_output()
    print(f"数据目录: {cfg.data_dir}")
    print(f"输出目录: {cfg.output_dir}")

    if args.phase in ("archive", "all"):
        print("=== Phase: archive ===")
        run_archive(cfg)

    if args.phase in ("sessions", "all"):
        print("=== Phase: sessions ===")
        run_sessions_pipeline(
            cfg,
            skip_download=args.skip_download,
            skip_indicators=args.skip_indicators,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

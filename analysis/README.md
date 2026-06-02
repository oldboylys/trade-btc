# 研究分析

## Coolish 公开账本 (`coolish_archive`)

基于 [BTC-Trading-Since-2020](https://github.com/bwjoke/BTC-Trading-Since-2020) 的独立行为分析，不修改 `src/strategies/`。

### 运行

```bash
# 默认数据路径：仓库外同级目录 BTC-Trading-Since-2020-main
pip install -e ".[dev]"
pip install matplotlib   # 图表
python -m analysis.coolish_archive.run_all
```

环境变量：

- `COOLISH_DATA_DIR`：CSV 数据目录
- `COOLISH_OUTPUT_DIR`：输出目录（默认 `analysis/output`）

### 产出

- `docs/research/coolish_archive_report.md`
- `analysis/output/summary_stats.json`
- `analysis/output/*.png`

### 持仓回合指标（sessions）

```bash
pip install -e ".[analysis]"
python -m analysis.coolish_archive.run_all --phase sessions
```

产出：

- `docs/research/coolish_sessions_indicator_report.md`
- `analysis/output/sessions_enriched.parquet`
- `analysis/output/session_analytics.json`
- **`analysis/output/session_dashboard.html`** — 可视化结论（浏览器打开）
- `analysis/output/session_overview_dashboard.png` 等图表

单独生成可视化：

```bash
python -m analysis.coolish_archive.session_visualize
```

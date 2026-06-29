# COIN 专项配置

回测条件：85 个 ATR 有效 session（2026-02-23 ~ 2026-06-24），1000U 起步复利，`preplace_stop` + EOD，**ATR = Binance 1d**（与 live 一致）。

| 文件 | 说明 |
|------|------|
| `strategy.env` | **推荐方案**环境变量（合并进 `.env.oi` 或单独加载） |
| `symbols.txt` | 单标 COIN |
| `config.json` | 结构化参数与回测摘要 |

## 推荐方案

**OR10 + 3.0% 单笔风险 + 全天交易（tw0）**

| 指标 | 值 |
|------|-----|
| 净收益 | **+6461U**（期末 7461U） |
| 开仓数 | 83 |
| 胜率 | ~19% |
| OOS（2026-04-17 起，47 session） | **+2406U** |

| 参数 | 值 |
|------|-----|
| `ORB_OR_MINUTES` | 10 |
| `ORB_RISK_PCT` | 0.03 |
| `ORB_TRADE_WINDOW_MINUTES` | 0 |

详见 `strategy.env` 与 `output/orb/v2/eval/coin_profile.json`。

## 备选方案

| 方案 | OR | 风险 | 窗口 | 净收益 | 开仓 | 胜率 | 说明 |
|------|-----|------|------|--------|------|------|------|
| A（推荐） | 10 | 3.0% | 0 | **+6461U** | 83 | ~19% | profile 全段最优 |
| B | 10 | 2.5% | 90m | +4567U | 82 | ~18% | 更低 risk + 窗口过滤 |
| C | 10 | 2.5% | 0 | +4674U | 83 | ~19% | 仅降 risk |
| D | 15 | 3.0% | 90m | +4883U | 81 | ~20% | OR15 方案 |

备选 B/C/D 环境变量（其余与推荐方案相同：`eod`、`atr_pct`、`macro_filter=0`、一日一单）：

```bash
# B — OR10 + 2.5% + tw90
ORB_OR_MINUTES=10
ORB_RISK_PCT=0.025
ORB_TRADE_WINDOW_MINUTES=90

# C — OR10 + 2.5% + tw0
ORB_OR_MINUTES=10
ORB_RISK_PCT=0.025
ORB_TRADE_WINDOW_MINUTES=0

# D — OR15 + 3.0% + tw90
ORB_OR_MINUTES=15
ORB_RISK_PCT=0.03
ORB_TRADE_WINDOW_MINUTES=90
```

## 不可行方向（勿用）

| 方向 | 结果 |
|------|------|
| 一日多单 + `robot_reuse` | 800+ 笔，账户归零 |
| Chase 入场（`stoplimit_gap`） | ~+450U，滑点伤大赢家 |
| 仅调 filter / early_exit（1% risk） | ~+983U，远不够 4000 |

## Entry filter 结论（2026-06 研究，**不采用**）

全样本回测结论：**任何 hard entry filter 都会漏大盈日、压低 net PnL**。策略 edge 在少数 ≥5R EOD 大赢，不在胜率。

| 曾测 filter | 对 baseline 影响 |
|-------------|------------------|
| 前日 ATR / Binance 1d vol | 大赢 12→5~7，PnL 大幅下降 |
| 开盘 30min 振幅 | 漏 5/12 大盈日 |
| 5m 9/20 EMA 方向 | 漏 5/12 大盈日，PnL +6066→+1789U |
| 第一根 5m 阴阳定方向 | 漏 5/12 大盈日，PnL +6066→+1294U |
| early_exit / 30min 水下平仓 | 宽 SL 结构下更亏 |

**实盘与回测均用 baseline，不加 filter。** 研究脚本见 `tools/orb/v2/research/`（仅分析，不进 live 路径）。

## 另一条策略线

Pairs + Kalman（BTC/ETH 等）见 `config/pairs/`，与 ORB **独立**，见 `tools/pairs/run_backtest.py`。

## 实盘（COIN + CRCL + TSLA）

与 CRCL、TSLA 同池上线见 **`config/orb/live/README.md`**。本目录 `strategy.env` 在 scan 时由 `orb/core/symbol_strategy.py` 自动合并（COIN = OR10 3%）。

## 回测命令

```bash
cd next-k-api
# 将 strategy.env 中的 ORB_* 写入 .env.oi，或：
# Get-Content config/orb/COIN/strategy.env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }

python tools/orb/v2/batch_symbol_sim.py ^
  --symbols-file config/orb/COIN/symbols.txt ^
  --entry-fill preplace_stop ^
  --or-minutes 10 ^
  --no-live-filters
```

`.env.oi` 中需设 `ORB_RISK_PCT=0.03`、`ORB_TRADE_WINDOW_MINUTES=0`。

## 相对默认 V2 的改动（推荐方案）

- `ORB_OR_MINUTES`: 15 → **10**
- `ORB_RISK_PCT`: 0.01 → **0.03**
- `ORB_TRADE_WINDOW_MINUTES`: 0（保持全天）
- `ORB_MACRO_FILTER`: 1 → **0**

其余保持：5m 扫描、Binance 1d × 14 日 ATR、5% 止损、一日一单、EOD 平仓。

## 基线对比

| 配置 | 净收益 |
|------|--------|
| 默认 OR15 + 1% risk | +983U |
| OR10 + 1% risk | +1175U |
| 推荐 OR10 + 3.0% + tw0 | **+6461U** |

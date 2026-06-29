# HOOD 专项配置

回测条件：89 个 ATR 有效 session（2026-02-09 ~ 2026-06-24），1000U 起步复利，`preplace_stop` + EOD，`ORB_V2_ROBOT_RESET_CAP=0`。

> **与 COIN 不同：HOOD 在本区间 honest preplace 无法达到净收益 4000U。** 全参数网格（OR5/10/15 × risk 1~5% × tw 0/60/90/120）最高约 **+1386U**（OR15 + 5% + tw90）。

| 文件 | 说明 |
|------|------|
| `strategy.env` | **推荐方案**环境变量 |
| `symbols.txt` | 单标 HOOD |
| `config.json` | 结构化参数与回测摘要 |

## 推荐方案

**OR15 + 4% 单笔风险 + 90 分钟交易窗口**（收益/风险折中）

| 指标 | 值 |
|------|-----|
| 净收益 | **+1147U**（期末 2147U） |
| 开仓数 | 85 |
| 胜率 | ~13% |

| 参数 | 值 |
|------|-----|
| `ORB_OR_MINUTES` | 15 |
| `ORB_RISK_PCT` | 0.04 |
| `ORB_TRADE_WINDOW_MINUTES` | 90 |

详见 `strategy.env`。

## 备选方案

| 方案 | OR | 风险 | 窗口 | 净收益 | 开仓 | 胜率 | 说明 |
|------|-----|------|------|--------|------|------|------|
| A（推荐） | 15 | 4.0% | 90m | +1147U | 85 | ~13% | 收益与风险折中 |
| B | 15 | 5.0% | 90m | +1386U | 85 | ~13% | **区间最高**；连损风险最大 |
| C | 15 | 3.0% | 90m | +877U | 85 | ~13% | 更保守 |
| D | 15 | 1.0% | 0 | +267U | 89 | ~12% | 默认量级基线 |

备选 B/C/D 环境变量（其余同推荐：`eod`、`atr_pct`、`macro_filter=0`、一日一单）：

```bash
# B — OR15 + 5% + tw90（区间最高）
ORB_OR_MINUTES=15
ORB_RISK_PCT=0.05
ORB_TRADE_WINDOW_MINUTES=90

# C — OR15 + 3% + tw90
ORB_OR_MINUTES=15
ORB_RISK_PCT=0.03
ORB_TRADE_WINDOW_MINUTES=90

# D — 基线 OR15 + 1%
ORB_OR_MINUTES=15
ORB_RISK_PCT=0.01
ORB_TRADE_WINDOW_MINUTES=0
```

## HOOD vs COIN（同回测口径）

| 标的 | 1% 基线 | 推荐方案 | 能否 4000U |
|------|---------|----------|------------|
| COIN | +983U | OR10 2.5% tw90 → **+4567U** | 能 |
| HOOD | +267U | OR15 4% tw90 → **+1147U** | **不能**（最高 ~1386U） |

HOOD 胜率 ~12%、EOD 大赢家尾部弱于 COIN，提 risk 只能线性放大，无法复制 COIN 的 4000+ 结构。

## 不可行方向（勿用）

| 方向 | 结果 |
|------|------|
| 一日多单 + `robot_reuse` | 过度交易，易爆仓（同 COIN） |
| Chase 入场 | 滑点侵蚀本已偏弱的 edge |
| 提 risk 但不关 `ROBOT_RESET_CAP` | sum_pnl 虚高 |
| 照搬 COIN 的 OR10 2.5% | HOOD 上并非最优（OR15 更好） |

## 回测命令

```bash
cd next-k-api
python tools/orb/v2/batch_symbol_sim.py ^
  --symbols-file config/orb/HOOD/symbols.txt ^
  --entry-fill preplace_stop ^
  --or-minutes 15 ^
  --no-live-filters
```

`.env.oi` 中设 `ORB_RISK_PCT=0.04`、`ORB_TRADE_WINDOW_MINUTES=90`（见 `strategy.env`）。

## 相对默认 V2 的改动（推荐方案）

- `ORB_RISK_PCT`: 0.01 → **0.04**
- `ORB_TRADE_WINDOW_MINUTES`: 0 → **90**
- `ORB_MACRO_FILTER`: 1 → **0**
- `ORB_OR_MINUTES`: 保持 **15**（HOOD 上短 OR 无优势）

其余保持：5m 扫描、5%×ATR 止损、一日一单、EOD 平仓。

## 基线对比

| 配置 | 净收益 |
|------|--------|
| 默认 OR15 + 1% risk | +267U |
| 推荐 OR15 + 4% + tw90 | +1147U |
| 区间最高 OR15 + 5% + tw90 | +1386U |

# INTC 专项配置

> 由 **`tools/orb/v2/explore_symbol_profile.py INTC`** 从历史数据推导。
> 完整画像：[`output/orb/v2/eval/intc_profile.json`](../../../output/orb/v2/eval/intc_profile.json)

回测：89 ATR session（2026-02-09 ~ 2026-06-24），1000U 复利，`preplace_stop` + EOD，`ORB_V2_ROBOT_RESET_CAP=0`。

> **本区间无法达到净收益 4000U。** 全参数验证最高约 **+660U**（OR5 + 3% risk）。

| 文件 | 说明 |
|------|------|
| `strategy.env` | 推荐环境变量 |
| `symbols.txt` | 单标 INTC |
| `config.json` | 结构化摘要 |

---

## 1. 历史画像

### Session 特征

| 指标 | INTC | CRCL | COIN |
|------|------|------|------|
| 日均振幅 | 6.0% | 7.9% | ~5.8% |
| OR5 宽度 | 2.4% | 2.6% | — |
| 趋势 up/down | 45 / 38 | 36 / 41 | — |
| 震荡日 | 6 | 8 | — |

INTC 有波动、有趋势日，但 **EOD 大赢 tail 明显弱于 CRCL/COIN**（均 R ~4.7 vs 10+）。

### OR 周期对比（1% risk）

| OR | 净收益 | 胜率 | >=5R |
|----|--------|------|------|
| **OR5** | **+221U** | 16% | 5 |
| OR10 | +148U | 15% | 4 |
| OR15 | +81U | 14% | 3 |

**结论：用 OR5**，与 CRCL 相同；但 INTC 的 1% 基线仅 +221U，说明 **突破质量 / fat-tail 不足**，提 risk 只能线性放大，无法复制 CRCL 的 +1000U 基线。

### 交易解剖（OR5 @ 1%）

| 指标 | 值 |
|------|-----|
| EOD 赢 / SL 输 | 14 / 74 |
| EOD 总 PnL | +655U |
| SL 总 PnL | -434U |
| EOD 均 R | +4.7R |
| >=5R 大赢 | **5 笔** |
| EOD 1h 后仍为正 | 100% |

有 fat-tail 结构但 **tail 更短**：大赢笔数少、均 R 低，连损 74 笔吃掉大部分毛利。

### OR 宽度 / 入场时机

- OR 宽度各桶 PnL 分散，**无 CRCL 式 2.5–3.5% 甜区**；`min_or_width` 过滤会伤害收益。
- **75/88 笔** 在 OR 后 15m 内入场 → 不需要延长 `trade_window`（tw90 仅 +236U vs 基线 +221U，差异很小）。

---

## 2. 推荐方案

**OR5 + 2.5% 单笔风险，无 trade window**（收益/风险折中）

| 指标 | 值 |
|------|-----|
| 净收益 | **+553U** |
| 开仓 | 88 |
| 胜率 | ~16% |

```bash
ORB_OR_MINUTES=5
ORB_RISK_PCT=0.025
ORB_TRADE_WINDOW_MINUTES=0
```

## 3. 备选方案

| 方案 | OR | 风险 | 净收益 | 说明 |
|------|-----|------|--------|------|
| A（推荐） | 5 | 2.5% | +553U | 折中 |
| B | 5 | 3.0% | **+660U** | **区间最高** |
| C | 5 | 2.0% | +444U | 更保守 |
| D | 5 | 1.0% | +221U | 基线 |
| — | 15 | 1.0% | +81U | ❌ 勿用长 OR |

```bash
# B — 区间最高
ORB_OR_MINUTES=5
ORB_RISK_PCT=0.03
ORB_TRADE_WINDOW_MINUTES=0
```

## 4. 标的梯队（同回测口径）

| 标的 | 1% 基线 | 推荐方案 | 4000U |
|------|---------|----------|-------|
| CRCL | +1000U | OR5 2.5% → +3658U | ✅（3% → +4927U） |
| COIN | +983U | OR10 2.5% → +4567U | ✅ |
| INTC | +221U | OR5 2.5% → +553U | ❌（最高 +660U） |
| HOOD | +267U | OR15 4% → +1147U | ❌ |

INTC 与 HOOD 同属 **弱 edge 档**；若池子有限，优先 CRCL/COIN。

## 5. 不可行方向

| 方向 | 原因 |
|------|------|
| OR10/15 | 1% 基线更低 |
| `min_or_width>=2.0` | +136U，砍 profitable 样本 |
| `trade_window` | 早突破为主，延长无实质增益 |
| 提 risk 追 4000U | 3% 仅 +660U，5% 未验证且连损风险高 |
| 一日多单 / chase | 同其他标的 |

## 6. 回测 / 复现

```bash
cd next-k-api
python tools/orb/v2/explore_symbol_profile.py INTC

python tools/orb/v2/batch_symbol_sim.py ^
  --symbols-file config/orb/INTC/symbols.txt ^
  --entry-fill preplace_stop ^
  --or-minutes 5 ^
  --no-live-filters
```

## 7. 相对默认 V2 的改动

- `ORB_OR_MINUTES`: 15 → **5**
- `ORB_RISK_PCT`: 0.01 → **0.025**
- `ORB_TRADE_WINDOW_MINUTES`: 保持 **0**
- `ORB_MACRO_FILTER`: 1 → **0**

# CRCL 专项配置

> 本目录由 **`tools/orb/v2/explore_symbol_profile.py CRCL`** 从历史数据推导，非盲目网格扫描。
> 完整画像见 [`output/orb/v2/eval/crcl_profile.json`](../../../output/orb/v2/eval/crcl_profile.json)。

回测：85 ATR session（2026-02-09 ~ 2026-06-24），1000U 复利，`preplace_stop` + EOD。

| 文件 | 说明 |
|------|------|
| `strategy.env` | 推荐环境变量 |
| `symbols.txt` | 单标 CRCL |
| `config.json` | 结构化摘要 |

---

## 1. 历史画像（先于参数）

### Session 特征

| 指标 | CRCL | COIN（对照） |
|------|------|--------------|
| 日均振幅 | **7.9%** | ~5.8% |
| OR5 宽度 | **2.6%** | ~2.7%（OR15） |
| 趋势日 up/down | 36 / 41 | — |
| 震荡日 | 8 | — |

CRCL 比 COIN **更 volatile**，日内双向趋势都多，但 ORB edge 来自 **早突破 + EOD fat-tail**，不是 trend-follow 过滤。

### OR 周期对比（1% risk 基线）

| OR | 净收益 | 胜率 | >=5R 大赢 |
|----|--------|------|-----------|
| **OR5** | **+1000U** | 19% | **12** |
| OR10 | +414U | 14% | 7 |
| OR15 | +405U | 13% | 7 |

**结论：CRCL 必须用短 OR。** 与 COIN（OR10 最优）相反；长 OR 错过早盘突破，fill 质量下降。

### 交易解剖（OR5 @ 1%）

| 指标 | 值 |
|------|-----|
| EOD 赢 / SL 输 | 16 / 69 |
| EOD 总 PnL | +1637U |
| SL 总 PnL | -637U |
| EOD 均 R | **+10.2R** |
| EOD 1h 后仍为正 | **100%** |

结构类似 COIN：**低胜率 + 极大 EOD 赢家**。不宜 `early_exit`；`preplace_stop` 保留 EOD tail。

### OR 宽度分桶（1% 基线交易）

| OR 宽度 | 笔数 | 胜率 | PnL |
|---------|------|------|-----|
| 1.5–2.5% | 38 | 13% | +255U |
| **2.5–3.5%** | **27** | **26%** | **+546U** |
| 3.5–5% | 6 | 0% | -55U |

中等 OR（2.5–3.5%）贡献最多利润；`min_or_width>=2.0` 会 **砍掉 edge**（回测仅 +564U），**不建议** 加窄 OR 过滤。

### 入场时机

| 时段 | 笔数 | PnL |
|------|------|-----|
| **OR 后 <=15m** | **77** | **+894U** |
| >30m | 7 | -35U |

突破几乎全在 OR 后 15 分钟内完成；**不需要** COIN 那种 90m `trade_window` 延长，限制窗口反而略减收益。

---

## 2. 推荐方案（实盘）

**OR5 + 3.0% 单笔风险，无 trade window**

| 指标 | 值 |
|------|-----|
| 净收益 | **+6257U**（export，85 笔） |
| 开仓 | 85 |
| 胜率 | ~19% |
| 亏损合计 | −4831U（69 笔 SL） |

```bash
ORB_OR_MINUTES=5
ORB_RISK_PCT=0.03
ORB_TRADE_WINDOW_MINUTES=0
```

## 3. 备选方案

| 方案 | OR | 风险 | 窗口 | 净收益 | 说明 |
|------|-----|------|------|--------|------|
| **A（实盘）** | 5 | **3.0%** | 0 | **+6257U** | live 推荐 |
| B | 5 | 2.5% | 0 | +3658U | 更保守 |
| C | 5 | 2.0% | 0 | +2592U | 更保守 |
| D | 5 | 1.0% | 0 | +1000U | 基线 |
| — | 10 | 1.0% | 0 | +414U | ❌ 勿用长 OR |

```bash
# B — 更保守
ORB_OR_MINUTES=5
ORB_RISK_PCT=0.025
ORB_TRADE_WINDOW_MINUTES=0
```

## 4. 与 COIN / HOOD 差异

| | COIN | HOOD | CRCL |
|--|------|------|------|
| 最优 OR | 10 | 15 | **5** |
| 1% 基线 | +983U | +267U | **+1000U** |
| 4000+ 可达 | ✅ OR10 3% | ❌ | ✅ OR5 3% |
| fat-tail | 强 | 弱 | **强（12 笔 5R+）** |
| trade_window | 90m 有益 | 90m 有益 | **不需要** |

## 5. 不可行方向

| 方向 | 原因 |
|------|------|
| OR10/15 | 1% 基线仅 +400U 量级 |
| `min_or_width>=2.0` | 回测 +564U，砍掉 profitable 窄 OR |
| `trade_window` 缩短 | 77 笔在 15m 内，限制无 benefit |
| 一日多单 / robot_reuse | 同 COIN，过度交易 |
| Chase 入场 | 伤 EOD 大赢 tail |

## 6. 回测 / 复现分析

```bash
cd next-k-api

# 完整画像（session 统计 + 交易分解 + 参数验证）
python tools/orb/v2/explore_symbol_profile.py CRCL

# 单跑推荐配置
python tools/orb/v2/batch_symbol_sim.py ^
  --symbols-file config/orb/CRCL/symbols.txt ^
  --entry-fill preplace_stop ^
  --or-minutes 5 ^
  --no-live-filters
```

`.env.oi` 中设置 `ORB_RISK_PCT`（见 `strategy.env`）。

## 7. 相对默认 V2 的改动

- `ORB_OR_MINUTES`: 15 → **5**（关键）
- `ORB_RISK_PCT`: 0.01 → **0.03**
- `ORB_TRADE_WINDOW_MINUTES`: 保持 **0**
- `ORB_MACRO_FILTER`: 1 → **0**

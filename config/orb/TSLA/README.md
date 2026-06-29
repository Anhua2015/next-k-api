# TSLA 专项配置

> 由 **`tools/orb/v2/explore_symbol_profile.py TSLA`** 推导。
> 画像：[`output/orb/v2/eval/tsla_profile.json`](../../../output/orb/v2/eval/tsla_profile.json)

回测：92 ATR session（2026-02-09 ~ 2026-06-24），1000U 复利，`preplace_stop` + EOD。

## 推荐方案（实盘 R3）

**OR5 + 3.0% 单笔风险，无 trade window**

| 指标 | 值 |
|------|-----|
| 净收益 | **+2441U**（复跑 2026-06） |
| 开仓 | 91 |
| 胜率 | ~16.5% |
| >=5R 大赢 | 12 笔 |

结构与 COIN/CRCL 同类：**低胜率 + EOD fat-tail**。

## 与 COIN / CRCL 对照

| | COIN | CRCL | TSLA |
|--|------|------|------|
| 最优 OR | 10 | 5 | **5** |
| 3% 净利 | +6461U | +6257U | **+2441U** |
| fat-tail | 强 | 强 | **强（12 笔 5R+）** |

TSLA 为实盘池第三标，收益量级约为 COIN/CRCL 的 **40%**，但 edge 结构相似。

## 回测

```bash
cd next-k-api
python tools/orb/v2/batch_symbol_sim.py ^
  --symbols-file config/orb/TSLA/symbols.txt ^
  --entry-fill preplace_stop ^
  --or-minutes 0 ^
  --no-live-filters
```

上线说明见 **`config/orb/live/README.md`**。

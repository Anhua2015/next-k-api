# PLTR 专项配置

> `python tools/orb/v2/explore_symbol_profile.py PLTR` → [`pltr_profile.json`](../../../output/orb/v2/eval/pltr_profile.json)

85 ATR session | 无法 4000U | 最高 ~+381U（OR15 3%）

## 画像摘要

- **低波动**（日均振幅 4.3%），OR 偏窄（OR15 ~2.3%）
- **OR5/10 @ 1% 为负**（-16/-34U）；**OR15 @ 1% 才转正**（+162U）→ 与 CRCL 相反，PLTR 需要更长 OR 过滤假突破
- fat-tail 存在：6 笔 >=5R，EOD 均 R ~7.0，但胜率仅 **11%**
- 窄 OR（<1.5%）桶 **0% 胜率 -105U**；1.5–2.5% 桶贡献主要利润
- 晚于 OR 15m 的入场 **全部亏损**

## 推荐

**OR15 + 2.5% risk，tw=0** → **+341U**（81 笔，WR 11%）

| 备选 | 配置 | 净收益 |
|------|------|--------|
| B | OR15 3.0% | +381U |
| C | OR15 1% + min_or≥2.0% | +292U（47 笔，WR 15%） |
| D | OR15 1% 基线 | +162U |
| ❌ | OR5 1% | -16U |

```bash
ORB_OR_MINUTES=15
ORB_RISK_PCT=0.025
ORB_TRADE_WINDOW_MINUTES=0
```

可选过滤：`ORB_MIN_OR_WIDTH_PCT=2.0`（更高 WR、更低总收益）。

# NVDA 专项配置

> `python tools/orb/v2/explore_symbol_profile.py NVDA` → [`nvda_profile.json`](../../../output/orb/v2/eval/nvda_profile.json)

**53 ATR session**（数据最短）| 无法 4000U | 最高 ~+194U

## 画像摘要

- 低振幅（3.1%），极窄 OR（OR5 ~1.1%）
- **OR5 > OR10 > OR15**（1% 基线 +87 / +50 / +29U）
- 47/53 笔在 OR 后 15m 内入场；`<1.5% OR` 桶贡献 +131U
- 3 笔 >=5R，EOD 均 R ~5.0 — 有 tail 但样本少

## 推荐

**OR5 + 2.5% risk** → **+176U**（53 笔，WR 15%）

| 备选 | 净收益 |
|------|--------|
| B：3.0% risk | +194U |
| C：1.0% 基线 | +87U |
| tw60 @ 1% | +101U |

注意：**仅 53 个交易日**，结论稳定性低于 COIN/CRCL。

```bash
ORB_OR_MINUTES=5
ORB_RISK_PCT=0.025
```

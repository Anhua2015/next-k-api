# PAYP 专项配置

> `python tools/orb/v2/explore_symbol_profile.py PAYP` → [`payp_profile.json`](../../../output/orb/v2/eval/payp_profile.json)

**56 ATR session**（数据较短）| 无法 4000U | 最高 ~+202U

## 画像摘要

- 振幅 ~5.6%，偏空 bias（down 28 / up 18 天）
- **无 >=5R 大赢**（0 笔），EOD 均 R 仅 **1.6** → 几乎无 fat-tail
- OR15 略优于 OR5/10（1% 基线 +66 vs +44/+6U）
- 15–30m 入场段表现最好（+59U），但整体 edge 极弱
- 提 risk 仅线性放大：3% → +202U

## 推荐

**OR15 + 2.5% risk** → **+168U**（53 笔，WR 17%）

| 备选 | 净收益 |
|------|--------|
| B：3.0% risk | +202U |
| C：1.0% 基线 | +66U |

**梯队：弱 edge 档**，池子紧张时可降级或剔除。

```bash
ORB_OR_MINUTES=15
ORB_RISK_PCT=0.025
```

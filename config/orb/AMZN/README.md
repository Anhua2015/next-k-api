# AMZN 专项配置

> `python tools/orb/v2/explore_symbol_profile.py AMZN` → [`amzn_profile.json`](../../../output/orb/v2/eval/amzn_profile.json)

## ⚠️ 结论：本区间 **不建议** 纳入 ORB 池

85 ATR session | 所有常规配置 **净收益 ≤ 0**

## 画像摘要

- **极低波动**：日均振幅 **2.5%**，OR 宽度 ~1.0%（8 标最低）
- OR10 1% 基线 **-24U**；提 risk 至 3% → **-145U**
- 74/85 笔在 `<1.5% OR` 桶 — 突破空间不足，SL+fee 吃掉 EOD 毛利
- 虽有 4–5 笔 >=5R，但 SL 连损 73 笔（-504U）> EOD 赢（+480U）

## 唯一“正”配置（不推荐实盘）

`min_or_width≥2.0%` + OR10 + 1% → **+13U**，仅 **5 笔** / 4.5 个月，统计无意义。

## 建议

- 从 `symbols.txt` **移除 AMZN**，或单独禁用
- 若必须保留：勿提 risk；可试验 `ORB_MIN_OR_WIDTH_PCT=2.0` 但样本过少

```bash
# 仅供参考，非推荐
ORB_OR_MINUTES=10
ORB_RISK_PCT=0.01
ORB_MIN_OR_WIDTH_PCT=2.0
```

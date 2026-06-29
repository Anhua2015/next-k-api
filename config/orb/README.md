# ORB 配置（V2）

单标配置由 **`tools/orb/v2/explore_symbol_profile.py <SYMBOL>`** 从历史数据推导。

## 实盘（2026-06）

| 标的 | OR | risk | 目录 |
|------|-----|------|------|
| **COIN** | 10 | 3% | `COIN/` |
| **CRCL** | 5 | 3% | `CRCL/` |
| **TSLA** | 5 | 3% | `TSLA/` |

- 标的池：**`v2/symbols.txt`**（COIN、CRCL、TSLA）
- 上线说明：`live/README.md`
- Gate + 模型：`orb_live/`

其余目录（HOOD、PLTR、INTC 等）仅作历史回测参考，**不在实盘池**。

复现分析：`python tools/orb/v2/explore_symbol_profile.py COIN`

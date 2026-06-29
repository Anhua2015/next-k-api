# ORB 实盘 — COIN + CRCL + TSLA

三标 **独立策略、独立 robot**，同一 API 进程扫描。

| Robot | 标的 | OR | 风险 | 配置 |
|-------|------|-----|------|------|
| R1 | COIN | 10m | 3% | `config/orb/COIN/` |
| R2 | CRCL | 5m | 3% | `config/orb/CRCL/` |
| R3 | TSLA | 5m | 3% | `config/orb/TSLA/` |

## 标的池

默认 **`config/orb/v2/symbols.txt`**（COIN、CRCL、TSLA）。旧 8 标（HOOD/PLTR 等）已移出池；R4+ robot 在 scan 时自动 `enabled=0`。

## 机制

- 公共参数：本目录 `strategy.env` + `.env.oi`
- 每标 OR / risk：`config/orb/{COIN,CRCL,TSLA}/strategy.env`（`orb/core/symbol_strategy.py` 自动合并）
- `ORB_V2_ROBOT_BOUND=1`，3 robot ↔ 3 标
- 默认 **`ORB_V2_ROBOT_EQUITY=30`**（每 robot 30U，三标合计 90U 定仓基准；128U 账户 + 5x 杠杆）

## 上线

1. 合并 `strategy.env` → `.env.oi`（`ORB_V2_ROBOT_COUNT=3`、`ORB_V2_ROBOT_EQUITY=30`）
2. 部署；R4–R8 与池外 symbol bot 自动停用
3. 前端展示 COIN / CRCL / TSLA

```powershell
cd next-k-api
python orb_scanner.py --pretty
```

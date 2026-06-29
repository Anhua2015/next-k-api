# ORB 实盘 — COIN + CRCL

两标 **独立策略、独立 robot**，同一 API 进程扫描。

| Robot | 标的 | OR | 风险 | 配置 |
|-------|------|-----|------|------|
| R1 | COIN | 10m | 3% | `config/orb/COIN/` |
| R2 | CRCL | 5m | 3% | `config/orb/CRCL/` |

## 标的池

默认 **`config/orb/v2/symbols.txt`**（仅 COIN、CRCL）。旧 8 标（HOOD/PLTR 等）已移出池；R3+ robot 在 scan 时自动 `enabled=0`。

## 机制

- 公共参数：本目录 `strategy.env` + `.env.oi`
- 每标 OR / risk：`config/orb/{COIN,CRCL}/strategy.env`（`orb/core/symbol_strategy.py` 自动合并）
- `ORB_V2_ROBOT_BOUND=1`，2 robot ↔ 2 标

## 上线

1. 合并 `strategy.env` → `.env.oi`
2. 部署；R3–R8 与池外 symbol bot 自动停用
3. 前端只展示 COIN / CRCL

```powershell
cd next-k-api
python orb_scanner.py --pretty
```

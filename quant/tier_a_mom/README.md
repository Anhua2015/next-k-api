# Tier-A Mom Turn（现货）

策略 ID：`mom_turn_pool10_smart_exit`  
市场：**现货多头**（`live_leverage=1`）

## 规则摘要

| 层 | 规则 |
|----|------|
| 观察池 | Wyckoff phase=1 + ret20≤-10% + BTC 20d>-8% |
| 入场 | 池≤10 且 5 日动量转正 |
| 出场 | -8%/入场日低止损；+30%/+50% 各卖 1/3；+10% 后 trail 20EMA；最长 90 日 |
| 仓位 | 权益 × 15%，最多 5 仓（复利） |

## 数据链路

1. 扫描：`breakoutscanner` → `python run_scanner.py pool -u top100`  
   写出 `breakoutscanner/data_cache/potential_pool.json`
2. 本 lane 通过 `quant.common.scanner_potential_pool` 读取该 JSON 作为标的池
3. vnpy 日线执行：`TierAMomVnpyStrategy`

环境变量（可选覆盖路径）：

```
SCANNER_POTENTIAL_POOL_PATH=/path/to/potential_pool.json
```

## 开关（默认全关）

```
STRATEGY_TIER_A_MOM_ENABLED=1
STRATEGY_TIER_A_MOM_SHADOW=1          # 只记信号不强平
STRATEGY_TIER_A_MOM_LIVE=0             # 实盘需 1 + 交易所凭证
TIER_A_MOM_VNPY_EQUITY_USDT=100000
TIER_A_MOM_VNPY_POSITION_PCT=0.15
TIER_A_MOM_VNPY_MAX_OPEN_POSITIONS=5
```

现货交易所请用框架已有 spot gateway（如 `LIVE_EXCHANGE=binance` / `bitget_spot`），杠杆保持 1。

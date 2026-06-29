# COIN / MSTR — Binance 代币化股票

Kalman pairs on `COINUSDT` / `MSTRUSDT`（与 ORB 同一数据源）。

## 回测

```bash
cd next-k-api
python tools/pairs/run_backtest.py --config config/pairs/coin_mstr/config.json --days 180 --fetch
```

## 当前默认（2x）

| 参数 | 值 |
|------|-----|
| `entry_z` / `exit_z` / `delta` | 1.5 / 0.0 / 1e-5 |
| `cost_bps` | 2（maker） |
| `initial_capital_usdt` | **5000** |
| `deploy_pct` | 0.5 |
| `leverage` | **2** |

K 线实际 **~139d**（2026-02-09 起，RTH 1h）。92 趟，约 **+1,825 U (+36.5%)**，maxDD ~−616 U。

## 注意

- 仅 **美股交易时段** 有重叠 K 线
- 与 ORB 同标的，策略独立（spread 回归 vs RTH 突破）

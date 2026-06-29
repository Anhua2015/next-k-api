# Pairs + Kalman — AVAX / FIL

Holdout 最优 crypto-crypto pair（`pair_explore.json` 排名第 1）。

| 参数 | 值 |
|------|-----|
| entry_z / exit_z / delta | 1.5 / 0 / 1e-4 |
| capital | 10,000 U（组合里占 50%） |
| leverage | 2x，deploy 0.5 |

```bash
python tools/pairs/run_backtest.py --config config/pairs/avax_fil/config.json --days 180 --fetch
```

完整框架说明 → `config/pairs/README.md`

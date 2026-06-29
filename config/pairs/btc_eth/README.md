# Pairs + Kalman — BTC / ETH

探索 holdout 最优参数 + Ruuj 完整 framework 字段。

| 参数 | 值 |
|------|-----|
| entry_z / exit_z / delta | 2.5 / 0.25 / 1e-5 |
| capital | 10,000 U |
| P_trace | halt 95pct + 渐变 sizing |
| 成本 | maker 2bps + slip 1bps + funding 1bps/8h |

```bash
python tools/pairs/run_backtest.py --config config/pairs/btc_eth/config.json --days 180 --fetch
python tools/pairs/run_portfolio.py --days 180
```

完整框架 → `config/pairs/README.md`

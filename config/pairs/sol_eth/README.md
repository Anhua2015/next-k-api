# SOL / ETH

## 结论（180d 1h）

**当前 Kalman + z-score 框架下基本不可交易**：\|z\| 几乎从不超 2（max ~0.96），109 趟为 0。

SOL/ETH 1h 走势太同步，innovation 被 Kalman 滤成噪声，没有足够 spread 偏离。

## 若仍想研究

- 更短周期（15m/5m）——趟数↑、手续费↑
- 对数价格 + 更小 delta（样本内 \|z\|>2 仍极少）
- 换 pair（BTC/ETH、COIN/MSTR 更有效）

```bash
python tools/pairs/run_backtest.py --config config/pairs/sol_eth/config.json --days 180 --fetch
```

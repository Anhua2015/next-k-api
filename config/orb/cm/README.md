# CrackingMarkets 波动率突破（文章对齐）

对齐 [Day Trading Volatility Breakouts](https://www.crackingmarkets.com/day-trading-volatility-breakouts-systematically-all-rules-included/) 与公开 [Blueprint](https://www.crackingmarkets.com/intraday-volatility-breakout-blueprint/) 的**基线引擎**（非 ORB 开盘区间）。

## 规则（Blueprint 公开部分）

| 参数 | 值 |
|------|-----|
| 突破线 | **前收 ± 0.33 × 14日 ATR** |
| 止损 | **0.33 × ATR**（与突破距离同 k） |
| 出场 | **EOD** |
| 单笔风险 | **0.33%** |
| K 线 | **1m** |
| 频率 | 每标 **一日一单**（先触发方向） |
| 入场 | **preplace_stop**（开盘挂双 STOP） |

## 标的

文章 6 ETF：`SPY IWM QQQ GLD USO DIA`。Binance token 可用：**SPY / QQQ / IWM / DIA**（GLD、USO 无合约）。

## 与 ORB v2 的差异

| | ORB v2（COIN 等） | CM vol_breakout |
|--|-------------------|-----------------|
| 区间 | Opening Range 高低 | 前收 ± k×ATR |
| 武装时机 | OR 收盘后 | **开盘即武装** |
| 默认 risk | 1–3% | **0.33%** |
| 止损 | 5%×ATR | **0.33×ATR** |

## 回测

```powershell
cd next-k-api
Get-Content config/orb/cm/strategy.env | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
  }
}

python tools/orb/v2/batch_symbol_sim.py `
  --symbols-file config/orb/cm/symbols.txt `
  --entry-fill preplace_stop `
  --no-live-filters `
  --from-date 2026-02-09 `
  --to-date 2026-06-24
```
## 局限

- 文章**完整 market-context filter**（narrow day、trend 等）在付费墙后，当前配置为 Blueprint **无 filter 基线**。
- 数据为 Binance tokenized ETF + 本地 kline cache，与文章 IB 1m SIP 存在差异。
- 组合级 9023 笔 / 2018 起回测需更长历史与 Alpaca/IB 数据源；本目录先用现有 cache 窗口验证逻辑。


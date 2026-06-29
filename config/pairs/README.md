# Pairs + Kalman — 完整框架（Ruuj）

统计套利：**协整选 pair → Kalman 动态 β → innovation z-score → 成本 → P_trace 风控 → holdout / walk-forward**。

参考：[Pairs Trading 完整框架](https://x.com/RuujSs/status/2066545926467174765) · [Kalman Filter 完整框架](https://x.com/RuujSs/article/2069430225801490602)

---

## 目录结构

| 路径 | 说明 |
|------|------|
| `pairs/kalman.py` | 动态 hedge ratio + z = e/√S |
| `pairs/cointegration.py` | ADF + half-life 筛选 |
| `pairs/sizing.py` | P_trace 渐变仓位 + 暂停开仓 |
| `pairs/backtest.py` | USDT 钱包复利、手续费、滑点、资金费 |
| `pairs/walk_forward.py` | 滚动 OOS 验证 |
| `config/pairs/portfolio.json` | 组合分配 + 全局 framework |
| `config/pairs/btc_eth/` | BTC/ETH（探索最优参数） |
| `config/pairs/avax_fil/` | AVAX/FIL（holdout 最优） |
| `tools/pairs/run_backtest.py` | 单 pair 回测 |
| `tools/pairs/run_portfolio.py` | 多 pair 组合回测 |
| `tools/pairs/walk_forward.py` | Walk-forward CLI |
| `tools/pairs/explore_pairs.py` | 全宇宙协整 + holdout 调参 |

---

## 框架层（`portfolio.json` → `framework`）

| 参数 | 默认 | 含义 |
|------|------|------|
| `halt_p_trace_pct` | null | P_trace 超 rolling 分位 → **禁止新开仓**（可选；默认关，靠 sizing） |
| `p_trace_sizing` | true | 按 P_trace 分位 **渐变减仓**（1.0 → min_scale） |
| `p_trace_min_scale` | 0.25 | 关系不确定时最小名义比例 |
| `slippage_bps` | 1 | 开/平仓额外滑点（每腿） |
| `funding_bps_per_8h` | 1 | 永续资金费（每 8h bps，持仓扣） |
| `walk_forward_train_days` | 90 | WF 训练窗 |
| `walk_forward_test_days` | 30 | WF OOS 窗 |

---

## 快速开始

```bash
cd next-k-api

# 单 pair（完整参数在 config.json）
python tools/pairs/run_backtest.py --config config/pairs/btc_eth/config.json --days 180

# 组合：BTC/ETH + AVAX/FIL，各 1 万 U，共 2 万 U
python tools/pairs/run_portfolio.py --days 180

# Walk-forward OOS
python tools/pairs/walk_forward.py --config config/pairs/btc_eth/config.json --portfolio config/pairs/portfolio.json --days 180

# 重新探索 universe + holdout 调参
python tools/pairs/explore_pairs.py --days 180 --fetch
```

首次缺 K 线加 `--fetch`。

---

## 当前组合（2×1 万 U / pair）

| Pair | entry/exit/δ | 180d 探索参考 |
|------|--------------|---------------|
| BTC/ETH | 2.5 / 0.25 / 1e-5 | +28.6% |
| AVAX/FIL | 1.5 / 0 / 1e-4 | +28.1% |

完整框架额外扣：**maker 2bps + slippage 1bps + funding 1bps/8h**；P_trace 高时减仓或暂停开仓。

---

## 与 ORB 关系

独立策略线，24h 可跑，与 COIN ORB（RTH 方向突破）低相关。ORB 保持 baseline 无 filter → `config/orb/COIN/README.md`。

---

## 输出

| 文件 | 内容 |
|------|------|
| `output/pairs/eval/last_backtest.json` | 最近一次单 pair |
| `output/pairs/eval/portfolio.json` | 组合回测 |
| `output/pairs/eval/walk_forward.json` | WF OOS |
| `output/pairs/eval/pair_explore.json` | 全 universe 探索 |

看 **`wallet`** 字段：USDT 复利 PnL、手续费、资金费、maxDD。

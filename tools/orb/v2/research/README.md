# ORB v2 研究脚本（不进 live）

以下脚本仅用于 **filter / 特征分析**，结论见 `config/orb/COIN/README.md`：**live 与推荐回测均用 baseline，不加 entry filter**。

| 脚本 | 用途 |
|------|------|
| `ab_coin_binance1d_vol_filter.py` | 前日 ATR filter 全样本 sim |
| `ab_coin_breakout_filters.py` | 30min 振幅 / early exit |
| `ab_coin_first_bar_filter.py` | 第一根 5m 阴阳 filter |
| `analyze_coin_breakout_discriminators.py` | 真/假突破特征对比 |
| `analyze_coin_ema5_breakout.py` | 5m EMA post-hoc |
| `explore_coin_ema.py` | EMA filter 全样本 sim |

`sim_live_session.py` 中 `ema_trend_filter` 默认为 **False**；COIN `strategy.env` 不启用任何 filter。

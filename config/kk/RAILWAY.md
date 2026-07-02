# King Keltner — Railway 部署（官方 vnpy_binance 直连）

## 你需要做的

1. **Railway 部署 next-k-api**（`railway.json`，`startCommand` = `uvicorn main:app`）
2. 在 **next-k-api 服务** 配置静态出口 IP（若币安 API Key 绑定了 IP 白名单）
3. 在 **next-k-api** Variables 里设置：

```env
KK_ENGINE=vnpy
KK_ENABLED=1
KK_LIVE_ENABLED=1
BINANCE_API_KEY=你的-key
BINANCE_API_SECRET=你的-secret
BINANCE_SERVER=REAL
KK_EQUITY_USDT=14
KK_RISK_PCT=0.01
KK_LIVE_LEVERAGE=5
KK_MAX_OPEN_POSITIONS=7
```

4. **Deploy** — 无需 shell 脚本

## 启动时发生什么

`main.py` lifespan 会：

1. 启动内嵌 APScheduler
2. 自动拉起 KK vnpy 后台线程（`KK_ENGINE=vnpy`）
3. 连接官方 `BinanceLinearGateway`（WebSocket depth10 + 直连下单）
4. 加载 `KingKeltnerKkStrategy` 全池

日志搜 `[kk-vnpy]`、`BINANCE_LINEAR`。

## 构建

- `pip install -r requirements.txt -r requirements-vnpy.txt`
- `vnpy-master` 在仓库内；另装 `vnpy_binance`、`vnpy-rest`、`vnpy-websocket`

## 注意

- 一个 replica 只跑一个 vnpy 线程
- **币安 Key 放在 next-k-api**（不再经 Protocol 跳板）
- 公开行情 WebSocket **不需要** IP 白名单；**交易接口**需要 Key 与白名单对齐
- `KK_SHADOW=1` 时策略运行但 Gateway 拒单

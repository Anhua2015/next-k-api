# King Keltner — Railway 部署

## 你需要做的

1. **Railway 部署 next-k-api**（已有 `railway.json`，`startCommand` 仍是 `uvicorn main:app`）
2. **Railway 部署 Next-k-protocol**（固定 IP / 白名单跳板）
3. 在 **next-k-api 服务** 的 Variables 里设置：

```env
KK_ENGINE=vnpy
KK_ENABLED=1
PROTOCOL_API_URL=https://你的-protocol-服务.railway.app
KK_LIVE_ENABLED=1
KK_EQUITY_USDT=14
KK_RISK_PCT=0.01
KK_LIVE_LEVERAGE=5
KK_MAX_OPEN_POSITIONS=7
```

4. **Deploy** — 无需执行任何 shell 脚本

## 启动时发生什么

`main.py` 的 `lifespan` 在 API 起来后会：

1. 启动内嵌 APScheduler（热力/OI 等）
2. **自动启动 KK vnpy 后台线程**（`KK_ENGINE=vnpy` 时）
3. 等待 `PROTOCOL_API_URL` health
4. 加载 `KingKeltnerKkStrategy` 全池

日志在 Railway **Deploy Logs** 里搜 `[kk-vnpy]`。

## 构建

`railway.json` 构建命令会：

- `pip install -r requirements.txt -r requirements-vnpy.txt`
- 检查 `config/kk/symbols.txt` 存在

`vnpy-master` 在仓库内，无需额外安装 vnpy 核心包。

## 健康检查

`GET /api/health` — 可看 API 与调度器状态。

## 注意

- **不要** 在 Railway 上再跑第二个 kk 进程；一个 replica 一个 vnpy 线程即可
- `PROTOCOL_API_URL` 必须指向已部署的 protocol 服务（公网 URL 或 Railway private networking）
- 币安 API Key 只放在 **protocol** 服务的环境变量里

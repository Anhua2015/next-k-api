# Binance 实盘交易桥 (Binance Live-Trading Bridge)

币安期货实盘交易模块，内嵌于 `next-k-api/binance_bridge/`。通过环境变量 `BINANCE_ENABLED=1` 激活，与主 API 完全解耦。

---

## 架构概览

```
next-k-api/
├── main.py                  # 条件挂载 binance_bridge.router + 初始化 binance.db
├── scheduler_config.py      # 注册 binance_bridge.scheduler 中的定时任务
├── worker_tasks.py          # ZCT 扫描完成后调用 signal_bridge.on_scan_complete()
└── binance_bridge/
    ├── __init__.py          # 包标识
    ├── db.py                # SQLite 数据层（binance.db，独立于 accumulation.db）
    ├── models.py            # Pydantic 响应模型
    ├── router.py            # FastAPI 路由（prefix=/api/binance）
    ├── scheduler.py         # APScheduler 任务：sync_positions / expire_positions
    ├── signal_bridge.py     # 信号桥：ZCT 信号 → 开仓决策
    ├── trader.py            # 币安 Futures REST 执行层
    └── BINANCE.md           # 本文档
```

---

## 目录结构说明

| 文件 | 职责 |
|---|---|
| `db.py` | DDL 建表、WAL 写锁、config/signals_log/positions CRUD |
| `models.py` | Pydantic 输入/输出模型（ConfigUpdate、PositionOut、SignalLogOut 等） |
| `router.py` | FastAPI 路由，tag=`binance-live-trading`，统一出现在 `/docs` |
| `scheduler.py` | `register_binance_jobs(sch)` 注册 sync(30s) 和 expire(5min) 任务 |
| `signal_bridge.py` | 读取 accumulation.db ZCT 信号，门控逻辑，调用 `execute_trade()` |
| `trader.py` | HMAC 签名 REST、重试/退避、开仓/SL/TP/强平、PnL 记录 |

---

## 信号流（推送模型）

```
ZCT 扫描子进程完成
    └─ worker_tasks.run_zct_vwap_signal_task()
           └─ signal_bridge.on_scan_complete()         ← 事件驱动，非轮询
                  ├─ 读 accumulation.db (read-only URI) 取未结算信号
                  ├─ 跳过：已处理 / 来源不在白名单 / 同 symbol 已有持仓 / 持仓数达上限
                  ├─ insert signals_log (status=received)
                  └─ trader.execute_trade()
                         ├─ 设杠杆 / 逐仓
                         ├─ MARKET 开仓
                         ├─ 挂 SL/TP via /fapi/v1/algoOrder
                         └─ insert positions (status=open, expire_at=+4h)
```

---

## 持仓生命周期

```
open  ─┬─ (SL 触发)    → sync_open_positions()  → closed  close_reason=sl
       ├─ (TP 触发)    → sync_open_positions()  → closed  close_reason=tp
       ├─ (到期 4h)    → expire_open_positions() → closed  close_reason=expired
       └─ (手动平仓)   → sync_open_positions()  → closed  close_reason=manual/unknown
```

- `sync_open_positions()`：每 30 秒通过 `/fapi/v2/positionRisk` 检查，若仓位为 0 则检查 algo order 状态，判定 tp/sl/manual 并入库。
- `expire_open_positions()`：每 5 分钟检查 `expire_at <= now`，取消全部挂单后发 MARKET reduceOnly，`close_reason=expired`。

---

## PnL 计算公式

| 方向 | pnl_usdt | return |
|---|---|---|
| LONG | `qty × (close_price - entry_price)` | `close/entry - 1` |
| SHORT | `qty × (entry_price - close_price)` | `entry/close - 1` |

`pnl_pct = return × leverage × 100`（按保证金计算杠杆收益率）

---

## 定时任务

| job id | 函数 | 周期 |
|---|---|---|
| `binance_sync_positions` | `trader.sync_open_positions()` | 每 30 秒 |
| `binance_expire_positions` | `trader.expire_open_positions()` | 每 5 分钟 |

任务通过 `scheduler_config.register_scheduled_jobs()` 中调用 `binance.scheduler.register_binance_jobs(sch)` 注册。

---

## 鉴权

与 `next-k-api` 现有鉴权保持一致：

- 请求头 `X-Maintenance-Token: <token>` 或 `Authorization: Bearer <token>`
- Token 来自环境变量 `NEXT_K_MAINTENANCE_TOKEN`
- 依赖项 `utils.maintenance_auth.require_maintenance_token`（所有非 public 端点）
- `GET /api/binance/health` 为公开探活端点，无需 token

---

## 数据库 Schema

### config

| key | 默认值 | 说明 |
|---|---|---|
| `binance_api_key` | `""` | 币安 API Key（也可在 .env 中设置） |
| `binance_api_secret` | `""` | 币安 API Secret |
| `testnet` | `false` | `true` 使用 testnet.binancefuture.com |
| `enabled` | `false` | `true` 启用实盘开仓 |
| `position_size_usdt` | `100` | 单笔名义规模（USDT） |
| `max_positions` | `3` | 最大同时持仓数 |
| `leverage` | `10` | 杠杆倍数 |
| `enabled_sources` | `zct_vwap,zct_hot_oi` | 允许触发开仓的信号来源（逗号分隔） |
| `position_expire_hours` | `4` | 持仓到期自动平仓时限（小时） |

### signals_log

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `source` | TEXT | 信号来源（如 `zct_vwap`） |
| `api_signal_id` | TEXT | 信号唯一 ID（UNIQUE 联合 source） |
| `symbol` | TEXT | 交易对（如 `BTCUSDT`） |
| `side` | TEXT | `LONG` / `SHORT` |
| `entry_price` | REAL | 信号建议入场价 |
| `sl_price` | REAL | 止损价 |
| `tp_price` | REAL | 止盈价 |
| `confidence` | REAL | 信号置信度 |
| `regime` | TEXT | 市场状态标签 |
| `notional_usdt` | REAL | 名义规模 |
| `received_at` | TEXT | ISO8601 UTC 接收时间 |
| `status` | TEXT | `received` / `traded` / `skipped_*` / `error` |
| `skip_reason` | TEXT | 跳过原因（nullable） |

### positions

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `signal_log_id` | INTEGER FK | 关联 signals_log |
| `symbol` | TEXT | 交易对 |
| `side` | TEXT | `LONG` / `SHORT` |
| `entry_order_id` | TEXT | 入场订单 ID |
| `sl_order_id` | TEXT | SL algo 订单 ID |
| `tp_order_id` | TEXT | TP algo 订单 ID |
| `entry_price` | REAL | 实际成交均价 |
| `sl_price` | REAL | 止损触发价 |
| `tp_price` | REAL | 止盈触发价 |
| `quantity` | REAL | 合约数量 |
| `notional_usdt` | REAL | 开仓名义规模 |
| `leverage` | INTEGER | 杠杆倍数 |
| `opened_at` | TEXT | ISO8601 UTC 开仓时间 |
| `expire_at` | TEXT | ISO8601 UTC 过期时间（opened_at + expire_hours） |
| `status` | TEXT | `open` / `closed` |
| `close_reason` | TEXT | `tp` / `sl` / `expired` / `manual` / `unknown` |
| `close_price` | REAL | 实际平仓价 |
| `closed_at` | TEXT | ISO8601 UTC 平仓时间 |
| `pnl_usdt` | REAL | 盈亏（USDT） |
| `pnl_pct` | REAL | 保证金收益率（%） |

---

## REST API 端点

所有端点统一出现在 `next-k-api` 的 `/docs`，tag：`binance-live-trading`。

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/binance/health` | 无 | 存活探测 |
| GET | `/api/binance/status` | Token | 运行状态概览（enabled、持仓数等） |
| GET | `/api/binance/config` | Token | 读配置（API key/secret 脱敏） |
| POST | `/api/binance/config` | Token | 批量更新配置（body: `{"pairs": {...}}`） |
| GET | `/api/binance/signals` | Token | 信号日志（`?limit=100&offset=0`） |
| GET | `/api/binance/positions` | Token | 持仓列表（`?status=open\|closed`） |
| GET | `/api/binance/positions/{id}` | Token | 单条持仓详情 |
| GET | `/api/binance/pnl/summary` | Token | PnL 汇总 + 近 30 日日报 |
| POST | `/api/binance/trigger-signal-scan` | Token | 手动触发信号桥（测试用） |

---

## 环境变量 / 配置

### 方式 1：环境变量（启动时写入 DB，持久化存储）

在 `.env.oi` 或 Railway/Docker 环境变量中设置。首次启动时这些值通过 `INSERT OR IGNORE` 写入 `config` 表（**优先级高于默认值**），之后可通过 `POST /api/binance/config` 运行时修改。

```
BINANCE_ENABLED=1                # 必须，否则模块不加载
BINANCE_API_KEY=<key>            # 写入 binance_api_key
BINANCE_API_SECRET=<secret>      # 写入 binance_api_secret
BINANCE_TESTNET=false            # 写入 testnet
BINANCE_POSITION_SIZE_USDT=100   # 写入 position_size_usdt
BINANCE_LEVERAGE=10              # 写入 leverage
BINANCE_MAX_POSITIONS=3          # 写入 max_positions
BINANCE_EXPIRE_HOURS=4           # 写入 position_expire_hours
```

> 注意：env var 值只会写入一次（INSERT OR IGNORE）。如果 DB 中已有手动设置的值，env var 不会覆盖。修改配置优先使用 API。

### 方式 2：运行时 API

```bash
curl -X POST http://localhost:8000/api/binance/config \
  -H "X-Maintenance-Token: <token>" \
  -H "Content-Type: application/json" \
  -d '{"pairs": {"binance_api_key": "xxx", "binance_api_secret": "yyy", "enabled": "true"}}'
```

---

## 部署说明

### 本地开发

```bash
cd next-k-api
cp .env.oi.example .env.oi
# 编辑 .env.oi，填入 BINANCE_ENABLED=1 及 API key/secret
source .env.oi && python main.py
```

### Railway / Docker

1. 在环境变量面板设置 `BINANCE_ENABLED=1` 及相关 key
2. 挂载 Volume 到 `/data`，设置 `DATA_DIR=/data`（binance.db 和 accumulation.db 均写入此目录）
3. 无需额外进程，实盘桥内嵌于 API 进程

### 健康检查

```
GET /api/binance/health  →  {"status": "ok", "module": "binance-bridge"}
```

---

## FAQ

**Q: BINANCE_ENABLED 未设置时会加载模块吗？**  
A: 不会。`main.py` 在 `if sched_cfg.env_truthy("BINANCE_ENABLED"):` 块内才 import 和挂载，`worker_tasks.py` 中也有相同开关。零副作用。

**Q: 同一 symbol 可以同时持两个方向的仓位吗？**  
A: 不可以。`signal_bridge.on_scan_complete()` 在持有写锁期间检查 `get_open_position_for_symbol(symbol)`，任意方向已有持仓则跳过。

**Q: SL/TP 挂单失败怎么办？**  
A: `trader.py` 在 SL/TP 失败后立即发送紧急 MARKET 平仓，记录 status=error，不会留下裸仓。

**Q: API key 泄露了怎么办？**  
A: `sync_open_positions()` 在检测到连续 20 次 401/403 后自动将 `enabled` 设为 `false`，停止所有交易操作。轮换 key 后通过 API 重新 enable。

**Q: accumulation.db 和 binance.db 会互相锁吗？**  
A: 不会。`signal_bridge.py` 以 `file:path?mode=ro` URI 只读方式打开 accumulation.db，不获取写锁。binance.db 有独立的 `_db_write_lock`（RLock）。

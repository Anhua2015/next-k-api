# Next K API

> FastAPI 量化交易后端 -- 市场扫描、信号生成、OI 雷达、收筹池、ORB 纸面策略

**版本**: 2.0.0 | **端口**: 8000 | **语言**: Python 3.11+ | **数据库**: SQLite (WAL 模式)

---

## 目录

- [项目概览](#项目概览)
- [快速开始](#快速开始)
- [架构总览](#架构总览)
- [启动与入口](#启动与入口)
- [配置 (.env.oi)](#配置-envoi)
- [API 路由](#api-路由)
  - [Core -- 健康与状态](#core----健康与状态)
  - [Accumulation -- 收筹池与 OI 雷达](#accumulation----收筹池与-oi-雷达)
  - [ORB -- 开盘区间突破策略](#orb----开盘区间突破策略)
  - [S2 -- 费率转负信号](#s2----费率转负信号)
  - [Maintenance -- 维护与数据导出](#maintenance----维护与数据导出)
- [策略详解](#策略详解)
  - [ORB V2.2 开盘区间突破](#orb-v22-开盘区间突破)
  - [收筹池 + OI 雷达](#收筹池--oi-雷达)
  - [S2 费率转负信号](#s2-费率转负信号)
  - [信号源插件 (plugins/)](#信号源插件-plugins)
- [定时调度](#定时调度)
- [Worker 任务](#worker-任务)
- [数据库](#数据库)
- [工具脚本 (tools/)](#工具脚本-tools)
- [工具库 (utils/)](#工具库-utils)
- [依赖](#依赖)
- [部署](#部署)
- [目录结构速览](#目录结构速览)

---

## 项目概览

Next K API 是整个 `next-k` 量化交易系统的核心扫描与信号生成层。负责：

1. **收筹池扫描** -- 每日发现庄家横盘吸筹币种
2. **OI 异动雷达** -- 每小时监控持仓量异动
3. **ORB 纸面策略** -- 开盘区间突破 ML 门控策略（可接实盘）
4. **S2 费率信号** -- 费率转负 + OI 上升的逆向信号
5. **信号推送** -- 通过 HTTP POST 向 Next-k-protocol 推送交易信号

**数据流**: `next-k-api (扫描/信号)` -> HTTP POST -> `Next-k-protocol (交易执行/持仓管理)`

---

## 快速开始

```bash
cd next-k-api

# 1. 配置环境
cp .env.oi.example .env.oi
# 编辑 .env.oi 设置关键变量

# 2. 安装依赖
pip install -r requirements.txt
# 开发 / 测试
pip install -r requirements-dev.txt

# 3. 启动服务
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# 或使用启动脚本（含虚拟环境管理、双进程模式）
./start.sh

# 4. 停止服务
./stop.sh
```

启动后访问：
- API 根路径: <http://localhost:8000/>
- Swagger 文档: <http://localhost:8000/docs>
- 健康检查: <http://localhost:8000/api/health>

### 运行测试

```bash
pytest tests/ -v                          # 全部测试
pytest tests/test_orb_paper.py -v         # 单个文件
pytest tests/ -v -k "test_live_gate"      # 按名称筛选
```

---

## 架构总览

```
main.py  (FastAPI app)
  |
  +-- lifespan: 启动时初始化 DB、检查 ORB live bundle、可选启动内嵌调度器
  |
  +-- routers/
  |     +-- core         GET /, GET /api/health
  |     +-- accumulation  GET/POST OI雷达、收筹看盘、维护
  |     +-- orb           GET/POST ORB纸面策略、实盘状态
  |     +-- s2            GET 费率转负信号
  |     +-- maintenance   GET 数据卷导出
  |
  +-- worker_tasks.py   定时任务执行逻辑（子进程模式）
  +-- scheduler_config.py  定时任务注册
  +-- scheduler_main.py    独立调度器进程入口
  |
  +-- accumulation_radar.py  收筹池核心逻辑
  +-- s2_oi_funding_rate_scanner.py  费率扫描
  +-- orb_scanner.py        ORB扫描CLI
  |
  +-- orb/                  ORB策略完整模块
  |     +-- core/           核心逻辑
  |     +-- v2/             2.0 ML门控
  |     +-- ml/             机器学习模型
  |
  +-- plugins/              信号源插件
  +-- tools/                研究/训练脚本
  +-- utils/                工具库
```

---

## 启动与入口

### main.py

FastAPI 应用定义文件。关键要素：

```python
app = FastAPI(
    title="Next K",
    description="OI radar, accumulation watchlists, ORB strategy API.",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 全开放（允许跨域前端调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 5 个路由模块
app.include_router(core_router.router)
app.include_router(maintenance_router.router)
app.include_router(accumulation_router.router)
app.include_router(orb_router.router)
app.include_router(s2_router.router)
```

### lifespan 启动流程

`@asynccontextmanager` 生命周期管理器，在应用启动时依次执行：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `state.startup_time` 记录 | 写入 UTC 时间戳 |
| 2 | 内嵌/独立调度器判断 | `NEXT_K_EMBED_SCHEDULER` 控制 |
| 3 | `init_db()` 初始化 accumulaton.db | 创建 SQLite WAL 表结构 |
| 4 | ORB 生产环境检查 | 检测 `orb_live/` 参数包是否就绪 |
| 5 | ORB live bundle 自举 | 首次启动自动复制模型文件 |
| 6 | ORB symbols 日志 | 打印标的池加载情况 |
| yield | 服务就绪 | |
| 7 | 调度器关闭 | SIGTERM 时优雅停止 APScheduler |

### 双进程模式

| 模式 | 环境变量 | 说明 |
|------|----------|------|
| **内嵌调度** (默认) | `NEXT_K_EMBED_SCHEDULER=1` 或不设 | APScheduler 在主 API 进程内运行 |
| **独立调度** | `NEXT_K_EMBED_SCHEDULER=0` | API 进程不含调度器，需单独运行 `python scheduler_main.py` |

双进程模式适用场景：生产环境需隔离 API 请求处理与定时扫描负载。

### app_state.py

```python
class AppState:
    startup_time: datetime | None = None

state = AppState()  # 全局单例
```

极简全局状态，仅记录启动时间供健康检查计算 uptime。

---

## 配置 (.env.oi)

`env_loader.py` 在 main.py 最顶部调用 `load_env_oi()`，从 `.env.oi` 文件加载所有环境变量（不覆盖已存在的变量）。

### 完整环境变量表

#### 调度与路径

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEXT_K_EMBED_SCHEDULER` | `1` | `1`=内嵌调度器，`0`=独立调度器进程 |
| `DATA_DIR` | 项目根目录 | SQLite 数据库与运行时 JSON 文件目录 |
| `NEXT_K_EXPORT_VOLUME_ENABLED` | `0` | 启用 `/export-volume` 数据卷打包下载接口 |

#### ORB 策略参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ORB_MARKET` | `us_equity` | 市场类型: `crypto` 或 `us_equity` |
| `ORB_SIGNAL_INTERVAL` | `5m` | 信号 K 线周期: `1m/2m/3m/5m/15m` |
| `ORB_OR_MINUTES` | `15` | 开盘区间分钟数 |
| `ORB_SESSION_TZ` | `America/New_York` | 交易时段时区 |
| `ORB_ENTRY_MODE` | `breakout` | 入场模式: `breakout` 或 `retest` |
| `ORB_CONFIRM_BARS` | `1` | 确认 K 线数（突破后连续站稳的 bar 数） |
| `ORB_EXIT_MODE` | `eod` | 离场模式: `eod`(收盘平仓) 或 `fixed_r`(固定R倍数) |
| `ORB_SL_MODE` | `atr_pct` | 止损模式: `atr_pct`(ATR百分比) 或 `or_range`(OR区间) |
| `ORB_ATR_PERIOD` | `14` | ATR 计算周期 |
| `ORB_ATR_SL_FRACTION` | `0.05` | ATR 止损分数（5% ATR） |
| `ORB_RISK_PCT` | `0.01` | 单笔风险比例（1%） |
| `ORB_SYMBOL_BOT_EQUITY` | `1000` | 每标机器人初始虚拟本金 (USDT) |
| `ORB_ACCOUNT_EQUITY` | `1000` | 账户总权益 (USDT) |
| `ORB_LEVERAGE` | `10` | 杠杆倍数 |
| `ORB_MARGIN_USDT` | `100` | 保证金 (USDT) |
| `ORB_MAX_OPEN_POSITIONS` | `6` | 最大同时持仓数 |
| `ORB_SYMBOLS` | 美股标的列表 | 逗号分隔的扫描标的，如 `COINUSDT,PAYPUSDT,...` |
| `ORB_VWAP_FILTER` | `false` | VWAP 过滤器开关 |
| `ORB_MACRO_FILTER` | `true` | 宏观事件过滤（FOMC/CPI等前夕跳过） |
| `ORB_SL_BUFFER_BPS` | `5.0` | 止损缓冲(bps) |
| `ORB_MIN_SL_PCT` | `0.0` | 最小止损百分比 |
| `ORB_POSITION_SAFETY_PCT` | `0.15` | 仓位安全比例 |

#### ORB 实盘与 V2

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ORB_LIVE_ENABLED` | `0` | `1`=启用实盘交易（通过 Protocol 执行） |
| `ORB_LIVE_LEVERAGE` | `10` | 实盘杠杆（覆盖 ORB_LEVERAGE） |
| `PROTOCOL_API_URL` | `http://localhost:8001` | Next-k-protocol 地址 |
| `ORB_V2_ENABLED` | `1` | ORB V2.0 ML Gate 开关 |
| `ORB_V2_SCHEDULER_ENABLED` | `1` | V2 定时扫描开关 |
| `ORB_V2_SHADOW` | `0` | 影子模式（只打分不开单） |
| `ORB_V2_SYMBOLS` | (空) | 逗号分隔的 V2 标的列表 |
| `ORB_V2_SYMBOLS_FILE` | `config/orb/v2/symbols.txt` | V2 标的文件路径 |
| `ORB_V2_ROBOT_COUNT` | `8` | 机器人数量（资金池分片） |
| `ORB_V2_ROBOT_EQUITY` | `1000` | 每机器人初始资金 (USDT) |

#### ORB ML 与训练

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ORB_LIVE_BUNDLE_ROOT` | `orb_live/` | 实盘参数包目录 |
| `ORB_SCAN_INTERVAL_MINUTES` | `5` | ORB 扫描间隔（分钟，须整除60） |
| `ORB_SCAN_CRON_SECOND` | `5` | ORB cron 秒偏移 |
| `ORB_ML_KLINE_REFRESH_ENABLED` | `1` | 每月 K 线自动刷新 |
| `ORB_ML_KLINE_DAYS` | `180` | K 线拉取天数 |
| `ORB_V2_MONTHLY_TRAIN_ENABLED` | `0` | 每月自动训练模型 |
| `ORB_KLINE_CACHE_ROOT` | `data/orb/kline` | K 线缓存目录 |

---

## API 路由

### Core -- 健康与状态

| 方法 | 路径 | 说明 | 参数 | 响应 |
|------|------|------|------|------|
| `GET` | `/` | API 根路径 | -- | `{name, version, description, docs, health}` |
| `GET` | `/api/health` | 健康检查 | -- | `{status, version, uptime, scheduler_embedded, scheduler_running}` |

**`GET /`** 示例响应:

```json
{
  "name": "Next K API",
  "version": "2.0.0",
  "description": "OI / accumulation / ORB API",
  "docs": "/docs",
  "health": "/api/health"
}
```

**`GET /api/health`** 示例响应:

```json
{
  "status": "healthy",
  "version": "2.0.0",
  "uptime": 12345.6,
  "scheduler_embedded": true,
  "scheduler_running": true
}
```

### Accumulation -- 收筹池与 OI 雷达

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| `GET` | `/api/accumulation/oi-radar` | OI 雷达快照（从磁盘 JSON 读取） | -- |
| `GET` | `/api/accumulation/heat-accum-watch` | 热度+收筹看盘 | -- |
| `GET` | `/api/accumulation/ambush-watch` | 埋伏榜（暗流+低市值） | -- |
| `GET` | `/api/accumulation/focus-watch` | 重点关注（逼空/天量/暗流） | -- |
| `GET` | `/api/accumulation/patrick-core-watch` | Patrick核心（收筹池+OI异动） | -- |
| `GET` | `/api/accumulation/worth-watch` | 值得关注七类归档 | `?category=heat_accum` |
| `POST` | `/api/accumulation/oi-radar/refresh` | 触发 OI 雷达刷新（后台线程） | -- |
| `POST` | `/api/accumulation/maintenance/trigger-cron` | 手动触发定时任务 | `{task: "pool"\|"oi"\|"s2_funding"\|...}` |
| `POST` | `/api/accumulation/maintenance/clear-watch-tables` | 清空看盘表 | `{tables: ["watchlist", ...]}` |
| `POST` | `/api/accumulation/maintenance/refresh-heat-watch` | 刷新热度看盘整表 | -- |

#### GET /api/accumulation/oi-radar

从 `oi_radar_snapshot.json` 读取最新快照（磁盘缓存），响应极快（避免长连接超时）。若无快照返回错误提示。

#### GET /api/accumulation/worth-watch

支持 `?category=` 查询参数筛选七类归档之一：
`heat_accum`, `patrick_composite`, `hot_oi`, `chase_fire`, `dual_signal`, `ambush_lowcap`, `ambush_dark_flow`

#### POST /api/accumulation/maintenance/trigger-cron

支持的 task 键值：

| task 键 | 对应函数 | 说明 |
|---------|----------|------|
| `pool` | `run_pool_task` | 每日收筹池扫描 |
| `heat_watch` | `run_heat_watch_refresh_task` | 热度看盘刷新 |
| `oi` | `run_oi_task` | OI 异动扫描 |
| `s2_funding` | `run_s2_oi_funding_task` | 费率转负扫描 |
| `orb_scan` | `run_orb_scan_task` | ORB 纸面扫描 |
| `orb_v2_monthly_train` | `run_orb_v2_monthly_train_task` | 月度模型训练 |
| `orb_ml_kline_refresh` | `run_orb_ml_kline_refresh_task` | K线刷新 |

#### POST /api/accumulation/oi-radar/refresh

- 后台线程执行完整扫描写入快照
- 并发锁 + 120秒冷却（`OI_RADAR_REFRESH_COOLDOWN_SEC`）防滥用
- 立即返回 `{accepted: true}`，前端轮询 GET 获取结果

### ORB -- 开盘区间突破策略

前缀: `/api/orb`

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| `GET` | `/api/orb/live` | 实盘启用状态 | -- |
| `GET` | `/api/orb/live-bundle` | Live 参数包就绪状态 | -- |
| `GET` | `/api/orb/session/today` | 今日交易时段信息 | -- |
| `GET` | `/api/orb/summary` | ORB 策略摘要 | -- |
| `GET` | `/api/orb/signals` | 信号列表 | `?limit=&offset=&symbol=&status=all\|open\|settled` |
| `GET` | `/api/orb/scan/latest` | 最近一次扫描结果 | -- |
| `POST` | `/api/orb/maintenance/scan` | 手动触发扫描 | (需维护令牌) |
| `POST` | `/api/orb/maintenance/clear-db` | 清空 ORB 数据库表 | (需维护令牌) |

#### GET /api/orb/summary

返回完整的策略摘要，包括：

```json
{
  "ok": true,
  "lane": "orb_v2",
  "strategy": "orb_v2",
  "orb_version": 2,
  "open_positions": 2,
  "settled_trades": 47,
  "sum_pnl_usdt": 123.45,
  "touch_win_rate": 0.5532,
  "outcome_breakdown": {"win": 26, "loss": 21},
  "robot_count": 8,
  "robot_equity_usdt": 1000.0,
  "universe_count": 38,
  "symbols_file": "config/orb/v2/symbols.txt",
  "robots": [...],
  "gate": {
    "min_p_true": 0.35,
    "max_opens_per_day": 8,
    "robot_reuse_after_exit": false,
    "day_abort_enabled": false
  },
  "today": { ... },
  "live_enabled": false,
  "protocol_configured": true
}
```

#### GET /api/orb/signals

参数：
- `limit` (1-1000, 默认 200): 返回条数
- `offset` (>=0, 默认 0): 分页偏移
- `symbol` (可选): 按标的筛选
- `status`: `all` | `open` | `settled`

信号状态映射：
| outcome | 显示状态 |
|---------|----------|
| `win` | 盈利 |
| `loss` | 止损 |
| `expired` | 超时 |
| `session_close` | 收盘平仓 |
| `early_exit` | 提前离场 |
| `supersede` | 信号结束 |
| 持仓中 (有 side + sl_price) | 持仓中 |
| 其他 | 观望 |

### S2 -- 费率转负信号

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| `GET` | `/api/s2/funding-signals` | 近2日费率转负+OI上涨信号 | -- |

数据源：`accumulation.db` 表 `s2_funding_signals`，按 `recorded_at` 降序返回近2日记录。

### Maintenance -- 维护与数据导出

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| `GET` | `/api/export-volume/info` | 查看 DATA_DIR 体量摘要 | (需 `NEXT_K_EXPORT_VOLUME_ENABLED=1`) |
| `GET` | `/export-volume` | 下载 DATA_DIR 打包 | `?fmt=zip\|tar.gz` (需启用) |

导出接口供 Railway Volume 数据迁移使用。下载完成后自动清理临时文件。

---

## 策略详解

### ORB V2.2 开盘区间突破

全称 Opening Range Breakout，是基于开盘区间突破 + ML 门控的量价策略。

#### 模块结构

```
orb/
  __init__.py
  core/
    config.py          -- OrbConfig 数据类 + 环境变量解析
    breakout.py        -- 突破检测逻辑
    signals.py         -- OrbSignal 信号模型 + 仓位计算
    paper.py           -- 纸面扫描核心流程（日线加载/信号UPSERT/复盘）
    resolve.py         -- 持仓结算（walk-forward 1m bars）
    db.py              -- SQLite ORB 表 + 查询辅助
    kline_cache.py     -- K线缓存
    indicators.py      -- 技术指标（ATR等）
    backtest.py        -- 回测引擎
    backtest_ml.py     -- ML 回测
    session_today.py   -- 今日交易时段构建
    session.py         -- 交易时段锚点计算
    live_exec.py       -- 实盘信号 -> Protocol 推送
    live_settings.py   -- 实盘状态读取
    protocol_client.py -- Protocol HTTP 客户端
    tz.py              -- 时区工具
    macro_calendar.py  -- 宏观事件日历过滤
    us_equity_calendar.py -- 美股交易日历
  v2/
    config.py          -- OrbV2Config (继承 OrbConfig + ML gate + 标的池)
    paper.py           -- V2 纸面扫描 (ML Live Gate + 8-robot 资金池)
    db.py              -- V2 辅助表 (breakout_seen, gate_day, runs)
    robots.py          -- 8-robot 资金池管理
    gate_state.py      -- Gate 日状态持久化
    paths.py           -- 路径解析
  ml/
    features.py        -- 12 维特征提取
    gbm.py             -- LightGBM HistGradientBoosting 模型
    ranker.py          -- BreakoutRanker 打分器
    gate.py            -- LiveGate 实盘门控决策
    profiles.py        -- 标的先验画像 (Bayesian Tier A/B/C)
    samples.py         -- 训练样本构建
    live_bundle.py     -- 参数包管理 (orb_live/)
    horizon.py         -- 时域衰减
    gate_replay.py     -- Gate 回放验证
    live_gate_sim.py   -- Gate 模拟
    paths.py           -- ML 路径配置
    model/             -- 模型训练/验证/自动配置
      train.py, validate.py, auto_config.py,
      bundle.py, gate_tune.py, manifest.py,
      paths.py, promote.py
```

#### 策略流程

```
Session Setup
    |
    v
Opening Range Computation (开盘区间: 前 15 分钟 High/Low)
    |
    v
Breakout Detection (价格突破 OR 边界)
    |
    v
Volume / VWAP / Width Filters (量价过滤)
    |
    v
ML Gate: 12维特征提取 -> GBM 打分 (p_true) + 标的先验画像
    |
    v
Live Gate 决策: min_p_true 阈值 + day cap + early trap 过滤
    |
    v
Position Sizing: risk-based (风险比例) 或 fixed (固定名义)
    |
    v
8-Robot 资金池分配
    |
    v
SL/TP 放置 (ATR 百分比止损 + 可选 R 倍数止盈)
    |
    v
Resolution: walk-forward 1m bars 复盘 (逐 bar 检查 SL/TP/EOD)
```

#### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `signal_interval` | `5m` | 信号 K 线周期 |
| `or_minutes` | `15` | 开盘区间时间窗口 |
| `entry_mode` | `breakout` | 入场方式（突破入场） |
| `confirm_bars` | `1` | 确认 bar 数 |
| `exit_mode` | `eod` | 收盘平仓 |
| `sl_mode` | `atr_pct` | ATR 百分比止损 |
| `atr_period` | `14` | ATR 周期 |
| `atr_sl_fraction` | `0.05` | ATR 止损比例 |
| `risk_pct` | `0.01` | 风险比例 |
| `symbol_bot_equity_usdt` | `$1000` | 每标机器人本金 |
| `leverage` | `10x` | 杠杆 |
| `max_open_positions` | `6` | 最大持仓 |
| `macro_filter` | `true` | 宏观事件过滤 |

#### ML 模型详情

**模型类型**: LightGBM HistGradientBoostingClassifier (scikit-learn)

| 超参 | 值 |
|------|-----|
| `max_depth` | 4 |
| `learning_rate` | 0.08 |
| `max_iter` | 200 |
| 备选模型 | Logistic Regression |

**12 维特征**:

| 特征 | 说明 |
|------|------|
| `or_width_pct` | OR 宽度百分比 |
| `vol_ratio` | 成交量比率 (相对 MA) |
| `side_long` | 是否为多头 |
| `vwap_dist_pct` | 距 VWAP 距离 |
| `risk_frac_pct` | 风险分数 |
| `minutes_after_or` | OR 结束后分钟数 |
| `gap_pct` | 跳空百分比 |
| `pm_rvol` | 盘前相对成交量 |
| `pm_regime_go` | 盘前做多信号 |
| `pm_regime_fade` | 盘前反向信号 |
| `atr_pct` | ATR 百分比 |
| `sync_same_side` | 同步同向信号 |

**标的先验画像 (Bayesian Symbol Prior)**:

| Tier | 说明 | Gate 调整 |
|------|------|-----------|
| Tier A | 高胜率标的 | 标准阈值 |
| Tier B | 中等胜率标的 | 标准阈值 |
| Tier C | 低胜率标的 | `min_p_true + tier_c_extra_min_p` |

#### orb_live/ 实盘参数包

实盘与回测统一从此目录加载 Gate + 模型，改参数直接覆盖文件无需重启。

| 文件 | 说明 |
|------|------|
| `live_gate.json` | Gate 参数（min_p_true、max_opens_per_day、early_trap等） |
| `breakout_gbm.pkl` | GBM 模型文件（必需） |
| `breakout_gbm.json` | 模型元信息/指标 |
| `symbol_breakout_profiles.json` | 标的先验画像（必需） |
| `breakout_gbm_train_report.json` | 训练报告（参考） |
| `bundle_manifest.json` | 打包清单 |

#### 实盘交易代码路径

`ORB_LIVE_ENABLED=1` 时的信号流转：

```
orb_scanner.py
  -> orb/v2/paper.py (扫描 + ML Gate + 信号生成)
    -> orb/core/live_exec.py (构建开仓/平仓 payload)
      -> orb/core/protocol_client.py (HTTP POST)
        -> Next-k-protocol: POST /api/binance/signals/ingest
```

#### LiveGateConfig 参数

从 `orb_live/live_gate.json` 加载：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_opens_per_day` | 8 | 单日最大开仓数 |
| `min_p_true` | 0.35 | 最低突破概率阈值 |
| `early_trap_minutes` | 20 | early trap 窗口（分钟） |
| `early_trap_sync_min` | 3 | trap 检测 sync 下限 |
| `early_trap_sync_max` | 14 | trap 检测 sync 上限 |
| `day_abort_after_signals` | 8 | 触发日终止的信号数 |
| `day_abort_median_p_max` | 0.32 | 日终止中位数阈值 |
| `day_abort_enabled` | false | 日终止开关 |
| `tier_c_extra_min_p` | 0.05 | Tier C 额外阈值 |
| `robot_reuse_after_exit` | false | 机器人退出后可复用 |

### 收筹池 + OI 雷达

核心模块：`accumulation_radar.py`

#### 核心逻辑（Patrick 理论）

1. **庄家拉盘前必须先收筹** -> 长期横盘 + 低成交量 = 收筹中
2. **OI 暴涨** = 大资金进场建仓 = 即将拉盘
3. **两个信号叠加** = 最强信号

#### 两种模式

| 模式 | 触发 | 说明 |
|------|------|------|
| **pool** (收筹池) | 每日 10:00 CST | 发现正在被庄家收筹的币种 |
| **oi** (OI 雷达) | 每小时 :30 | 标的池内币种的 OI 异动监控 |

#### 看盘表体系

| 表名 | 说明 | 保留策略 |
|------|------|----------|
| `watchlist` | 收筹池主表 | 14 天（`WATCHLIST_RETENTION_DAYS`） |
| `heat_accum_watch` | 热度+收筹看盘 | 2 天 |
| `ambush_watch` | 埋伏榜（暗流/低市值+OI） | 2 天 |
| `focus_watch` | 重点关注（逼空/天量/暗流+否决） | 2 天 |
| `patrick_core_watch` | Patrick核心（收筹池+6h OI 达标） | 2 天 |
| `worth_watch_*` (7张) | 值得关注七类归档 | 7 天（`WORTH_WATCH_RETENTION_DAYS`） |

**值得关注七类**:

| 类别 | 表名 | 说明 |
|------|------|------|
| `heat_accum` | `worth_watch_heat_accum` | 热度+收筹双高 |
| `patrick_composite` | `worth_watch_patrick_composite` | pool_sc + OI 综合评分 |
| `hot_oi` | `worth_watch_hot_oi` | 热度 + OI 差值 |
| `chase_fire` | `worth_watch_chase_fire` | 费率负值追涨 |
| `dual_signal` | `worth_watch_dual_signal` | 双重信号共振 |
| `ambush_lowcap` | `worth_watch_ambush_lowcap` | 低市值埋伏 |
| `ambush_dark_flow` | `worth_watch_ambush_dark_flow` | 暗流埋伏 |

每类动态门槛 + 至多 5 条入库，无人达标时回退为至少 2 名。

#### OI 雷达快照机制

- 每小时扫描写入 `oi_radar_snapshot.json`（磁盘缓存）
- GET 接口直接从磁盘读取，响应极快
- POST refresh 接口在后台线程扫描后更新快照

### S2 费率转负信号

核心模块：`s2_oi_funding_rate_scanner.py`

#### 检测逻辑

1. OI 四段首尾抬升（OI 持续放大）
2. 费率由非负 -> 足够负（`MIN_CURR_FR_FOR_FLIP`）
3. 两个条件同时满足 = 逆向信号（市场过度看空后的反转机会）

#### 数据存储

- **DB**: `accumulation.db` 表 `s2_funding_signals`
- **运行时**: `fr_snapshot.json` (费率快照，含时间戳)
- **历史**: `oi_funding_alerts.json` (告警历史)

快照间隔过短或过长时自动跳过一期对比，避免部署或久置后的假阳性。

### 信号源插件 (plugins/)

位于 `plugins/` 目录，每个子目录是一个独立信号源/策略。

| 插件 | 说明 |
|------|------|
| `accumulation` | 收筹池信号 |
| `oi_funding` | OI+费率信号 |
| `orb` | ORB 信号 |

这些插件生成的信号通过 HTTP POST 推送到 Next-k-protocol：
```
POST http://localhost:8001/api/binance/signals/ingest
Content-Type: application/json
{"signals": [...]}
```

---

## 定时调度

`scheduler_config.py` 管理所有定时任务。时区为 `Asia/Shanghai` (CST, UTC+8)，ORB 扫描使用 `UTC` 时区。

### 注册的 Cron 任务

| 任务 ID | 调度 | 函数 | 功能 |
|---------|------|------|------|
| `pool_daily` | 每日 10:00 CST | `run_pool_task()` | 收筹池每日扫描 |
| `heat_watch_refresh` | 每小时 :07 CST | `run_heat_watch_refresh_task()` | 热度看盘整表刷新 |
| `oi_hourly` | 每小时 :30 CST | `run_oi_task()` | OI 异动每小时扫描 |
| `s2_oi_funding` | 每小时 :05 CST | `run_s2_oi_funding_task()` | 费率转负扫描 |
| `orb_scanner` | 每 5min :05 UTC | `run_orb_scan_task()` | ORB ML Gate 纸面扫描 |
| `orb_ml_kline_refresh` | 每月 1 日 02:00 CST | `run_orb_ml_kline_refresh_task()` | K 线数据刷新 |
| `orb_v2_monthly_train` | 每月 1 日 03:00 CST | `run_orb_v2_monthly_train_task()` | 月度模型训练（默认关闭） |

### 关键开关逻辑

- `ORB_V2_SCHEDULER_ENABLED=0` -> 跳过 `orb_scanner`
- `ORB_V2_MONTHLY_TRAIN_ENABLED=0` -> 跳过 `orb_v2_monthly_train`（默认关闭）
- `ORB_ML_KLINE_REFRESH_ENABLED=0` -> 跳过 `orb_ml_kline_refresh`
- `ORB_V2_ENABLED=0` -> 跳过 ORB 纸面扫描

### ORB 扫描 Cron 对齐

ORB 扫描使用 UTC 时区对齐 Cron，要求 `ORB_SCAN_INTERVAL_MINUTES` 能整除 60：
- 整除 60 -> `minute=*/N, second=5, timezone=UTC`
- 不能整除 -> 回退 `IntervalTrigger(minutes=N)`（不推荐）

---

## Worker 任务

`worker_tasks.py` 是定时任务的实际执行层。所有任务以子进程方式运行脚本（除 heat_watch 刷新在进程内执行），使用 `threading.Lock` 防止并发重叠。

### 任务函数清单

| 函数 | 锁键 | 执行的脚本 |
|------|------|-----------|
| `run_pool_task()` | `accumulation_pool` | `python accumulation_radar.py pool` |
| `run_oi_task()` | `accumulation_oi` | `python accumulation_radar.py oi` |
| `run_heat_watch_refresh_task()` | `heat_watch_refresh` | 进程内（`refresh_all_heat_accum_watch_full`） |
| `run_s2_oi_funding_task()` | `s2_funding` | `python s2_oi_funding_rate_scanner.py` |
| `run_orb_scan_task()` | `orb_scan` | `python orb_scanner.py` |
| `run_orb_v2_monthly_train_task()` | `orb_v2_monthly_train` | `python tools/orb/v2/monthly_train.py` |
| `run_orb_ml_kline_refresh_task()` | `orb_ml_kline_refresh` | `python tools/orb/v2/refresh_klines.py` |

### 子进程保护机制

`_run_subprocess_locked()` 确保：
- 同一类型任务不会并发执行（非阻塞锁，上一轮未完成则跳过本轮）
- 子进程异常不影响主进程
- 锁在 finally 中释放

---

## 数据库

### accumulation.db (SQLite WAL 模式)

**连接**: 通过 `accumulation_radar.init_db()` 获取连接，WAL 模式自动启用。

**所有表**:

| 表名 | 用途 | 模块 |
|------|------|------|
| `watchlist` | 收筹池主表 | accumulation_radar |
| `heat_accum_watch` | 热度+收筹看盘 | accumulation_radar |
| `ambush_watch` | 埋伏榜 | accumulation_radar |
| `focus_watch` | 重点关注 | accumulation_radar |
| `patrick_core_watch` | Patrick核心 | accumulation_radar |
| `worth_watch_heat_accum` | 值得关注-热度收筹 | accumulation_radar |
| `worth_watch_patrick_composite` | 值得关注-Patrick | accumulation_radar |
| `worth_watch_hot_oi` | 值得关注-热OI | accumulation_radar |
| `worth_watch_chase_fire` | 值得关注-追涨 | accumulation_radar |
| `worth_watch_dual_signal` | 值得关注-双重 | accumulation_radar |
| `worth_watch_ambush_lowcap` | 值得关注-低市值 | accumulation_radar |
| `worth_watch_ambush_dark_flow` | 值得关注-暗流 | accumulation_radar |
| `s2_funding_signals` | 费率转负信号 | s2_oi_funding_rate_scanner |
| `fr_persist` | 费率快照持久化 | s2_oi_funding_rate_scanner |
| `orb_signals` | ORB 信号主表 | orb/core/db.py |
| `orb_settlements` | ORB 结算记录 | orb/core/db.py |
| `orb_symbol_bots` | 单标机器人资金表 | orb/core/db.py |
| `orb_runs` | ORB 扫描运行记录 | orb/core/db.py |
| `orb_v2_breakout_seen` | V2 突破记录 (session_date+symbol 唯一) | orb/v2/db.py |
| `orb_v2_gate_day` | V2 Gate 日状态 | orb/v2/db.py |
| `orb_v2_runs` | V2 扫描运行记录 | orb/v2/db.py |
| `orb_robots` | 8-robot 资金池 | orb/v2/robots.py |

### ORB 核心表结构

**orb_signals** - 信号主表：

```
id, recorded_at_utc, updated_at_utc, symbol (UNIQUE), play, side,
confidence, entry_price, entry_bar_open_ms, sl_price, tp_price,
r_unit, virtual_notional_usdt, or_high, or_low, or_width_pct,
session_date, volume, vol_ma, mark_price, unrealized_pnl_usdt,
outcome, outcome_at_utc, exit_price, pnl_r, pnl_usdt, exit_rule,
reasons_json, scan_params_json, notes
```

**orb_settlements** - 结算归档：

```
id, settled_at_utc, signal_id, symbol, side, play, outcome,
entry_price, exit_price, pnl_r, pnl_usdt, virtual_notional_usdt,
exit_rule, session_date
```

### 数据访问

不使用 ORM，全部通过原生 `sqlite3` 操作，Row factory 模式：

```python
conn = init_db()
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT * FROM orb_signals WHERE symbol = ?", (sym,))
rows = [dict(r) for r in cur.fetchall()]
```

---

## 工具脚本 (tools/)

### 顶层脚本

| 脚本 | 用途 |
|------|------|
| `binance_fapi.py` | 币安合约 API 封装 |
| `orb_scanner.py` | ORB 纸面扫描 CLI（定时任务入口） |
| `s2_oi_funding_rate_scanner.py` | 费率扫描 CLI（定时任务入口） |
| `orb_backtest.py` | ORB 回测入口 |
| `watchlist_symbols.py` | 标的列表管理 |
| `clean_accumulation_db.py` | accumulation.db 清理 |
| `train_breakout_gbm.py` | GBM 模型训练 |
| `eval_live_gate.py` | Live Gate 评估 |
| `collect_shared_breakout_samples.py` | 共享样本收集 |

### tools/orb/ 研究/训练目录

| 脚本 | 用途 |
|------|------|
| `bootstrap_live_bundle.py` | 初始化 orb_live/ 参数包 |

### tools/orb/v2/ - V2 专用工具

| 脚本 | 用途 |
|------|------|
| `backtest_universe.py` | 全量标的回测 |
| `backtest_symbol.py` | 单标回测 |
| `monthly_train.py` | 月度模型重训练 |
| `refresh_klines.py` | K 线数据刷新 |
| `tune_gate.py` | Gate 参数调优 |
| `verify_live_backtest_parity.py` | 实盘/回测一致性验证 |
| `export_trade_detail.py` | 交易明细导出 |
| `analyze_missed_attribution.py` | 漏单归因分析 |
| `analyze_true_breakout_capture.py` | 真实突破捕获率分析 |
| `print_daily_backtest_detail.py` | 每日回测详情 |
| `print_daily_true_breakouts.py` | 每日真实突破 |

### tools/orb/ml/ - ML 专用工具

| 脚本 | 用途 |
|------|------|
| `train_breakout_gbm.py` | GBM 模型训练 |
| `train_shared_breakout_model.py` | 共享模型训练 |
| `eval_live_gate.py` | Live Gate 评估 |
| `optimize_breakout_gbm.py` | GBM 超参优化 |
| `sweep_breakout_gbm.py` | GBM 参数扫描 |
| `sweep_live_gate_pnl.py` | Gate 参数扫描 (PNL视角) |
| `apply_gbm_sweep.py` | 应用 GBM 扫描结果 |
| `build_symbol_profiles.py` | 构建标的先验画像 |
| `collect_shared_breakout_samples.py` | 收集共享突破样本 |
| `analyze_true_breakout_features.py` | 真实突破特征分析 |
| `diagnose_rank_model.py` | 排序模型诊断 |
| `rank_day_breakouts.py` | 单日突破排序 |
| `rank_days_batch.py` | 批量突破排序 |
| `relabel_hold30_samples.py` | 30分钟持有重标注 |
| `sim_coin_filter_pnl.py` | 币种过滤模拟 |
| `sim_symbol_rank.py` | 标的排序模拟 |

---

## 工具库 (utils/)

### rate_limit.py -- MinIntervalGuard

线程安全的最小间隔限流器：

```python
guard = MinIntervalGuard("OI_RADAR_REFRESH_COOLDOWN_SEC", 120.0)
allowed, retry_after = guard.check_allow()
if allowed:
    guard.mark_used()
    # 执行操作
```

用于 OI 雷达刷新的 120 秒冷却保护。

### maintenance_auth.py

```python
async def require_maintenance_token() -> None:
    return None  # 当前鉴权已禁用
```

维护类路由的依赖注入，当前返回 None（鉴权已禁用），为未来接入 `PROTOCOL_MAINTENANCE_TOKEN` 预留。

### volume_export.py -- 数据卷导出

- `export_volume_enabled()`: 检查 `NEXT_K_EXPORT_VOLUME_ENABLED` 是否启用
- `resolve_data_dir()`: 解析 DATA_DIR 路径
- `summarize_data_dir()`: 统计文件数和总字节数
- `create_data_archive(fmt="zip")`: 打包为 zip/tar.gz
- `cleanup_export_paths()`: 清理临时文件

---

## 依赖

```
fastapi>=0.109.0          # Web 框架
uvicorn[standard]>=0.27.0  # ASGI 服务器
pydantic>=2.5.0           # 数据验证
numpy>=1.24.0             # 数值计算
pandas>=2.0.0             # 数据处理
requests>=2.31.0          # HTTP 客户端
apscheduler>=3.10.0       # 定时调度
pytz>=2023.3              # 时区处理
aiohttp>=3.9.0            # 异步 HTTP
tqdm>=4.65.0              # 进度条
scikit-learn>=1.3.0       # 机器学习 (LightGBM GBM)
```

---

## 部署

### Railway (NIXPACKS)

`railway.json`:

```json
{
  "build": {
    "builder": "NIXPACKS",
    "buildCommand": "python -c \"验证 orb_live bundle 存在\""
  },
  "deploy": {
    "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

部署前检查 `orb_live/` 目录下三个必需文件都存在：
- `live_gate.json`
- `breakout_gbm.pkl`
- `symbol_breakout_profiles.json`

### 本地部署 (start.sh / stop.sh)

**start.sh** 自动化流程:

1. 检查 Python 3.11+
2. 创建/复用 `.venv` 虚拟环境
3. 安装 requirements.txt 依赖
4. 准备 .env.oi（不存在时从 example 复制）
5. 读取关键参数（PORT、NEXT_K_EMBED_SCHEDULER）
6. 创建 `.pid/` 和 `logs/` 目录
7. 启动 uvicorn API 进程（nohup 后台）
8. 等待 API 就绪（最多 30s）
9. 单进程模式: 调度器随 API 启动；双进程模式: 启动独立 `scheduler_main.py`
10. 打印启动摘要

**stop.sh** 优雅关闭:

1. 先停调度器进程（SIGTERM, 15s 超时后 SIGKILL）
2. 再停 API 进程（SIGTERM, 15s 超时后 SIGKILL）
3. 清理 PID 文件

```bash
# 常用命令
./start.sh                                    # 默认启动（内嵌调度器）
NEXT_K_EMBED_SCHEDULER=0 ./start.sh           # 双进程模式
PORT=9000 ./start.sh                           # 自定义端口
./stop.sh                                      # 停止全部
```

---

## 目录结构速览

```
next-k-api/
  main.py                    # FastAPI 应用入口
  app_state.py               # 全局 AppState 单例
  env_loader.py              # .env.oi 加载器
  scheduler_config.py        # 定时任务注册（APScheduler）
  scheduler_main.py          # 独立调度器进程入口
  worker_tasks.py            # 定时任务执行层（子进程模式）
  accumulation_radar.py      # 收筹池 + OI 雷达核心
  s2_oi_funding_rate_scanner.py  # 费率转负扫描
  orb_scanner.py             # ORB 扫描 CLI
  start.sh / stop.sh         # 启停脚本
  railway.json               # Railway 部署配置
  requirements.txt           # PyPI 依赖
  .env.oi.example            # 环境变量模板

  routers/
    core.py                  # /, /api/health
    accumulation.py          # /api/accumulation/*
    orb.py                   # /api/orb/*
    s2.py                    # /api/s2/*
    maintenance.py           # /api/export-volume/*

  models/
    api_models.py            # Pydantic 请求/响应模型

  orb/                       # ORB 策略完整模块
    core/                    # 核心逻辑 (config, breakout, signals, paper, resolve, db, ...)
    v2/                      # V2.0 ML Gate (config, paper, db, robots, gate_state, paths)
    ml/                      # 机器学习 (features, gbm, ranker, gate, profiles, samples, ...)
      model/                 # 模型训练/验证/自动配置

  orb_live/                  # 实盘参数包（Git 部署）
    live_gate.json           # Gate 参数
    breakout_gbm.pkl         # GBM 模型
    symbol_breakout_profiles.json  # 标的先验画像

  plugins/                   # 信号源插件
    accumulation/ oi_funding/ orb/

  tools/                     # CLI 工具脚本
    orb/                     # ORB 研究/训练工具
      v2/                    # V2 专用 (回测、Gate调优、月度训练、K线刷新)
      ml/                    # ML 专用 (模型训练、优化、评估、画像)

  utils/
    rate_limit.py            # MinIntervalGuard 限流器
    maintenance_auth.py      # 维护鉴权（已禁用）
    volume_export.py         # 数据卷打包导出

  tests/                     # 测试目录
  logs/                      # 运行日志（gitignore）
  .pid/                      # 进程 PID 文件（gitignore）
```

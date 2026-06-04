# Moss2 回测 CSV（线上自动，无需 skills 目录）

## 默认行为（已内置）

- **目录**：`next-k-api/data/moss2_en_data_cache/`（或 `DATA_DIR/moss2_en_data_cache`）
- **不依赖** `moss-trade-bot-skills-main`
- **拉取前自动清理**（`MOSS2_DATA_BOOTSTRAP_CLEAN_BEFORE_FETCH`）：删掉旧命名/重复 CSV；`force=true` 时清空 25 核心再拉
- **启动后约 90 秒（UTC）**：仅在「有缺失 seed CSV」或「从未成功 bootstrap」时拉取；已齐则跳过
- **K 线窗**：`MOSS2_FETCH_SINCE_ROLLING=True`，拉取**最近 90 天至当前**（覆盖进化窗 4500 根 + 预热）；文件名 `..._15m_90d.csv`
- **每周日 04:00（调度器 Asia/Shanghai）**：刷新超过 24h 的 stale CSV（未过期则 skipped）
- **启动后约 5 分钟 / 每周日 04:45 UTC**：全自动 Profile（25 核心 suggest→创建→进化→启用）
- **实盘 15m 扫描**：仍用币安实时 K 线，不等待 CSV

覆盖路径：`moss2/config.py` → `MOSS2_DATA_BOOTSTRAP_*`

## 手动触发

```http
POST /api/moss2/maintenance/bootstrap-data?force=false
X-Maintenance-Token: <PROTOCOL_MAINTENANCE_TOKEN>
```

`force=true` 强制重拉全部（会先清理再拉）。

## 什么时候会拉 CSV（25 核心）

| 时机 | 触发 | 说明 |
|------|------|------|
| 启动 +90s | 调度 `startup` | 缺文件或首次；**先 cleanup 再拉** |
| 每周日 04:00 | 调度 `weekly`（上海时区） | 仅拉 stale&gt;24h 的币；**先 cleanup 去重** |
| 手动 | `POST .../bootstrap-data?force=` | 维护 Token |
| 纸面 15m | **不拉 CSV** | 用币安实时 K 线 |

同日上午：**04:45** auto-provision、**05:00** evolve、**06:30** cull（不拉 CSV，用已有缓存回测）。

## 全自动 Profile

```http
POST /api/moss2/maintenance/auto-provision?force_evolve=false
X-Maintenance-Token: <PROTOCOL_MAINTENANCE_TOKEN>
```

开关：`moss2/config.py` → `MOSS2_AUTO_PROVISION_*`、`MOSS2_EVOLVE_AUTO_APPROVE`、`MOSS2_AUTO_ENABLE_PROFILES`。

## 本地开发（可选读旧 skills 目录）

若本机有 `moss-trade-bot-skills-main` 且想沿用其中 CSV：

```env
MOSS2_PREFER_SKILLS_DATA_CACHE=1
```

## 自定义目录

```env
MOSS2_EN_DATA_CACHE=/data/moss2_csv
```

## 离线脚本（可选）

仍可使用 `scripts/fetch_factory_en_moss_universe.ps1`；线上一般不需要。

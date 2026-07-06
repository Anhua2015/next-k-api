# King Keltner — 已停用

KK vnpy 车道已由 **Trading ORB** 接管。请勿再部署 KK。

## 当前推荐

见 `config/trading_orb/RAILWAY.md` 与 `.env.oi.example`：

```env
ORB_VNPY_ENABLED=1
ORB_VNPY_AUTO_START=1
KK_ENABLED=0
KK_SCHEDULER_ENABLED=0
```

## 回滚 KK（仅应急）

在 Variables 中设 `KK_ENABLED=1`、`ORB_VNPY_ENABLED=0` 后重新部署。同一 replica 不要同时启用两条 vnpy lane。

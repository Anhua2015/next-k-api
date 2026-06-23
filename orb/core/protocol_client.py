"""Next-k-protocol 的最小同步 HTTP 客户端。

策略扫描运行在子进程中，因此这里使用简单的 ``requests`` 同步调用即可。请求失败会
抛给 ORB 编排层，由编排层统一回滚纸面状态，不能在此处吞掉异常后假装实盘成功。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 30.0
SOURCE_ORB = "orb"


def protocol_api_url() -> str:
    return (os.getenv("PROTOCOL_API_URL") or "http://localhost:8001").strip().rstrip("/")


def protocol_configured() -> bool:
    return bool(protocol_api_url())


def _protocol_headers() -> Dict[str, str]:
    """构造服务间鉴权头；未配置令牌时保持兼容开放模式。"""
    headers = {"Content-Type": "application/json"}
    token = os.getenv("PROTOCOL_MAINTENANCE_TOKEN", "").strip()
    if token:
        headers["X-Maintenance-Token"] = token
    return headers


def ingest_signals(signals: List[Dict[str, Any]], *, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> Dict[str, Any]:
    """批量推送信号并返回 Protocol 原始汇总结果。

    ``api_signal_id`` 的幂等性由 Protocol 数据库保证，因此调用方可以在网络层重试；
    本函数本身不自动重试，以免长时间占用扫描进程并让错误时序变得不透明。
    """
    if not signals:
        return {"scanned": 0, "traded": 0, "skipped": 0, "errors": 0, "details": []}
    url = f"{protocol_api_url()}/api/binance/signals/ingest"
    resp = requests.post(
        url,
        json={"signals": signals},
        headers=_protocol_headers(),
        timeout=timeout_sec,
    )
    if resp.status_code >= 400:
        body = resp.text[:500]
        raise RuntimeError(f"protocol ingest HTTP {resp.status_code}: {body}")
    data = resp.json()
    logger.info(
        "[orb] protocol ingest: scanned=%s traded=%s skipped=%s errors=%s",
        data.get("scanned"),
        data.get("traded"),
        data.get("skipped"),
        data.get("errors"),
    )
    return data

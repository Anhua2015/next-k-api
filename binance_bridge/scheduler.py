"""Background job registration for the Binance live-trading bridge.

Jobs registered when BINANCE_ENABLED=1:
  - binance_sync_positions:   every 30 s — detect SL/TP triggers, update DB.
  - binance_expire_positions: every 5 min — force-close positions past expire_at.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("binance_bridge.scheduler")


def register_binance_jobs(sch: Any) -> None:
    """Add Binance bridge jobs to an APScheduler BackgroundScheduler instance."""
    from binance_bridge.trader import expire_open_positions, sync_open_positions

    sch.add_job(
        sync_open_positions,
        "interval",
        seconds=30,
        id="binance_sync_positions",
        max_instances=1,
        replace_existing=True,
    )
    sch.add_job(
        expire_open_positions,
        "interval",
        minutes=5,
        id="binance_expire_positions",
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Binance bridge jobs registered (sync=30s, expire=5min)")

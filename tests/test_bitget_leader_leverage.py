"""Leader leverage must be applied on Bitget opens (not account default)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import utils.hl_bitget_executor as ex
from utils.hl_paper_copy import _lev_for_coin, paper_config


def test_lev_for_coin_prefers_target_map_not_bitget_echo():
    bot = {
        "id": "bot_c",
        "target_lev_by_coin": {"BTC": 20.0},
        "positions": {"bot_c:BTC": {"coin": "BTC", "sz": -0.05, "leverage": 23.0}},
    }
    assert _lev_for_coin(bot, "BTC", paper_config()) == 20


def test_lev_for_coin_uses_target_positions_when_map_missing():
    bot = {
        "id": "bot_c",
        "target_positions": {"BTC": {"sz": -15.5, "leverage": 20.0}},
        "positions": {"bot_c:BTC": {"coin": "BTC", "sz": -0.05, "leverage": 23.0}},
    }
    assert _lev_for_coin(bot, "BTC", paper_config()) == 20


def test_leader_leverage_hip3_not_clamped_by_paper_cap():
    """Paper default asset cap is 10; live Bitget must still follow leader 20x."""
    bot = {
        "id": "bot_c",
        "target_positions": {"XYZ:GOOGL": {"sz": 2808.0, "leverage": 20.0}},
    }
    # Paper path still caps non-majors at 10.
    assert _lev_for_coin(bot, "GOOGL", paper_config()) == 10
    with patch.object(ex, "_load_bot", return_value=bot):
        assert ex.leader_leverage_for_symbol("bot_c", "GOOGLUSDT") == 20


def test_place_one_passes_leverage_on_open(monkeypatch):
    monkeypatch.setenv("HL_BITGET_LIVE", "1")
    monkeypatch.setenv("HL_BITGET_DRY_RUN", "0")
    placed = {}

    def _fake_place(**kwargs):
        placed.update(kwargs)
        return {"orderId": "1"}

    with (
        patch.object(ex, "live_ready", return_value=(True, "")),
        patch.object(ex, "dry_run", return_value=False),
        patch.object(ex, "live_enabled", return_value=True),
        patch.object(ex, "_ensure_one_way_once"),
        patch.object(ex, "_append_ledger"),
        patch(
            "quant.engine.exchanges.bitget.account.place_market_order",
            side_effect=_fake_place,
        ),
    ):
        out = ex._place_one(
            symbol="BTCUSDT",
            side="sell",
            size=0.01,
            client_oid="testoid123",
            reduce_only=False,
            meta={"action": "sub_sync"},
            account_id="main",
            leverage=20,
        )
    assert out["status"] == "sent"
    assert placed.get("leverage") == 20


def test_sync_idle_does_not_retouch_existing_leverage(monkeypatch):
    monkeypatch.setenv("HL_BITGET_LIVE", "1")
    monkeypatch.setenv("HL_BITGET_DRY_RUN", "0")
    set_calls: list[tuple[str, int]] = []

    with (
        patch.object(ex, "dry_run", return_value=False),
        patch.object(ex, "live_enabled", return_value=True),
        patch.object(ex, "log_skips", return_value=False),
        patch.object(ex, "leader_leverage_for_symbol", return_value=20),
        patch(
            "quant.engine.exchanges.bitget.account.fetch_signed_position",
            return_value=-0.05,
        ),
        patch(
            "quant.engine.exchanges.bitget.account.set_symbol_leverage",
            side_effect=lambda sym, lev: set_calls.append((sym, lev)),
        ),
    ):
        out = ex.sync_account_symbol(
            "BTCUSDT",
            -0.05,
            account_id="main",
            bot_id="bot_c",
            mode_tag="sub_sync",
        )
    assert out[0]["status"] == "synced"
    assert set_calls == []


def test_sync_open_from_flat_passes_leader_leverage(monkeypatch):
    monkeypatch.setenv("HL_BITGET_LIVE", "1")
    monkeypatch.setenv("HL_BITGET_DRY_RUN", "0")
    placed = {}

    def _fake_place(**kwargs):
        placed.update(kwargs)
        return {"orderId": "1"}

    with (
        patch.object(ex, "dry_run", return_value=False),
        patch.object(ex, "live_enabled", return_value=True),
        patch.object(ex, "live_ready", return_value=(True, "")),
        patch.object(ex, "_ensure_one_way_once"),
        patch.object(ex, "_append_ledger"),
        patch.object(ex, "leader_leverage_for_symbol", return_value=20),
        patch(
            "quant.engine.exchanges.bitget.account.fetch_signed_position",
            return_value=0.0,
        ),
        patch(
            "quant.engine.exchanges.bitget.account.place_market_order",
            side_effect=_fake_place,
        ),
    ):
        out = ex.sync_account_symbol(
            "ETHUSDT",
            -0.1,
            account_id="main",
            bot_id="bot_c",
            mode_tag="sub_sync",
        )
    assert out[0]["status"] == "sent"
    assert placed.get("leverage") == 20


def test_sync_size_up_keeps_existing_symbol_leverage(monkeypatch):
    monkeypatch.setenv("HL_BITGET_LIVE", "1")
    monkeypatch.setenv("HL_BITGET_DRY_RUN", "0")
    placed = {}

    def _fake_place(**kwargs):
        placed.update(kwargs)
        return {"orderId": "1"}

    with (
        patch.object(ex, "dry_run", return_value=False),
        patch.object(ex, "live_enabled", return_value=True),
        patch.object(ex, "live_ready", return_value=(True, "")),
        patch.object(ex, "_ensure_one_way_once"),
        patch.object(ex, "_append_ledger"),
        patch.object(ex, "leader_leverage_for_symbol", return_value=20),
        patch(
            "quant.engine.exchanges.bitget.account.fetch_signed_position",
            return_value=-0.05,
        ),
        patch(
            "quant.engine.exchanges.bitget.account.place_market_order",
            side_effect=_fake_place,
        ),
    ):
        out = ex.sync_account_symbol(
            "BTCUSDT",
            -0.08,
            account_id="main",
            bot_id="bot_c",
            mode_tag="sub_sync",
        )
    assert out[0]["status"] == "sent"
    assert placed.get("leverage") is None


def test_place_market_order_raises_when_set_leverage_fails():
    from quant.engine.exchanges.bitget import account as bg

    with (
        patch.object(bg, "get_order_by_client_oid", return_value=None),
        patch.object(
            bg,
            "set_symbol_leverage",
            side_effect=RuntimeError("Bitget set-leverage rejected"),
        ),
    ):
        try:
            bg.place_market_order(
                symbol="BTCUSDT",
                side="sell",
                size=0.01,
                client_oid="abc123",
                reduce_only=False,
                leverage=20,
            )
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "leverage" in str(exc).lower() or "rejected" in str(exc).lower()
    assert raised

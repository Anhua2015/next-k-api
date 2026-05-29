from __future__ import annotations

import pytest


def test_live_notional_uses_protocol_balance_and_leverage():
    from moss_quant.paper_scanner import live_notional_from_account

    params = {"risk_per_trade": 0.10, "max_position_pct": 0.50}
    notional = live_notional_from_account(
        wallet_balance_usdt=1000,
        enabled_profile_count=5,
        protocol_leverage=8,
        params=params,
    )

    assert notional == 160.0


def test_live_notional_rejects_invalid_inputs():
    from moss_quant.paper_scanner import live_notional_from_account

    with pytest.raises(ValueError, match="enabled_profile_count"):
        live_notional_from_account(
            wallet_balance_usdt=1000,
            enabled_profile_count=0,
            protocol_leverage=8,
            params={"risk_per_trade": 0.1, "max_position_pct": 0.5},
        )


def test_protocol_client_builds_headers(monkeypatch):
    monkeypatch.setenv("PROTOCOL_API_URL", "http://protocol.test")
    monkeypatch.setenv("PROTOCOL_MAINTENANCE_TOKEN", "secret")

    from moss_quant.protocol_client import ProtocolClient

    c = ProtocolClient.from_env()
    assert c.base_url == "http://protocol.test"
    assert c.headers()["X-Maintenance-Token"] == "secret"

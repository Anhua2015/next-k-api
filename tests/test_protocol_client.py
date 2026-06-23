from __future__ import annotations

from orb.core import protocol_client


def test_protocol_headers_include_configured_token(monkeypatch):
    monkeypatch.setenv("PROTOCOL_MAINTENANCE_TOKEN", "secret-token")

    assert protocol_client._protocol_headers() == {
        "Content-Type": "application/json",
        "X-Maintenance-Token": "secret-token",
    }


def test_protocol_headers_omit_empty_token(monkeypatch):
    monkeypatch.delenv("PROTOCOL_MAINTENANCE_TOKEN", raising=False)

    assert protocol_client._protocol_headers() == {
        "Content-Type": "application/json",
    }

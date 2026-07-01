"""ProtocolGateway 集成测试（mock ingest / lookup）。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from orb.kk.config import KKConfig
from orb.kk.vnpy.bootstrap import ensure_vnpy_path

ensure_vnpy_path()

from vnpy.event import EventEngine  # noqa: E402
from vnpy.trader.constant import Direction, Exchange, Offset, OrderType, Status  # noqa: E402
from vnpy.trader.object import OrderRequest  # noqa: E402

from orb.kk.vnpy.protocol_gateway import ProtocolGateway  # noqa: E402


def _gateway(kk: KKConfig) -> ProtocolGateway:
    gw = ProtocolGateway(EventEngine())
    gw.kk = kk
    gw.orb_cfg = kk.orb_session_cfg()
    gw._poll_interval = 0.01
    return gw


def _prepare_order(
    gw: ProtocolGateway,
    *,
    offset: Offset,
    direction: Direction,
    symbol: str = "COINUSDT",
    price: float = 250.0,
    volume: float = 1.0,
) -> tuple[str, OrderRequest]:
    req = OrderRequest(
        symbol=symbol,
        exchange=Exchange.GLOBAL,
        direction=direction,
        type=OrderType.LIMIT,
        volume=volume,
        price=price,
        offset=offset,
    )
    order = req.create_order_data("t1", gw.gateway_name)
    order.status = Status.SUBMITTING
    vt_id = order.vt_orderid
    gw._orders[vt_id] = order
    return vt_id, req


class TestProtocolGateway(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            "PROTOCOL_API_URL": "http://127.0.0.1:8001",
            "KK_LIVE_ENABLED": "1",
        }
        self._env_patch = mock.patch.dict(os.environ, self._env, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_shadow_rejects_open(self):
        kk = KKConfig(live_enabled=True, shadow=True, symbols=["COINUSDT"])
        gw = _gateway(kk)
        vt_id, req = _prepare_order(gw, offset=Offset.OPEN, direction=Direction.LONG)
        gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.REJECTED)

    def test_live_disabled_rejects_open(self):
        kk = KKConfig(live_enabled=False, shadow=False, symbols=["COINUSDT"])
        gw = _gateway(kk)
        vt_id, req = _prepare_order(gw, offset=Offset.OPEN, direction=Direction.LONG)
        gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.REJECTED)

    def test_max_open_positions_rejects(self):
        kk = KKConfig(
            live_enabled=True,
            shadow=False,
            max_open_positions=2,
            symbols=["COINUSDT"],
        )
        gw = _gateway(kk)
        with mock.patch.object(gw, "_open_position_count", return_value=2):
            vt_id, req = _prepare_order(gw, offset=Offset.OPEN, direction=Direction.LONG)
            gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.REJECTED)

    @mock.patch("orb.kk.vnpy.protocol_gateway.lookup_signal")
    @mock.patch("orb.kk.vnpy.protocol_gateway.ingest_signals")
    def test_open_success_waits_for_traded(self, mock_ingest, mock_lookup):
        mock_ingest.return_value = {"traded": 1, "details": [{"action": "traded"}]}
        mock_lookup.return_value = {"status": "traded", "entry_price": 251.0}
        kk = KKConfig(
            live_enabled=True,
            shadow=False,
            live_leverage=5.0,
            max_open_positions=0,
            symbols=["COINUSDT"],
        )
        gw = _gateway(kk)
        with mock.patch.object(gw, "_session_date", return_value="2026-06-23"):
            vt_id, req = _prepare_order(gw, offset=Offset.OPEN, direction=Direction.LONG)
            gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.ALLTRADED)
        mock_ingest.assert_called_once()
        payload = mock_ingest.call_args[0][0][0]
        self.assertEqual(payload["source"], "kk")
        self.assertEqual(payload["action"], "open")
        self.assertEqual(payload["entry_type"], "MARKET")
        self.assertIsNotNone(payload.get("sl_price"))

    @mock.patch("orb.kk.vnpy.protocol_gateway.lookup_signal")
    @mock.patch("orb.kk.vnpy.protocol_gateway.ingest_signals")
    def test_ingest_failure_rejects(self, mock_ingest, mock_lookup):
        mock_ingest.return_value = {"errors": 1, "details": [{"action": "error"}]}
        kk = KKConfig(live_enabled=True, shadow=False, symbols=["COINUSDT"])
        gw = _gateway(kk)
        with mock.patch.object(gw, "_session_date", return_value="2026-06-23"):
            vt_id, req = _prepare_order(gw, offset=Offset.OPEN, direction=Direction.LONG)
            gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.REJECTED)
        mock_lookup.assert_not_called()

    @mock.patch("orb.kk.vnpy.protocol_gateway.lookup_signal")
    @mock.patch("orb.kk.vnpy.protocol_gateway.ingest_signals")
    def test_fill_timeout_rejects(self, mock_ingest, mock_lookup):
        mock_ingest.return_value = {"traded": 1, "details": [{"action": "submitted"}]}
        mock_lookup.return_value = {"status": "submitted"}
        kk = KKConfig(live_enabled=True, shadow=False, symbols=["COINUSDT"])
        gw = _gateway(kk)
        with mock.patch.object(gw, "_session_date", return_value="2026-06-23"):
            vt_id, req = _prepare_order(gw, offset=Offset.OPEN, direction=Direction.LONG)
            with mock.patch.object(gw, "_wait_fill", return_value=None):
                gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.REJECTED)

    @mock.patch("orb.kk.vnpy.protocol_gateway.lookup_signal")
    @mock.patch("orb.kk.vnpy.protocol_gateway.ingest_signals")
    def test_close_uses_market_without_close_price(self, mock_ingest, mock_lookup):
        mock_ingest.return_value = {"traded": 1, "details": [{"action": "traded"}]}
        mock_lookup.return_value = {"status": "traded", "close_price": 249.0}
        kk = KKConfig(live_enabled=True, shadow=False, symbols=["COINUSDT"])
        gw = _gateway(kk)
        with mock.patch.object(gw, "_session_date", return_value="2026-06-23"):
            vt_id, req = _prepare_order(gw, offset=Offset.CLOSE, direction=Direction.SHORT)
            gw._execute_order(vt_id, req)
        self.assertEqual(gw._orders[vt_id].status, Status.ALLTRADED)
        payload = mock_ingest.call_args[0][0][0]
        self.assertEqual(payload["action"], "close")
        self.assertNotIn("close_price", payload)
        self.assertIn("close", payload["api_signal_id"])

    def test_open_position_count_from_protocol(self):
        kk = KKConfig(symbols=["COINUSDT"])
        gw = _gateway(kk)
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"symbol": "COINUSDT", "quantity": 1.0},
            {"symbol": "TSLAUSDT", "quantity": 0.0},
        ]
        with mock.patch("requests.get", return_value=mock_resp):
            self.assertEqual(gw._open_position_count(), 1)

    def test_open_position_count_ignores_outside_pool(self):
        kk = KKConfig(symbols=["COINUSDT"])
        gw = _gateway(kk)
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"symbol": "COINUSDT", "quantity": 1.0},
            {"symbol": "TSLAUSDT", "quantity": 2.0},
        ]
        with mock.patch("requests.get", return_value=mock_resp):
            self.assertEqual(gw._open_position_count(), 1)


if __name__ == "__main__":
    unittest.main()

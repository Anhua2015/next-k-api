"""币安实盘视图 API 辅助函数测试。"""

from __future__ import annotations

import unittest
from unittest import mock

from orb.vnpy import binance_account as ba


class TestBinanceAccountHelpers(unittest.TestCase):
    def test_normalize_open_order_limit(self):
        row = {
            "orderId": 99,
            "symbol": "btcusdt",
            "side": "BUY",
            "type": "LIMIT",
            "price": "100",
            "origQty": "0.01",
            "executedQty": "0",
            "status": "NEW",
            "reduceOnly": False,
            "time": 1700000000000,
        }
        out = ba._normalize_open_order(row, kind="limit")
        self.assertEqual(out["symbol"], "BTCUSDT")
        self.assertEqual(out["order_id"], "99")
        self.assertEqual(out["status_label"], "待成交")

    @mock.patch("orb.vnpy.binance_account._signed_get")
    def test_fetch_open_orders_merges_limit_and_algo(self, mock_get):
        mock_get.side_effect = [
            [{"orderId": 1, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "price": "1", "origQty": "1", "executedQty": "0", "status": "NEW", "time": 2}],
            [{"algoId": 9, "symbol": "ETHUSDT", "side": "SELL", "orderType": "STOP_MARKET", "triggerPrice": "2000", "quantity": "0.1", "algoStatus": "NEW", "createTime": 3}],
        ]
        rows = ba.fetch_open_orders(limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "ETHUSDT")
        self.assertEqual(rows[0]["kind"], "algo")

    @mock.patch("orb.vnpy.binance_account._signed_get")
    def test_fetch_realized_pnl_history(self, mock_get):
        mock_get.return_value = [
            {
                "symbol": "BTCUSDT",
                "incomeType": "REALIZED_PNL",
                "income": "-1.5",
                "asset": "USDT",
                "time": 1700000000000,
                "tradeId": "123",
                "tranId": 456,
                "info": "CLOSE",
            }
        ]
        rows = ba.fetch_realized_pnl_history(days=7, limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BTCUSDT")
        self.assertEqual(rows[0]["pnl_usdt"], -1.5)


if __name__ == "__main__":
    unittest.main()

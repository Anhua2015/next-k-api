# -*- coding: utf-8 -*-
"""Bitget order-detail 40109 must not block place_market_order."""

from __future__ import annotations

import unittest

from quant.engine.exchanges.bitget.account import is_order_not_found_error


class BitgetOrderNotFoundTests(unittest.TestCase):
    def test_40109_cannot_be_found_from_log(self):
        err = RuntimeError(
            "Bitget /api/v2/mix/order/detail HTTP 400: "
            "{'code': '40109', 'msg': 'The data of the order cannot be found, "
            "please confirm the order number', 'requestTime': 1785802885459, 'data': None}"
        )
        self.assertTrue(is_order_not_found_error(err))

    def test_not_found_wording(self):
        self.assertTrue(is_order_not_found_error("order not found"))
        self.assertTrue(is_order_not_found_error("Bitget path code=40109 missing"))

    def test_unrelated_errors_still_raise(self):
        self.assertFalse(is_order_not_found_error("Bitget HTTP 400: {'code': '40012'}"))
        self.assertFalse(is_order_not_found_error("invalid ip"))
        # Broad "order id" alone must NOT match (old buggy keyword).
        self.assertFalse(is_order_not_found_error("invalid order id format"))


if __name__ == "__main__":
    unittest.main()

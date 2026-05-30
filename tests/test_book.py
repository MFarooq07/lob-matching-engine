"""
tests/test_book.py — Unit tests for the Limit Order Book matching engine.

Covers:
  - Simple limit order resting (no cross)
  - Full fill (aggressor exactly matches passive volume)
  - Partial fill (aggressor < passive)
  - Over-fill / sweep through multiple price levels
  - Market order execution
  - Cancel-then-cross (cancelled order must not be matched)
  - Price-time priority (earlier order fills first at same price)
  - Bid/ask depth snapshot accuracy
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from engine.book   import OrderBook
from engine.models import LimitOrder, MarketOrder, OrderStatus, Side


def _limit(oid: int, side: Side, price: float, vol: int) -> LimitOrder:
    """Shorthand factory for LimitOrder."""
    return LimitOrder(order_id=oid, side=side, price=price, volume=vol)


class TestOrderResting(unittest.TestCase):
    """Orders that don't cross should rest in the book."""

    def test_bid_rests_when_no_ask(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 10))
        self.assertEqual(book.best_bid(), 100.0)
        self.assertIsNone(book.best_ask())

    def test_ask_rests_when_no_bid(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.ASK, 101.0, 5))
        self.assertIsNone(book.best_bid())
        self.assertEqual(book.best_ask(), 101.0)

    def test_no_cross_when_spread_exists(self):
        book = OrderBook()
        trades = book.add_limit_order(_limit(1, Side.BID, 100.0, 10))
        trades += book.add_limit_order(_limit(2, Side.ASK, 100.05, 5))
        self.assertEqual(trades, [])
        self.assertEqual(book.best_bid(), 100.0)
        self.assertEqual(book.best_ask(), 100.05)


class TestFullFill(unittest.TestCase):
    """Aggressor exactly consumes one resting order."""

    def test_ask_fully_fills_resting_bid(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.50, 10))
        trades = book.add_limit_order(_limit(2, Side.ASK, 100.50, 10))

        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.volume, 10)
        self.assertEqual(t.price,  100.50)
        self.assertEqual(t.passive_id, 1)
        self.assertEqual(t.aggressor_id, 2)

        # Book should be empty on both sides
        self.assertIsNone(book.best_bid())
        self.assertIsNone(book.best_ask())

    def test_bid_fully_fills_resting_ask(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.ASK, 99.95, 7))
        trades = book.add_limit_order(_limit(2, Side.BID, 99.95, 7))

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].volume, 7)
        self.assertIsNone(book.best_ask())
        self.assertIsNone(book.best_bid())


class TestPartialFill(unittest.TestCase):
    """Aggressor smaller than passive; passive remains with reduced volume."""

    def test_partial_fill_leaves_passive_resting(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 10))   # passive 10
        trades = book.add_limit_order(_limit(2, Side.ASK, 100.0, 3))  # aggressor 3

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].volume, 3)

        bids, _ = book.depth_snapshot(1)
        self.assertEqual(bids[0], (100.0, 7))   # 7 remaining

    def test_partial_fill_order_status(self):
        book = OrderBook()
        passive = _limit(1, Side.BID, 100.0, 10)
        book.add_limit_order(passive)

        aggressor = _limit(2, Side.ASK, 100.0, 4)
        book.add_limit_order(aggressor)

        self.assertEqual(passive.remaining, 6)
        self.assertEqual(passive.status,    OrderStatus.PARTIAL)
        self.assertEqual(aggressor.remaining, 0)
        self.assertEqual(aggressor.status,  OrderStatus.FILLED)


class TestSweep(unittest.TestCase):
    """Aggressor sweeps through multiple price levels."""

    def test_sweep_two_ask_levels(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.ASK, 100.0, 5))
        book.add_limit_order(_limit(2, Side.ASK, 100.5, 5))

        # Large aggressive bid
        trades = book.add_limit_order(_limit(3, Side.BID, 101.0, 10))

        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].price, 100.0)
        self.assertEqual(trades[0].volume, 5)
        self.assertEqual(trades[1].price, 100.5)
        self.assertEqual(trades[1].volume, 5)

        self.assertIsNone(book.best_ask())   # both levels consumed


class TestMarketOrder(unittest.TestCase):
    """Market orders sweep at any available price."""

    def test_market_sell_against_bids(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.50, 10))
        book.add_limit_order(_limit(2, Side.BID, 100.00, 10))

        mo = MarketOrder(order_id=99, side=Side.ASK, volume=3)
        trades = book.add_market_order(mo)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].price,  100.50)  # best bid hits first
        self.assertEqual(trades[0].volume, 3)

        bids, _ = book.depth_snapshot(2)
        self.assertEqual(bids[0][1], 7)   # 10-3 = 7 remaining at top

    def test_market_buy_exhaust_ask(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.ASK, 50.0, 5))
        mo = MarketOrder(order_id=99, side=Side.BID, volume=5)
        trades = book.add_market_order(mo)

        self.assertEqual(trades[0].volume, 5)
        self.assertIsNone(book.best_ask())


class TestCancel(unittest.TestCase):
    """Cancelled orders must not participate in future matching."""

    def test_cancelled_order_not_matched(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 10))

        cancelled = book.cancel_order(1)
        self.assertTrue(cancelled)

        # Now send a matching ask — should produce NO trades
        trades = book.add_limit_order(_limit(2, Side.ASK, 100.0, 10))
        self.assertEqual(trades, [])

    def test_cancel_nonexistent_returns_false(self):
        book = OrderBook()
        self.assertFalse(book.cancel_order(9999))

    def test_cancel_already_filled_returns_false(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 5))
        book.add_limit_order(_limit(2, Side.ASK, 100.0, 5))   # fills order 1
        self.assertFalse(book.cancel_order(1))


class TestPriceTimePriority(unittest.TestCase):
    """Earlier orders at the same price must fill before later ones."""

    def test_fifo_at_same_price(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 5))   # earlier
        book.add_limit_order(_limit(2, Side.BID, 100.0, 5))   # later

        # Aggressor that only fills 5 shares
        trades = book.add_limit_order(_limit(3, Side.ASK, 100.0, 5))

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].passive_id, 1)   # order 1 filled first


class TestDepthSnapshot(unittest.TestCase):
    """Depth snapshot aggregates volume correctly."""

    def test_depth_aggregation(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 10))
        book.add_limit_order(_limit(2, Side.BID, 100.0, 5))   # same level → 15
        book.add_limit_order(_limit(3, Side.BID, 99.5,  8))
        book.add_limit_order(_limit(4, Side.ASK, 101.0, 12))

        bids, asks = book.depth_snapshot(5)

        self.assertEqual(bids[0], (100.0, 15))
        self.assertEqual(bids[1], (99.5,  8))
        self.assertEqual(asks[0], (101.0, 12))

    def test_depth_excludes_cancelled(self):
        book = OrderBook()
        book.add_limit_order(_limit(1, Side.BID, 100.0, 10))
        book.add_limit_order(_limit(2, Side.BID, 100.0, 5))
        book.cancel_order(1)

        bids, _ = book.depth_snapshot(5)
        self.assertEqual(bids[0][1], 5)  # only order 2 remains


class TestExampleFromSpec(unittest.TestCase):
    """Reproduce the exact example from the project specification."""

    def test_spec_example(self):
        book = OrderBook()

        # Step 1: resting bid
        book.add_limit_order(LimitOrder(order_id=1, side=Side.BID, price=100.50, volume=10))

        # Step 2: resting ask (no cross — spread exists)
        book.add_limit_order(LimitOrder(order_id=2, side=Side.ASK, price=100.55, volume=5))

        # Step 3: market sell order — hits best bid
        mo = MarketOrder(order_id=3, side=Side.ASK, volume=3)
        trades = book.add_market_order(mo)

        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.volume, 3)
        self.assertEqual(t.price,  100.50)

        bids, asks = book.depth_snapshot(1)
        self.assertEqual(bids[0], (100.50, 7))   # 10 - 3 = 7
        self.assertEqual(asks[0], (100.55, 5))   # untouched


if __name__ == "__main__":
    unittest.main(verbosity=2)
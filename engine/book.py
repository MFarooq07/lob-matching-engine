"""
book.py — High-performance, in-memory Limit Order Book (LOB) with matching engine.

Data-structure design
---------------------
Each side of the book uses TWO complementary structures in lockstep:

  1. ``_price_levels`` : dict[float, deque[LimitOrder]]
       Maps a price level to a FIFO queue of orders resting at that price.
       Gives O(1) access to the queue for any known price.

  2. ``_sorted_prices`` : list[float]  (maintained via bisect)
       A sorted list of all *active* price levels.
       Gives O(log N) best-price lookup and insertion.

  3. ``_order_map`` : dict[int, LimitOrder]
       Maps order_id → LimitOrder for O(1) cancel / update without scanning.

The combination means:
  - Insert new order : O(log N)  — bisect insert into sorted price list
  - Cancel by id    : O(1) lookup + O(1) deque remove (mark-and-sweep lazily)
  - Best price peek : O(1)  — sorted_prices[0] or [-1]
  - Match market    : O(k log N) where k = number of price levels crossed
"""

from __future__ import annotations

import heapq
from bisect import insort, bisect_left
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .models import LimitOrder, MarketOrder, OrderStatus, Side, Trade


class OrderBook:
    """
    Dual-sided limit order book with price-time priority matching.

    Public API
    ----------
    add_limit_order(order)  → list[Trade]
    add_market_order(order) → list[Trade]
    cancel_order(order_id)  → bool
    best_bid()              → Optional[float]
    best_ask()              → Optional[float]
    depth_snapshot(levels)  → tuple[list, list]
    """

    def __init__(self) -> None:
        # ---- bid side (buy orders) ----------------------------------------
        self._bid_levels: dict[float, deque[LimitOrder]] = {}
        self._bid_prices: list[float] = []   # ascending; best = last element

        # ---- ask side (sell orders) ----------------------------------------
        self._ask_levels: dict[float, deque[LimitOrder]] = {}
        self._ask_prices: list[float] = []   # ascending; best = first element

        # ---- shared lookup ---------------------------------------------------
        self._order_map: dict[int, LimitOrder] = {}

        # ---- trade counter ---------------------------------------------------
        self._trade_seq: int = 0
        self._clock:     int = 0   # monotonic sequence for price-time priority

    # ================================================================== #
    #  Public interface                                                    #
    # ================================================================== #

    def add_limit_order(self, order: LimitOrder) -> list[Trade]:
        """
        Insert a limit order into the book, matching against the opposite
        side first (aggressive fills), then resting the remainder.

        Complexity: O(k log N) for k fills across at most k distinct price
        levels; O(log N) for the final resting insertion.

        Args:
            order: A ``LimitOrder`` with a unique ``order_id``.

        Returns:
            A (possibly empty) list of ``Trade`` objects produced by crossing.
        """
        if order.order_id in self._order_map:
            raise ValueError(f"Duplicate order_id: {order.order_id}")

        self._clock += 1
        order.timestamp = self._clock
        self._order_map[order.order_id] = order

        trades: list[Trade] = []

        if order.side == Side.BID:
            trades = self._match_bid(order)
        else:
            trades = self._match_ask(order)

        # Rest whatever volume remains
        if order.remaining > 0 and order.status == OrderStatus.OPEN:
            self._rest_order(order)
        elif order.remaining > 0 and order.status == OrderStatus.PARTIAL:
            self._rest_order(order)

        return trades

    def add_market_order(self, order: MarketOrder) -> list[Trade]:
        """
        Execute a market order, sweeping the opposite side of the book.

        A market order has no price — it matches at whatever prices are
        available until fully filled or the book is exhausted.

        Complexity: O(k log N) where k = number of resting orders consumed.

        Args:
            order: A ``MarketOrder`` specifying side and volume.

        Returns:
            List of ``Trade`` objects produced during execution.
        """
        # Synthesise a LimitOrder at an extreme price so it always crosses
        sentinel_price = float("inf") if order.side == Side.BID else 0.0
        limit = LimitOrder(
            order_id=order.order_id,
            side=order.side,
            price=sentinel_price,
            volume=order.volume,
        )
        self._clock += 1
        limit.timestamp = self._clock
        # NOTE: market orders are NOT added to _order_map (non-resting)

        if order.side == Side.BID:
            trades = self._match_bid(limit)
        else:
            trades = self._match_ask(limit)

        return trades

    def cancel_order(self, order_id: int) -> bool:
        """
        Cancel a resting limit order by its id.

        Uses the ``_order_map`` for O(1) lookup.  The order is marked
        CANCELLED in place; the dead entry is pruned lazily when the
        matching engine encounters it during a sweep.

        Args:
            order_id: The id of the order to cancel.

        Returns:
            True if the order existed and was open/partial; False otherwise.
        """
        order = self._order_map.get(order_id)
        if order is None:
            return False
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            return False
        order.status = OrderStatus.CANCELLED
        return True

    def best_bid(self) -> Optional[float]:
        """Return the highest resting bid price, or None if the bid side is empty."""
        self._purge_empty_bid_levels()
        return self._bid_prices[-1] if self._bid_prices else None

    def best_ask(self) -> Optional[float]:
        """Return the lowest resting ask price, or None if the ask side is empty."""
        self._purge_empty_ask_levels()
        return self._ask_prices[0] if self._ask_prices else None

    def mid_price(self) -> Optional[float]:
        """Return arithmetic mid-price between best bid and best ask."""
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def depth_snapshot(
        self, levels: int = 5
    ) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
        """
        Return (bids, asks) each as a list of (price, total_volume) tuples,
        sorted by best price first, up to ``levels`` price levels deep.

        Skips cancelled / fully-filled orders lazily during aggregation.

        Returns:
            bids: [(price, vol), ...] highest price first
            asks: [(price, vol), ...] lowest price first
        """
        bids: list[tuple[float, int]] = []
        for price in reversed(self._bid_prices):
            vol = self._live_volume_at(self._bid_levels, price)
            if vol > 0:
                bids.append((price, vol))
            if len(bids) >= levels:
                break

        asks: list[tuple[float, int]] = []
        for price in self._ask_prices:
            vol = self._live_volume_at(self._ask_levels, price)
            if vol > 0:
                asks.append((price, vol))
            if len(asks) >= levels:
                break

        return bids, asks

    # ================================================================== #
    #  Internal matching logic                                            #
    # ================================================================== #

    def _match_bid(self, aggressor: LimitOrder) -> list[Trade]:
        """
        Cross an incoming BID order against the ask side.

        Iterates from best ask upward; stops when aggressor is filled,
        no more asks exist, or the ask price exceeds the aggressor limit.
        """
        trades: list[Trade] = []

        while aggressor.remaining > 0 and self._ask_prices:
            best_ask_price = self._ask_prices[0]
            if aggressor.price < best_ask_price:
                break  # price limit not met

            queue = self._ask_levels[best_ask_price]
            trade = self._fill_from_queue(aggressor, queue, Side.BID)
            if trade:
                trades.append(trade)

            if not self._live_orders_in_queue(queue):
                # Remove the depleted price level
                self._ask_levels.pop(best_ask_price, None)
                self._ask_prices.pop(0)

        return trades

    def _match_ask(self, aggressor: LimitOrder) -> list[Trade]:
        """
        Cross an incoming ASK order against the bid side.

        Iterates from best bid downward; stops when aggressor is filled,
        no bids exist, or the bid price falls below the aggressor limit.
        """
        trades: list[Trade] = []

        while aggressor.remaining > 0 and self._bid_prices:
            best_bid_price = self._bid_prices[-1]
            if aggressor.price > best_bid_price:
                break  # price limit not met

            queue = self._bid_levels[best_bid_price]
            trade = self._fill_from_queue(aggressor, queue, Side.ASK)
            if trade:
                trades.append(trade)

            if not self._live_orders_in_queue(queue):
                self._bid_levels.pop(best_bid_price, None)
                self._bid_prices.pop()

        return trades

    def _fill_from_queue(
        self,
        aggressor: LimitOrder,
        queue: deque[LimitOrder],
        aggressor_side: Side,
    ) -> Optional[Trade]:
        """
        Consume volume from the front of a price-level queue.

        Skips dead (cancelled / filled) entries without O(n) scanning —
        each dead entry is popped exactly once when encountered at the front.

        Args:
            aggressor:      The incoming order consuming volume.
            queue:          FIFO deque of resting limit orders at one price.
            aggressor_side: The side of the aggressor (for Trade tagging).

        Returns:
            A Trade if any volume was exchanged, else None.
        """
        while queue and aggressor.remaining > 0:
            passive = queue[0]

            # --- lazy cleanup: skip dead orders ----------------------------
            if passive.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
                queue.popleft()
                continue

            fill_qty = min(aggressor.remaining, passive.remaining)
            exec_price = passive.price  # passive order sets the price

            # Update both sides
            aggressor.remaining -= fill_qty
            passive.remaining   -= fill_qty

            if aggressor.remaining == 0:
                aggressor.status = OrderStatus.FILLED
            else:
                aggressor.status = OrderStatus.PARTIAL

            if passive.remaining == 0:
                passive.status = OrderStatus.FILLED
                queue.popleft()
            else:
                passive.status = OrderStatus.PARTIAL
                # passive stays at front of queue for next aggressor

            self._trade_seq += 1
            return Trade(
                trade_id=self._trade_seq,
                aggressor_id=aggressor.order_id,
                passive_id=passive.order_id,
                price=exec_price,
                volume=fill_qty,
                aggressor_side=aggressor_side,
            )

        return None

    # ================================================================== #
    #  Internal helpers                                                    #
    # ================================================================== #

    def _rest_order(self, order: LimitOrder) -> None:
        """Insert a remaining limit order into the appropriate side's book."""
        if order.side == Side.BID:
            levels = self._bid_levels
            prices = self._bid_prices
        else:
            levels = self._ask_levels
            prices = self._ask_prices

        if order.price not in levels:
            levels[order.price] = deque()
            insort(prices, order.price)

        levels[order.price].append(order)

    def _live_volume_at(
        self, levels: dict[float, deque[LimitOrder]], price: float
    ) -> int:
        """Aggregate live (non-cancelled, non-filled) volume at a price level."""
        queue = levels.get(price)
        if queue is None:
            return 0
        return sum(
            o.remaining
            for o in queue
            if o.status not in (OrderStatus.FILLED, OrderStatus.CANCELLED)
        )

    @staticmethod
    def _live_orders_in_queue(queue: deque[LimitOrder]) -> bool:
        """Return True if at least one live order exists in the queue."""
        return any(
            o.status not in (OrderStatus.FILLED, OrderStatus.CANCELLED)
            for o in queue
        )

    def _purge_empty_bid_levels(self) -> None:
        """Remove trailing empty bid price levels (called only before best_bid lookup)."""
        while self._bid_prices:
            price = self._bid_prices[-1]
            if self._live_volume_at(self._bid_levels, price) == 0:
                self._bid_levels.pop(price, None)
                self._bid_prices.pop()
            else:
                break

    def _purge_empty_ask_levels(self) -> None:
        """Remove leading empty ask price levels (called only before best_ask lookup)."""
        while self._ask_prices:
            price = self._ask_prices[0]
            if self._live_volume_at(self._ask_levels, price) == 0:
                self._ask_levels.pop(price, None)
                self._ask_prices.pop(0)
            else:
                break
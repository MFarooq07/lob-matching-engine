"""
models.py — Core data model definitions for the Limit Order Book engine.

All domain objects are pure dataclasses with full type hints.
No external dependencies; only Python standard library.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Side(Enum):
    """Order side enumeration."""
    BID = auto()   # Buy side
    ASK = auto()   # Sell side


class OrderStatus(Enum):
    """Lifecycle state of a single limit order."""
    OPEN      = auto()   # Resting in the book
    PARTIAL   = auto()   # Partially filled, still resting
    FILLED    = auto()   # Completely executed
    CANCELLED = auto()   # Cancelled before full execution


@dataclass
class LimitOrder:
    """
    A single limit order placed into the order book.

    Attributes:
        order_id:  Unique identifier for this order.
        side:      BID (buy) or ASK (sell).
        price:     Limit price — the worst acceptable execution price.
        volume:    Original order size in shares/contracts.
        remaining: Shares not yet executed; decremented on each partial fill.
        status:    Current lifecycle state.
        timestamp: Monotonically-increasing sequence number (price-time priority).
        owner:     Optional tag identifying the originating agent (e.g. "BOT").
    """
    order_id:  int
    side:      Side
    price:     float
    volume:    int
    remaining: int        = field(init=False)
    status:    OrderStatus = field(init=False, default=OrderStatus.OPEN)
    timestamp: int        = field(default=0)
    owner:     str        = field(default="")

    def __post_init__(self) -> None:
        self.remaining = self.volume

    # ------------------------------------------------------------------ #
    #  Comparison helpers used by heapq priority queues                   #
    # ------------------------------------------------------------------ #
    def _bid_key(self) -> tuple[float, int]:
        """Higher price wins; earlier timestamp wins on tie → negate price."""
        return (-self.price, self.timestamp)

    def _ask_key(self) -> tuple[float, int]:
        """Lower price wins; earlier timestamp wins on tie."""
        return (self.price, self.timestamp)

    def priority_key(self) -> tuple[float, int]:
        """Return the correct priority key depending on side."""
        return self._bid_key() if self.side == Side.BID else self._ask_key()

    def __lt__(self, other: LimitOrder) -> bool:  # needed by heapq
        return self.priority_key() < other.priority_key()


@dataclass
class MarketOrder:
    """
    A market order that executes immediately against resting limit orders.

    Attributes:
        order_id: Unique identifier.
        side:     BID → buy at best ask; ASK → sell at best bid.
        volume:   Shares to execute.
    """
    order_id: int
    side:     Side
    volume:   int


@dataclass
class Trade:
    """
    An execution event produced by the matching engine.

    Attributes:
        trade_id:       Sequential trade counter.
        aggressor_id:   The order that caused the crossing.
        passive_id:     The resting limit order that was hit.
        price:          Execution price (passive order's limit price).
        volume:         Shares exchanged in this fill.
        aggressor_side: Side of the aggressor (BID = buyer aggressed).
    """
    trade_id:       int
    aggressor_id:   int
    passive_id:     int
    price:          float
    volume:         int
    aggressor_side: Side
"""
simulation.py — Automated market-making bot + simulation harness.

Architecture
------------
  MarketMakingBot  — quotes a two-sided spread around a reference price;
                     tracks inventory, realized PnL, and unrealized PnL.

  Simulation       — orchestrates the reference price random walk, generates
                     random background noise orders, steps the bot each tick,
                     and prints the book depth + final summary.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .book import OrderBook
from .models import LimitOrder, MarketOrder, OrderStatus, Side, Trade


# =========================================================================== #
#  Bot configuration                                                           #
# =========================================================================== #

@dataclass
class BotConfig:
    """
    Configuration knobs for the market-making bot.

    Attributes:
        target_spread:     Desired full bid-ask spread in price units.
        max_inventory:     Absolute inventory cap; bot skews quotes beyond this.
        quote_size:        Default volume of each two-sided quote.
        inventory_skew:    How aggressively to skew prices per unit of inventory.
        tick_size:         Minimum price increment (used for rounding quotes).
    """
    target_spread:   float = 0.05
    max_inventory:   int   = 100
    quote_size:      int   = 10
    inventory_skew:  float = 0.001   # price shift per share of inventory
    tick_size:       float = 0.01


# =========================================================================== #
#  Market-making bot                                                           #
# =========================================================================== #

class MarketMakingBot:
    """
    A two-sided quoting agent that:
      1. Tracks its current inventory (net long/short position).
      2. Calculates bid/ask quotes around the reference mid-price,
         applying an inventory skew to naturally flatten its book.
      3. Cancels and replaces its own orders each simulation step.
      4. Computes realized and unrealized PnL.

    Inventory accounting (simple FIFO cash approach):
      - Each time the bot's ask is hit → it sold shares; cash increases.
      - Each time the bot's bid is hit → it bought shares; cash decreases.
      - Realized PnL = total cash flow from executed trades.
      - Unrealized PnL = inventory × (current_mid − average_entry_price).
    """

    BOT_TAG = "BOT"

    def __init__(self, config: BotConfig, book: OrderBook, id_start: int = 10_000):
        self.cfg         = config
        self.book        = book
        self._id_counter = id_start

        # State
        self.inventory:       int   = 0       # positive = long, negative = short
        self.cash:            float = 0.0     # realized cash flow
        self.avg_entry_price: float = 0.0     # volume-weighted average cost basis

        # Active quote order ids
        self._bid_order_id: Optional[int] = None
        self._ask_order_id: Optional[int] = None

        # Metrics
        self.max_inventory_divergence: int   = 0
        self.trades_participated:      int   = 0
        self.total_volume_as_passive:  int   = 0

    # ------------------------------------------------------------------ #
    #  Step — called once per simulation tick                             #
    # ------------------------------------------------------------------ #

    def step(self, ref_price: float, executed_trades: list[Trade]) -> None:
        """
        Process executed trades, update PnL/inventory, then refresh quotes.

        Args:
            ref_price:        The current reference (mid) price from the walk.
            executed_trades:  Trades produced by the engine this tick.
        """
        self._process_fills(executed_trades)
        self._refresh_quotes(ref_price)
        self._update_max_divergence()

    # ------------------------------------------------------------------ #
    #  PnL / metrics                                                       #
    # ------------------------------------------------------------------ #

    def realized_pnl(self) -> float:
        """
        Realized PnL is the net cash flow from all completed round-trips.
        We compute it as: cash + inventory * avg_entry_price
        (i.e., what we'd have if we closed our position at cost basis).
        Actually for a pure market-maker the convention is:
            realized_pnl = cash_from_sells - cash_from_buys (for matched pairs)
        We accumulate it directly in self.cash as signed cash flow:
            buy  → cash -= price * vol  (outflow)
            sell → cash += price * vol  (inflow)
        Realized PnL = cash flow from *closed* inventory.
        """
        return round(self.cash, 4)

    def unrealized_pnl(self, current_mid: float) -> float:
        """
        Mark-to-market on the open inventory position.

        unrealized = inventory × (current_mid − average_entry_price)
        """
        if self.inventory == 0:
            return 0.0
        return round(self.inventory * (current_mid - self.avg_entry_price), 4)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _process_fills(self, trades: list[Trade]) -> None:
        """
        Scan newly generated trades; update inventory and cash for any
        that involved the bot's own orders.
        """
        bot_order_ids = {self._bid_order_id, self._ask_order_id}

        for trade in trades:
            is_passive_bid = trade.passive_id == self._bid_order_id
            is_passive_ask = trade.passive_id == self._ask_order_id

            if not (is_passive_bid or is_passive_ask):
                continue  # this trade doesn't involve the bot

            vol   = trade.volume
            price = trade.price
            self.trades_participated      += 1
            self.total_volume_as_passive  += vol

            if is_passive_bid:
                # Bot's resting bid was hit → bot BOUGHT
                self._update_inventory(+vol, price)
                self.cash -= price * vol

            else:
                # Bot's resting ask was hit → bot SOLD
                self._update_inventory(-vol, price)
                self.cash += price * vol

    def _update_inventory(self, delta: int, price: float) -> None:
        """Update inventory and recalculate average entry price."""
        old_inv = self.inventory
        new_inv = old_inv + delta

        if new_inv == 0:
            self.avg_entry_price = 0.0
        elif (old_inv >= 0 and delta > 0) or (old_inv <= 0 and delta < 0):
            # Adding to an existing position — blend the price
            total_cost = self.avg_entry_price * abs(old_inv) + price * abs(delta)
            self.avg_entry_price = total_cost / abs(new_inv)
        else:
            # Reducing / flipping position — entry price stays until fully flat
            if abs(delta) >= abs(old_inv):
                self.avg_entry_price = price if new_inv != 0 else 0.0
            # else: partial close — keep existing avg_entry_price

        self.inventory = new_inv

    def _refresh_quotes(self, ref_price: float) -> None:
        """
        Cancel existing two-sided quotes and place fresh ones.

        Inventory skew:  if long, push quotes down to encourage selling;
                         if short, push quotes up to encourage buying.
        """
        # --- cancel existing quotes ---
        if self._bid_order_id is not None:
            self.book.cancel_order(self._bid_order_id)
            self._bid_order_id = None
        if self._ask_order_id is not None:
            self.book.cancel_order(self._ask_order_id)
            self._ask_order_id = None

        half_spread = self.cfg.target_spread / 2.0
        skew        = self.cfg.inventory_skew * self.inventory  # shift mid

        raw_bid = ref_price - half_spread - skew
        raw_ask = ref_price + half_spread - skew

        # Snap to tick grid
        bid_price = self._round_to_tick(raw_bid)
        ask_price = self._round_to_tick(raw_ask)

        # Sanity guard: ensure bid < ask after rounding
        if bid_price >= ask_price:
            ask_price = bid_price + self.cfg.tick_size

        # Size: reduce when inventory is near the cap
        inv_fraction = abs(self.inventory) / max(self.cfg.max_inventory, 1)
        adj_size     = max(1, int(self.cfg.quote_size * (1.0 - inv_fraction)))

        # Post new orders
        bid_id = self._next_id()
        ask_id = self._next_id()

        bid_order = LimitOrder(
            order_id=bid_id,
            side=Side.BID,
            price=bid_price,
            volume=adj_size,
            owner=self.BOT_TAG,
        )
        ask_order = LimitOrder(
            order_id=ask_id,
            side=Side.ASK,
            price=ask_price,
            volume=adj_size,
            owner=self.BOT_TAG,
        )

        self.book.add_limit_order(bid_order)
        self.book.add_limit_order(ask_order)

        self._bid_order_id = bid_id
        self._ask_order_id = ask_id

    def _round_to_tick(self, price: float) -> float:
        """Round a raw price to the nearest tick increment."""
        tick = self.cfg.tick_size
        return round(round(price / tick) * tick, 10)

    def _update_max_divergence(self) -> None:
        if abs(self.inventory) > self.max_inventory_divergence:
            self.max_inventory_divergence = abs(self.inventory)


# =========================================================================== #
#  Simulation harness                                                          #
# =========================================================================== #

class Simulation:
    """
    Drives the LOB + market-making bot through a series of ticks.

    Each tick:
      1. Advance the reference price (geometric random walk).
      2. Inject N random background limit/market orders.
      3. Let the bot refresh its quotes.
      4. Print the book depth.

    After all ticks, print a final summary report.
    """

    SEPARATOR = "─" * 70

    def __init__(
        self,
        bot_config: BotConfig,
        num_ticks:        int   = 40,
        initial_price:    float = 100.0,
        price_volatility: float = 0.002,    # per-tick log vol
        orders_per_tick:  int   = 4,
        random_seed:      int   = 42,
        depth_levels:     int   = 5,
    ) -> None:
        random.seed(random_seed)

        self.num_ticks        = num_ticks
        self.price_vol        = price_volatility
        self.orders_per_tick  = orders_per_tick
        self.depth_levels     = depth_levels
        self.ref_price        = initial_price

        self.book = OrderBook()
        self.bot  = MarketMakingBot(bot_config, self.book)

        self._order_seq:  int = 0
        self._total_trades: list[Trade] = []

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Execute the full simulation and print results."""
        print(self.SEPARATOR)
        print("  HIGH-PERFORMANCE LIMIT ORDER BOOK — SIMULATION START")
        print(self.SEPARATOR)

        for tick in range(1, self.num_ticks + 1):
            # 1. Advance reference price
            shock = random.gauss(0, self.price_vol)
            self.ref_price *= math.exp(shock)

            tick_trades: list[Trade] = []

            # 2. Bot refreshes quotes first (seeds the book with liquidity)
            self.bot.step(self.ref_price, tick_trades)

            # 3. Inject background noise orders that may cross bot quotes
            for _ in range(self.orders_per_tick):
                new_trades = self._inject_random_order()
                tick_trades.extend(new_trades)

            # 4. Process any fills against bot's resting quotes
            self.bot.step(self.ref_price, tick_trades)

            self._total_trades.extend(tick_trades)

            # 5. Print depth
            print(f"\n{'═'*70}")
            print(f"  TICK {tick:>3}  |  Ref Price: {self.ref_price:>9.4f}  |  Bot Inventory: {self.bot.inventory:>+5}")
            print(f"{'═'*70}")
            self._print_depth()
            for t in tick_trades:
                print(
                    f"  ✦ TRADE #{t.trade_id:<4} | "
                    f"aggressor={t.aggressor_id} ({'BID' if t.aggressor_side == Side.BID else 'ASK'}) "
                    f"× passive={t.passive_id} | "
                    f"{t.volume:>4} @ {t.price:.4f}"
                )

        self._print_summary()

    # ------------------------------------------------------------------ #
    #  Background order injection                                          #
    # ------------------------------------------------------------------ #

    def _next_id(self) -> int:
        self._order_seq += 1
        return self._order_seq

    def _inject_random_order(self) -> list[Trade]:
        """
        Randomly inject a limit or market order near the current ref price.

        30 % chance market order; 70 % limit order with price drawn from
        a normal distribution ±0.3 % around the ref price.
        """
        trades: list[Trade] = []
        side  = Side.BID if random.random() < 0.5 else Side.ASK
        vol   = random.randint(1, 20)
        oid   = self._next_id()

        is_market = random.random() < 0.3

        if is_market:
            mo     = MarketOrder(order_id=oid, side=side, volume=vol)
            trades = self.book.add_market_order(mo)
        else:
            offset = random.gauss(0, self.ref_price * 0.003)
            price  = round(self.ref_price + offset, 2)
            price  = max(0.01, price)

            lo     = LimitOrder(order_id=oid, side=side, price=price, volume=vol)
            trades = self.book.add_limit_order(lo)

        return trades

    # ------------------------------------------------------------------ #
    #  Display helpers                                                     #
    # ------------------------------------------------------------------ #

    def _print_depth(self) -> None:
        bids, asks = self.book.depth_snapshot(self.depth_levels)

        max_rows = max(len(bids), len(asks))
        header   = f"  {'BID PRICE':>12}  {'SIZE':>6}    {'ASK PRICE':>12}  {'SIZE':>6}"
        print(header)
        print(f"  {'─'*12}  {'─'*6}    {'─'*12}  {'─'*6}")

        for i in range(max_rows):
            bid_str = f"{bids[i][0]:>12.4f}  {bids[i][1]:>6}" if i < len(bids) else " " * 20
            ask_str = f"{asks[i][0]:>12.4f}  {asks[i][1]:>6}" if i < len(asks) else ""
            print(f"  {bid_str}    {ask_str}")

    def _print_summary(self) -> None:
        total_vol  = sum(t.volume for t in self._total_trades)
        current_mid = self.bot.book.mid_price() or self.ref_price

        r_pnl = self.bot.realized_pnl()
        u_pnl = self.bot.unrealized_pnl(current_mid)

        print(f"\n{self.SEPARATOR}")
        print("  FINAL SIMULATION SUMMARY")
        print(self.SEPARATOR)
        print(f"  {'Total Ticks Simulated':<35}: {self.num_ticks}")
        print(f"  {'Total Trades Executed':<35}: {len(self._total_trades)}")
        print(f"  {'Total Volume Traded':<35}: {total_vol:,} shares")
        print(f"  {'Bot Trades Participated':<35}: {self.bot.trades_participated}")
        print(f"  {'Bot Volume (passive fills)':<35}: {self.bot.total_volume_as_passive:,} shares")
        print(f"  {'Bot Realized PnL':<35}: {r_pnl:>+.4f}")
        print(f"  {'Bot Unrealized PnL':<35}: {u_pnl:>+.4f}")
        print(f"  {'Bot Total PnL':<35}: {r_pnl + u_pnl:>+.4f}")
        print(f"  {'Bot Final Inventory':<35}: {self.bot.inventory:>+} shares")
        print(f"  {'Max Inventory Divergence':<35}: {self.bot.max_inventory_divergence} shares")
        print(f"  {'Final Reference Price':<35}: {self.ref_price:.4f}")
        print(self.SEPARATOR)
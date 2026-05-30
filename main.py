"""
main.py — Entry point for the LOB matching engine simulation.

Run:
    python main.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from engine.simulation import Simulation, BotConfig

if __name__ == "__main__":
    config = BotConfig(
        target_spread   = 0.05,
        max_inventory   = 100,
        quote_size      = 10,
        inventory_skew  = 0.001,
        tick_size       = 0.01,
    )

    sim = Simulation(
        bot_config       = config,
        num_ticks        = 40,
        initial_price    = 100.0,
        price_volatility = 0.002,
        orders_per_tick  = 4,
        random_seed      = 42,
        depth_levels     = 5,
    )

    sim.run()
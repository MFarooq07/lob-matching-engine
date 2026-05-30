# HIGH-PERFORMANCE LIMIT ORDER BOOK (LOB) & MARKET MAKING SIMULATOR
----------------------------------------------------------------------

```mermaid
graph TD
    %% Styling
    classDef mapStyle fill:#f9f,stroke:#333,stroke-width:2px;
    classDef listStyle fill:#bbf,stroke:#333,stroke-width:2px;
    classDef queueStyle fill:#bfb,stroke:#333,stroke-width:2px;
    classDef modelStyle fill:#fbb,stroke:#333,stroke-width:2px;

    %% Core Entry
    OrderBook[OrderBook Engine Instance] --> _order_map
    OrderBook --> _bid_prices
    OrderBook --> _ask_prices

    %% Order Map Lookup Vector
    _order_map[_order_map <br> dict: order_id -> LimitOrder]:::mapStyle
    _order_map -->|O1 Instant In-Place Cancel| LO_Resting

    %% Bid Side Data Structures
    subgraph Bid Side Data Structures (Buy Orders)
        _bid_prices[_bid_prices <br> list: Sorted Ascending]:::listStyle
        _bid_levels[_bid_levels <br> dict: price -> deque]:::mapStyle
        
        _bid_prices -->|Best Bid is last element| BestBid[prices[-1]]
        _bid_levels -->|Key Access| BidDeque[collections.deque]:::queueStyle
        BidDeque -->|FIFO Element 1| LO_Resting[LimitOrder Dataclass]:::modelStyle
        BidDeque -->|FIFO Element 2| LO_Resting2[LimitOrder Dataclass]:::modelStyle
    end

    %% Ask Side Data Structures
    subgraph Ask Side Data Structures (Sell Orders)
        _ask_prices[_ask_prices <br> list: Sorted Ascending]:::listStyle
        _ask_levels[_ask_levels <br> dict: price -> deque]:::mapStyle
        
        _ask_prices -->|Best Ask is first element| BestAsk[prices[0]]
        _ask_levels -->|Key Access| AskDeque[collections.deque]:::queueStyle
        AskDeque -->|FIFO Element 1| LO_Resting3[LimitOrder Dataclass]:::modelStyle
    end
```
A lightweight, deterministic, high-performance in-memory Limit Order
Book (LOB) matching engine and algorithmic market-making simulation
built from scratch in pure Python 3.1.

This project simulates real-world electronic financial market micro-
structures without relying on heavy external processing libraries,
showcasing optimal data structure utilization and efficient complexity
management.

----------------------------------------------------------------------
## PROJECT STRUCTURE
----------------------------------------------------------------------
```
lob-matching-engine/
├── main.py              # Application entry point & simulation config
├── engine/
│   ├── `__init__.py`      # Package level exports
│   ├── `models.py`        # Core domain dataclasses (Orders, Trades)
│   ├── `book.py`          # Dual-sided matching engine logic
│   └── `simulation.py`    # Random-walk harness and quoting bot
├── tests/
    └── `test_book.py `    # Comprehensive unit testing suite

```
----------------------------------------------------------------------
## FEATURES
----------------------------------------------------------------------
* **Optimal Memory Lookups:** Employs a decoupled map-and-sweep
  architecture. Order book mutations (insertions, cancellations,
  matches) bypass slow linear searches O(N) in favor of optimized
  O(log N) or O(1) amortized operations using standard library
  primitives (collections.deque, bisect).

* **Lazy Cancellation Cleanup:** Cancelled or completed orders are
  invalidated in-place via an `internal _order_map` index lookup O(1)
  and swept away dynamically when encountered at the front of a
  price queue during subsequent crossing engine sweeps.

* **Inventory-Skewed Quoting Agent:** Implements an automated market-
  making bot that quotes dual-sided liquidity around a moving
  reference price. The agent applies an adaptive inventory-skew
  constraint to mitigate toxic order exposure and maintain flat
  position delta risk limits.

* **Deterministic Evaluation Environment:** Drives a reproducible
  geometric random-walk simulation with mixed liquidity background
  orders (70% limit orders, 30% market orders) to evaluate trading
  metrics (Realized/Unrealized PnL, Maximum Inventory Divergence).


----------------------------------------------------------------------
## COMPLEXITY ANALYSIS MATRIX
----------------------------------------------------------------------


Operation:             New Limit Order Insertion
Structure:             bisect.insort into _sorted_prices + deque.append
Time Complexity:       O(log N)

Operation:             Order Cancellation
Structure:             State lookup via ID in _order_map + lazy sweep
Time Complexity:       O(1)

Operation:             Best Price Peek
Structure:             Array index access (prices[0] / prices[-1])
Time Complexity:       O(1)

Operation:             Order Matching Execution
Structure:             Iterative traversal across exhausted levels
Time Complexity:       O(k log N)  [where k = price levels crossed]


----------------------------------------------------------------------
## QUICK START
----------------------------------------------------------------------
Running the Simulation:
Execute the primary simulation harness from the root directory:
`python main.py`

Running Test Verification:
To validate engine mechanics, boundary edge-cases, and execution
accuracy, run the unittest suite:
`python -m unittest discover -s tests`

======================================================================#

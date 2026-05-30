# HIGH-PERFORMANCE LIMIT ORDER BOOK (LOB) & MARKET MAKING SIMULATOR
----------------------------------------------------------------------

```mermaid
flowchart TD
    %% Style Formats
    classDef mapStyle fill:#00f,stroke:#333,stroke-width:2px;
    classDef listStyle fill:#0f0,stroke:#333,stroke-width:2px;
    classDef queueStyle fill:#f00,stroke:#333,stroke-width:2px;
    classDef modelStyle fill:#f0f,stroke:#333,stroke-width:2px;

    %% Base Structural Framework
    OrderBook[OrderBook Engine Instance] --> orderMap
    OrderBook --> bidPrices
    OrderBook --> bidLevels
    OrderBook --> askPrices
    OrderBook --> askLevels

    %% Core Map Lookup Index
    orderMap["_order_map<br>dict: order_id to LimitOrder"]:::mapStyle
    orderMap -->|"O(1) Instant In-Place Cancel"| loResting1

    %% Bid Side Data Configurations
    subgraph bid_side ["Bid Side Data Structures (Buy Orders)"]
        bidPrices["_bid_prices<br>list: Sorted Ascending"]:::listStyle
        bidLevels["_bid_levels<br>dict: price to deque"]:::mapStyle
        
        bidPrices -->|"Best Bid is last element"| bestBid["prices[-1]"]
        bidLevels -->|"Key Access"| bidDeque["collections.deque"]:::queueStyle
        bidDeque -->|"FIFO Element 1"| loResting1["LimitOrder Dataclass"]:::modelStyle
        bidDeque -->|"FIFO Element 2"| loResting2["LimitOrder Dataclass"]:::modelStyle
    end

    %% Ask Side Data Configurations
    subgraph ask_side ["Ask Side Data Structures (Sell Orders)"]
        askPrices["_ask_prices<br>list: Sorted Ascending"]:::listStyle
        askLevels["_ask_levels<br>dict: price to deque"]:::mapStyle
        
        askPrices -->|"Best Ask is first element"| bestAsk["prices[0]"]
        askLevels -->|"Key Access"| askDeque["collections.deque"]:::queueStyle
        askDeque -->|"FIFO Element 1"| loResting3["LimitOrder Dataclass"]:::modelStyle
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

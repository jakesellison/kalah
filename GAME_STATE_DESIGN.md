# Mancala Game State Design - Kalah(6,4)

## Game Configuration
- **Variant**: Kalah(6,4) with standard capture rules
- **Board**: 6 pits per player + 1 store per player = 14 positions
- **Starting seeds**: 4 per pit × 12 pits = 24 total seeds (constant)
- **Goal**: Verify Irving's result (win by 10 for first player)

## Board Layout
```
      P2 Pits (12-7)
   [12][11][10][9][8][7]
[13]                    [6]  <- Stores
   [0] [1] [2] [3][4][5]
      P1 Pits (0-5)
```

Index mapping:
- P1 pits: indices 0-5
- P1 store: index 6
- P2 pits: indices 7-12
- P2 store: index 13

## State Space Analysis

### Theoretical Maximum
- 24 seeds across 14 positions
- Combinatorial: C(24+14-1, 14-1) = C(37, 13) ≈ **10 billion** possible positions

### Actual Game Graph (Reachable Positions)
- **Conservative estimate**: ~**1.3 trillion positions** (10^12)
- State-space complexity: ~10^12
- Game-tree complexity: 6×10^18
- Source: Game complexity research

### Storage Requirements - CRITICAL UPDATE
- **Full database**: 1.3 trillion positions × 9 bytes = **11.7 TB**
- **Your laptop disk**: 399 GB available
- **Conclusion**: **Cannot store complete database on laptop alone**

### Storage Strategy Options
1. **Cloud/External Storage**: Provision database on AWS/GCP or external drives
2. **Compressed/Sparse Storage**: Exploit symmetries, only store unique positions
3. **Hybrid Approach**: Build graph in stages, store on external media
4. **Streaming**: Generate-compute-discard for positions, only store results

---

## Representation Options

### Option A: Bit-Packed State (RECOMMENDED)
**Layout**: Pack into 72 bits (9 bytes)

```
Bits 0-4:   Pit 0  (5 bits, holds 0-31)
Bits 5-9:   Pit 1
Bits 10-14: Pit 2
Bits 15-19: Pit 3
Bits 20-24: Pit 4
Bits 25-29: Pit 5
Bits 30-34: Store 6
Bits 35-39: Pit 7
Bits 40-44: Pit 8
Bits 45-49: Pit 9
Bits 50-54: Pit 10
Bits 55-59: Pit 11
Bits 60-64: Pit 12
Bit 65:     Current player (0=P1, 1=P2)
Bits 66-71: Unused (or store 13, derived)
```

**Advantages**:
- Compact: 9 bytes per state
- Fast bitwise operations
- Can derive position 13 from sum (24 - sum of other positions)
- Efficient for hash tables and transposition tables

**Implementation**:
```python
class GameState:
    __slots__ = ['data']  # Two 64-bit integers or bytearray

    def __init__(self):
        self.data = bytearray(9)  # 72 bits

    def get_pit(self, index: int) -> int:
        # Extract 5 bits starting at bit_offset = index * 5

    def set_pit(self, index: int, value: int):
        # Set 5 bits starting at bit_offset = index * 5

    def get_player(self) -> int:
        # Extract bit 65

    def hash(self) -> int:
        # Fast hash for transposition table
```

### Option B: Byte Array (SIMPLER)
**Layout**: 15 bytes

```python
state = [
    pits[0:6],    # P1 pits (6 bytes)
    store[6],     # P1 store (1 byte)
    pits[7:13],   # P2 pits (6 bytes)
    store[13],    # P2 store (1 byte)
    player        # Current turn (1 byte)
]
```

**Advantages**:
- Simple, readable code
- Fast direct access
- Easy debugging

**Disadvantages**:
- 15 bytes vs 9 bytes (67% more memory)
- For 10 billion states: 150 GB vs 90 GB

---

## Storage Strategy: Hybrid RAM/Disk

### Architecture

```
┌─────────────────────────────────────┐
│         BFS Worker Pool             │
│     (14 workers, 1 per core)        │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│      State Cache (RAM)              │
│   LRU Cache: ~3-4 GB                │
│   Hot states: current + prev depth  │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│     Persistent Storage (Disk)       │
│   Option 1: SQLite with indexes     │
│   Option 2: LevelDB/RocksDB         │
│   Option 3: Custom memory-mapped    │
└─────────────────────────────────────┘
```

### Database Options

#### Option 1: SQLite (RECOMMENDED for simplicity)
```sql
CREATE TABLE states (
    state_id INTEGER PRIMARY KEY,
    state BLOB NOT NULL,           -- 9-byte packed state
    depth INTEGER NOT NULL,         -- BFS depth
    value INTEGER,                  -- Minimax value (null until phase 2)
    best_move INTEGER              -- Best move index (null until phase 2)
);
CREATE INDEX idx_depth ON states(depth);
CREATE INDEX idx_state ON states(state);
```

**Pros**: Simple, ACID guarantees, good for debugging
**Cons**: Slower than specialized key-value stores

#### Option 2: LevelDB/RocksDB
- Key: state hash (8 bytes)
- Value: metadata (depth, value, best_move)

**Pros**: Very fast writes, LSM-tree optimized for bulk inserts
**Cons**: More complex setup

#### Option 3: Memory-Mapped File
- Custom format with perfect hashing
- Maximum performance
- More implementation work

### Recommended: Start with SQLite, optimize later

---

## Canonicalization & Deduplication

### Symmetry Detection
Kalah(6,4) has no board symmetry (stores are on specific sides), but we should:
- Use transposition tables to detect repeated positions
- Hash states efficiently for O(1) lookup

### Zobrist Hashing (for transposition tables)
```python
# Pre-generate random numbers for each (position, seed_count) pair
zobrist_table = [
    [random.getrandbits(64) for seeds in range(25)]  # 0-24 seeds
    for position in range(14)
]

def zobrist_hash(state):
    h = 0
    for pos in range(14):
        h ^= zobrist_table[pos][state[pos]]
    h ^= zobrist_player[state.player]
    return h
```

---

## Estimated Storage Requirements

### Phase 1: BFS (Forward Search)
Assuming 50% of theoretical positions are reachable:
- **States**: 5 billion positions × 9 bytes = 45 GB disk
- **RAM Cache**: 3-4 GB for current depth level
- **Indexes**: ~10 GB additional

**Total**: ~55 GB disk, ~4 GB RAM working set

### Phase 2: Retrograde Analysis
- Read from disk, compute minimax, write back
- Can process in batches by depth level
- RAM usage stays constant

---

## Parallelization Strategy

### Phase 1: BFS Level-by-Level
```
Depth 0: [Starting position]
  ↓
Depth 1: Generate all moves from depth 0 → ~6 positions
  ↓ (Distribute to 14 workers)
Depth 2: Generate all moves from depth 1 → ~50 positions
  ↓
...
  ↓
Depth 50+: Deep midgame positions (millions of states)
```

**Work Distribution**:
- Master process: Maintains current depth frontier
- Workers: Each takes chunk of positions, generates successors
- Coordination: Workers report new states → deduplicate → next depth

### Phase 2: Retrograde Minimax by Seed Count
```
Start: Endgame (0-10 seeds)
  ↑ (Process in parallel, no dependencies within seed count)
  ↑
Next: 11 seeds (use 0-10 results)
  ↑
...
  ↑
Final: 24 seeds (starting position)
```

Each seed-count level can be fully parallelized.

---

## Next Steps

1. Implement basic GameState class with bit-packing
2. Implement move generation and game rules
3. Set up SQLite database schema
4. Build single-threaded BFS prototype
5. Add parallelization with worker pool
6. Implement retrograde analysis
7. Verify against Irving's results

---

## Performance Targets

Based on Rawlings' work (106 days, 55 trillion nodes for 6,6):
- Kalah(6,4) should be much faster (fewer total states)
- With 14 cores vs his likely 4-8: ~2-3× speedup
- Target: **Complete solve in 1-7 days**

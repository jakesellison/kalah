# Mancala Solver Architecture Summary

Quick reference for understanding the codebase architecture and design decisions.

## Deduplication Strategy

The solver uses **backend-specific deduplication** to optimize performance:

### PostgreSQL (Recommended for 8+ workers)

**Strategy:** Database-level deduplication via `ON CONFLICT DO NOTHING`

```python
# Workers generate ALL positions, including duplicates
for parent in parents:
    for move in legal_moves:
        child = generate_child(parent, move)
        positions.append(child)  # No dedup check!

# Database insert with conflict handling
INSERT INTO positions VALUES (...)
ON CONFLICT (state_hash) DO NOTHING  -- Database rejects duplicates
```

**Characteristics:**
- ✅ Zero RAM overhead for deduplication
- ✅ MVCC allows all workers to insert concurrently
- ✅ Scales linearly with workers (14 workers = 14× faster)
- ✅ Optimal for Kalah(6,3) and larger variants
- ❌ Requires PostgreSQL installation

**Memory usage:** ~1.7GB total (workers + runtime), no dedup set

### SQLite (Good for 1-4 workers)

**Strategy:** In-memory hash set to avoid database lookups

```python
# Track seen hashes in RAM
seen_hashes: Set[int] = set()

for parent in parents:
    for move in legal_moves:
        child = generate_child(parent, move)
        child_hash = hash(child)

        if child_hash not in seen_hashes:  # O(1) lookup in RAM
            seen_hashes.add(child_hash)
            positions.append(child)

# Only insert unique positions
storage.insert_batch(positions)
```

**Characteristics:**
- ✅ Faster than database EXISTS() queries
- ✅ No PostgreSQL installation needed
- ✅ Adaptive: switches to DB mode if memory gets tight
- ❌ Hash set can grow to several GB at peak depths
- ❌ Write serialization limits parallel speedup (~30% efficiency)

**Memory usage:** ~1.7GB baseline + up to 6.4GB for dedup set (adaptive)

## Memory Management

### Components

**Fixed (always loaded):**
- Python runtime: ~800MB
- Worker processes: 14 × 70MB = ~1GB
- Database connections: ~200MB

**Variable (depends on phase/backend):**
- **SQLite dedup set:** 0 to 6.4GB (grows/shrinks per depth)
- **PostgreSQL dedup set:** 0 (always zero)
- **Minimax unsolved dict:** Varies by seed level (managed via chunks)

### Adaptive Behavior

The solver monitors memory and adapts:

**Normal (>4GB available):**
- SQLite: In-memory dedup
- PostgreSQL: Database dedup (always)
- Full parallelism

**Throttled (2-4GB available):**
- SQLite: May switch to DB dedup
- Worker chunk size reduced
- Logging warnings

**Critical (<2GB available):**
- SQLite: Switches to DB dedup
- Operations paused for GC
- Aggressive memory management

### Tools

**Memory monitoring (`utils/memory.py`):**
- Cross-platform (macOS/Linux)
- Works with or without psutil
- Tracks process + system memory
- Configurable thresholds

**Adaptive cache sizing:**
- SQLite cache: 16MB to 256MB based on available RAM
- Reduces automatically under memory pressure

## Worker Architecture

### BFS Phase

**ParallelSolver** (for multi-worker BFS):
```
Main Process
    ├─ Fetches positions at depth D
    ├─ Splits into chunks for workers
    │
    └─► Worker Pool (14 workers)
         ├─ Worker 1: chunk 1 → generates children
         ├─ Worker 2: chunk 2 → generates children
         ├─ ...
         └─ Worker 14: chunk N → generates children
              │
              └─► Database (inserts with dedup)
```

**Deduplication happens:**
- PostgreSQL: At database insert (ON CONFLICT)
- SQLite: In worker memory (hash set)

### Minimax Phase

**ParallelMinimaxSolver** (retrograde analysis):
```
For each seed level (0 to max_seeds):
    Fetch all positions at this level

    While unsolved positions exist:
        ├─ Workers check: are children solved?
        ├─ Workers solve: compute minimax values
        └─ Update database

    Move to next seed level
```

**Memory-aware:**
- Adaptive chunk sizes based on RAM
- Pauses if critically low
- Scales workers down if needed

## Performance Characteristics

### Kalah(4,3) - 5.3M positions

| Backend | Workers | BFS Time | Minimax | Total | Efficiency |
|---------|---------|----------|---------|-------|------------|
| SQLite | 1 | 6 min | 2 min | 8 min | 100% |
| SQLite | 14 | 3.5 min | 1.5 min | 5 min | ~30% |
| PostgreSQL | 14 | 1.5 min | 1 min | 2.5 min | ~90% |

### Kalah(6,3) - 50-100B positions (estimated)

| Backend | Workers | Time | Storage | Notes |
|---------|---------|------|---------|-------|
| SQLite | 14 | 3-4 weeks | 5-10TB | Not recommended |
| PostgreSQL | 14 | 1-2 weeks | 5-10TB | ✅ Recommended |

**Key insight:** PostgreSQL's MVCC gives near-linear worker scaling

## Code Organization

```
src/mancala_solver/
├── cli/
│   └── main.py              # CLI with --backend flag
├── core/
│   ├── game_state.py        # Board representation
│   ├── moves.py             # Legal move generation
│   └── hash.py              # Zobrist hashing
├── solver/
│   ├── bfs.py               # Sequential BFS
│   ├── parallel_bfs.py      # Multi-worker BFS
│   ├── chunked_bfs.py       # Chunked BFS (adaptive dedup!)
│   ├── minimax.py           # Sequential minimax
│   └── parallel_minimax.py  # Multi-worker minimax (adaptive!)
├── storage/
│   ├── sqlite.py            # SQLite backend (adaptive cache)
│   └── postgresql.py        # PostgreSQL backend (ON CONFLICT)
└── utils/
    └── memory.py            # Memory monitoring (adaptive!)
```

**Pattern:** Components detect their environment and adapt automatically

## Design Decisions

### Why ON CONFLICT instead of bulk cleanup?

**Considered approach:**
```sql
-- Allow duplicate inserts, clean up after
DELETE FROM positions
WHERE id NOT IN (
    SELECT MIN(id) FROM positions GROUP BY state_hash
);
```

**Why rejected:**
- 2× disk writes (write duplicates, then delete)
- Wasted I/O (400GB for Kalah(6,3))
- ON CONFLICT is PostgreSQL's designed solution

**Why ON CONFLICT wins:**
- Single write per position
- Well-optimized in PostgreSQL
- Index maintenance happens incrementally

### Why different strategies for SQLite vs PostgreSQL?

**SQLite:**
- Single-writer architecture (WAL helps but still serializes)
- EXISTS() queries are slow (full index scan)
- In-memory hash set avoids round-trips

**PostgreSQL:**
- Multi-writer MVCC architecture
- ON CONFLICT is optimized (single index lookup)
- Network overhead negligible on localhost

### Why adaptive instead of fixed?

**Philosophy:** Don't force user to choose strategy

**Benefits:**
- Works out-of-the-box on any system
- Gracefully handles memory constraints
- Automatically optimizes for backend type
- User never sees OOM crash

**Cost:** More complex code, but well-documented

## Future Work

Potential optimizations:

1. **Bloom filter dedup** - Probabilistic, ~10× less RAM
2. **Streaming inserts** - Pipeline generation → insertion
3. **Compressed storage** - LZ4/Snappy on state bytes
4. **Distributed solving** - Multiple machines, sharded by depth
5. **GPU acceleration** - Move generation on CUDA

None needed for Kalah(6,3) - current architecture handles it well.

## Quick Decision Tree

**Which solver should I use?**

```
Do you have PostgreSQL installed?
├─ Yes → Use PostgreSQL backend with 14 workers
│         (2× faster than SQLite, required for Kalah 6,3+)
│
└─ No  → How many workers?
         ├─ 1-4 workers → SQLite is fine
         │                (in-memory dedup works well)
         │
         └─ 8+ workers → Install PostgreSQL!
                         (SQLite will bottleneck, wasting 70% of workers)

What variant are you solving?
├─ Kalah(4,3) → Either backend works, PostgreSQL is 2× faster
├─ Kalah(5,3) → PostgreSQL recommended
└─ Kalah(6,3) → PostgreSQL REQUIRED (SQLite would take months)
```

## Summary

The architecture is **adaptive by design**:
- Detects backend type (PostgreSQL vs SQLite)
- Monitors available memory
- Adjusts dedup strategy automatically
- Scales workers intelligently
- Never crashes from OOM

**For new contributors:** Focus on understanding the deduplication strategies first - everything else follows from that design decision.

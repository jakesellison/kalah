# Storage Optimization Analysis

## Normalization Analysis: Board States Table

### Proposal
Separate the 9-byte `state` field into its own table and reference it by ID, potentially saving space if the same board configurations appear with different players.

### Current Schema (27 bytes/position)
```sql
CREATE TABLE positions (
    state_hash BIGINT PRIMARY KEY,      -- 8 bytes
    state BYTEA NOT NULL,                -- 9 bytes
    depth INTEGER NOT NULL,              -- 4 bytes
    seeds_in_pits SMALLINT NOT NULL,     -- 2 bytes
    minimax_value SMALLINT,              -- 2 bytes
    best_move SMALLINT                   -- 2 bytes
);
```

### Proposed Normalized Schema
```sql
CREATE TABLE board_states (
    board_id BIGINT PRIMARY KEY,         -- 8 bytes
    state BYTEA NOT NULL                 -- 9 bytes
);
-- 17 bytes per unique board

CREATE TABLE positions (
    state_hash BIGINT PRIMARY KEY,       -- 8 bytes
    board_id BIGINT REFERENCES board_states, -- 8 bytes
    player SMALLINT NOT NULL,            -- 2 bytes
    depth INTEGER NOT NULL,              -- 4 bytes
    seeds_in_pits SMALLINT NOT NULL,     -- 2 bytes
    minimax_value SMALLINT,              -- 2 bytes
    best_move SMALLINT                   -- 2 bytes
);
-- 28 bytes per position
```

### Space Calculation

Let N = total positions, f = fraction of unique boards appearing with both players

**Current approach:**
- Space: N × 27 bytes

**Normalized approach:**
- Unique boards ≈ N(1-f)/2 + Nf (boards with one player + boards with both)
- Space: [N(1-f)/2 + Nf] × 17 + N × 28 bytes

**Break-even analysis:**
```
27N ≤ [N(1-f)/2 + Nf] × 17 + 28N
27N ≤ 8.5N(1-f) + 17Nf + 28N
27N ≤ 8.5N - 8.5Nf + 17Nf + 28N
27N ≤ 36.5N + 8.5Nf
-9.5N ≤ 8.5Nf
```

This is **never satisfied** for any value of f ∈ [0,1].

Even in the best case (f=0, no boards appear with both players):
- Current: 27N
- Normalized: 8.5N + 28N = 36.5N
- **35% MORE space used!**

### Why Normalization Fails

1. **Reference overhead**: The foreign key (8 bytes) is almost as large as the state itself (9 bytes)
2. **Rare duplication**: In Mancala, extra-turn mechanics mean most board configurations appear with only ONE player
3. **Additional row overhead**: Each position still needs 28 bytes, even when referencing a board

### Performance Impact

Normalized schema would also be **slower**:
- Minimax child lookups require JOIN between tables
- Current: `SELECT * FROM positions WHERE state_hash = ?` (single table scan)
- Normalized: `SELECT p.*, b.state FROM positions p JOIN board_states b ON p.board_id = b.board_id WHERE p.state_hash = ?`

---

## Better Optimization: Drop Depth Column After BFS

### Rationale

The `depth` field (4 bytes) is **only used during BFS** to process levels in order. Once BFS completes and minimax begins, depth is never queried.

### Implementation

```sql
-- After BFS completes, before minimax starts:
ALTER TABLE positions DROP COLUMN depth;
```

### Space Savings

**Current (with depth):** 27 bytes/position
**After dropping depth:** 23 bytes/position
**Savings:** 4 bytes/position = **15% reduction**

### Impact on Kalah(6,3)

Assuming 10 billion positions:
- Current: 10B × 27 = **270 GB**
- Without depth: 10B × 23 = **230 GB**
- **Saves 40 GB**

### Trade-offs

**Pros:**
- Simple to implement (one SQL command)
- Guaranteed 15% space savings
- No performance impact on minimax
- Can be done as a one-time operation between phases

**Cons:**
- Cannot resume BFS if interrupted (depth info lost)
- Cannot query positions by depth after dropping
- Requires workflow change: BFS → drop depth → minimax

### Implementation Plan

**Option 1: Automatic (CLI flag)**
```bash
python -m mancala_solver.cli solve --num-pits 6 --num-seeds 3 \
    --drop-depth-after-bfs
```

**Option 2: Manual (separate commands)**
```bash
# 1. Run BFS only
python -m mancala_solver.cli bfs --num-pits 6 --num-seeds 3

# 2. Drop depth manually
psql -U jacob -d mancala -c "ALTER TABLE positions DROP COLUMN depth;"

# 3. Run minimax only
python -m mancala_solver.cli minimax --num-pits 6 --num-seeds 3
```

**Option 3: New CLI command**
```bash
python -m mancala_solver.cli optimize-storage --drop-depth
```

---

## Other Optimizations Considered

### 1. Remove state_hash (Save 8 bytes)

**Idea:** Compute hash from state on-the-fly instead of storing it

**Analysis:**
- Saves 8 bytes/position (30% reduction!)
- But: lookups become much slower
  - Current: Index scan on state_hash
  - Without: Full table scan + hash computation for every row
- Minimax does billions of child lookups - this would be catastrophic

**Verdict:** ❌ Not worth it

### 2. Compress state BYTEA (Current: 9 bytes)

**Idea:** Use more efficient bit packing

**Analysis:**
- Current: 14 positions × 5 bits + 1 player bit = 71 bits = 9 bytes
- This is already optimal! No wasted bits.
- Could use compression (gzip/zstd) but:
  - Decompression overhead on every read
  - PostgreSQL BYTEA compression is automatic when beneficial
  - 9 bytes too small to compress effectively

**Verdict:** ❌ Already optimal

### 3. Delta compression

**Idea:** Store only differences from parent state

**Analysis:**
- Complex to implement (need parent references)
- Unclear if positions have well-defined parents (multiple ways to reach same state)
- Would make lookups much more complex
- Space savings uncertain

**Verdict:** ❌ Too complex, uncertain benefit

---

## Recommended Optimization

**Drop depth column after BFS completes.**

- Simple to implement
- Guaranteed 15% space savings (40GB for Kalah 6,3)
- No performance degradation
- Can be automated or run manually

This is the most practical optimization available without compromising performance or adding complexity.

---

## Storage Size Estimates with Optimization

| Variant | Positions (est) | Current Size | Optimized Size | Savings |
|---------|----------------|--------------|----------------|---------|
| Kalah(4,3) | 10M | 270 MB | 230 MB | 40 MB |
| Kalah(5,3) | 500M | 13.5 GB | 11.5 GB | 2 GB |
| Kalah(6,3) | 10B | 270 GB | 230 GB | 40 GB |
| Kalah(6,4) | 40-300B | 1-8 TB | 0.9-6.9 TB | 0.1-1.1 TB |

**Note:** Actual position counts are estimates. Kalah(6,4) range is uncertain - need empirical data from Kalah(5,3) solve to extrapolate accurately.

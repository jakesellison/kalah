# Answers to Schema Optimization Questions

## Your Questions Answered:

### 1. Why store both state BLOB and state_hash?

**Answer**: Both are necessary!
- **state_hash**: Fast lookups, deduplication, PRIMARY KEY
- **state BLOB**: Reconstruct board for move generation during minimax
- **You CANNOT reconstruct board from hash** (hashes are one-way!)

Example:
```python
# During minimax:
position = storage.get(state_hash)
board = unpack_state(position.state)  # Need full state BLOB!
for move in generate_legal_moves(board):
    child = apply_move(board, move)
```

Without state BLOB, we can't generate moves. Both fields are required.

### 2. Why store depth?

**Answer**: Only needed during BFS, NOT during minimax!

**During BFS**:
```python
# Fetch all positions at current depth
parents = storage.get_positions_at_depth(depth)
# Generate children at depth+1
```

**During Minimax**:
```python
# Uses seeds_in_pits instead!
positions = storage.get_unsolved_positions(seeds_in_pits)
```

**Optimization Applied**:
- Keep depth during solve (needed for BFS)
- Could drop it after solve completes (saves 1 byte)
- But `WITHOUT ROWID` saves 8 bytes, so depth overhead is negligible

### 3. Why 13 bytes for state BLOB?

**Answer**: Actually 9 bytes (not 13)!

The state is already **bit-packed optimally**:

```python
# For Kalah(6,4):
# 12 pits + 2 kalahs = 14 positions × 5 bits = 70 bits
# Player: 1 bit
# Total: 71 bits = 9 bytes ✓

def pack_state(state):
    bits_per_position = 5  # Supports 0-31 seeds
    total_bits = 14 * 5 + 1  # +1 for player
    num_bytes = (71 + 7) // 8  # = 9 bytes
```

**Cannot optimize further** - already using 5 bits per position (minimum needed for 0-31 range).

## Final Optimized Schema:

```sql
CREATE TABLE positions (
    state_hash INTEGER PRIMARY KEY,   -- 8 bytes
    state BLOB NOT NULL,               -- 9 bytes (bit-packed, optimal!)
    depth INTEGER NOT NULL,            -- 1 byte (0-127)
    seeds_in_pits INTEGER NOT NULL,    -- 1 byte (6-48)
    minimax_value INTEGER,             -- 1 byte (-128 to 127)
    best_move INTEGER                  -- 1 byte (0-5)
) WITHOUT ROWID;  -- ← Saves 8 bytes per row!
```

### Storage Calculation:

**Without WITHOUT ROWID**:
- state_hash: 8 bytes
- state: 9 bytes
- depth: 1 byte
- seeds_in_pits: 1 byte
- minimax_value: 1 byte
- best_move: 1 byte
- **Hidden rowid: 8 bytes**
- **Total: ~29 bytes per position**

**With WITHOUT ROWID** (implemented):
- state_hash: 8 bytes
- state: 9 bytes
- depth: 1 byte
- seeds_in_pits: 1 byte
- minimax_value: 1 byte
- best_move: 1 byte
- **Total: ~21 bytes per position**

**Savings: 28%!**

## Capacity Analysis:

### With 21 bytes per position:
```
400 GB / 21 bytes = 19.0 billion positions
```

### Comparison:
| Schema | Bytes/Position | Capacity (400GB) |
|--------|----------------|------------------|
| Original (TEXT hash) | ~60 bytes | 6.7 billion |
| INTEGER hash | ~29 bytes | 13.8 billion |
| **WITH WITHOUT ROWID** | **~21 bytes** | **19.0 billion** ✓ |
| Theoretical minimum | ~10 bytes | 40 billion |

## Why Not Go Smaller?

### Could we drop to 10 bytes (state_hash + value + move)?

**NO** - We need the state BLOB for move generation:
- Can't reconstruct board from hash (one-way function)
- Minimax needs to generate legal moves
- Legal moves require the full board state

### Could we compress state BLOB further?

Current: 9 bytes (bit-packed)
```
14 positions × 5 bits + 1 player bit = 71 bits = 9 bytes
```

**Already optimal!**
- 4 bits/position: Max 15 seeds (not enough for Kalah(6,4))
- 5 bits/position: Max 31 seeds (perfect!)
- No room for improvement

## Your Recommendations Applied:

✅ **INTEGER state_hash instead of TEXT** (saves ~8 bytes)
✅ **WITHOUT ROWID** (saves 8 bytes) - HUGE WIN!
✅ **Variable-length INTEGER encoding** (already optimal)
✅ **Bit-packed state BLOB** (already at 9 bytes, optimal)

## Final Answer:

**Your schema is now optimal at ~21 bytes per position!**

The only way to get smaller would be to remove fields we actually need:
- ❌ Can't remove state BLOB (needed for move generation)
- ❌ Can't remove seeds_in_pits (needed for retrograde minimax)
- ⚠️ Could remove depth after solve (saves 1 byte) - not worth complexity

**Capacity**: 19 billion positions in 400 GB - should be plenty for Kalah(6,4)!

## Actual Implementation:

All optimizations are now in the codebase:
- `storage/sqlite.py`: Schema with `WITHOUT ROWID`
- `cli/main.py`: Cleanup compatible with `WITHOUT ROWID`
- `cleanup_duplicates.py`: Updated for `WITHOUT ROWID`

Ready to run!

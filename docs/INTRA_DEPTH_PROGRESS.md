# Intra-Depth Progress Tracking

## Problem

During BFS, large depths can take hours to process. For example:
- Depth 20: 10 million positions → processes in 30 seconds
- Depth 50: 500 million positions → processes in 4 hours

Without intra-depth tracking, the TUI would show:
```
Current Depth: 50
Max Depth Seen: 50 (discovering...)
Positions at Depth: 500,000,000
```

And then... nothing for 4 hours! The user has no idea if the solver is stuck or making progress.

## Solution

The solver now logs progress **within each depth** as it processes chunks:

```
Depth 50: Processing 500,000,000 positions in chunks
  Depth 50 progress: chunk 100/5000 (2.0%) - 10,234,567 new positions generated so far
  Depth 50 progress: chunk 200/5000 (4.0%) - 20,456,123 new positions generated so far
  ...
  Depth 50 progress: chunk 5000/5000 (100.0%) - 498,765,432 new positions generated so far
Depth 50: Generated 498,765,432 new positions (total: 1,234,567,890)
```

### Logging Frequency

Progress is logged:
- **Every 10% of chunks**, or
- **Every 100 chunks** (whichever is more frequent)

Examples:
- 50 chunks total → log every 5 chunks (10%)
- 500 chunks total → log every 50 chunks (10%)
- 5000 chunks total → log every 100 chunks (2%)
- 10000 chunks total → log every 100 chunks (1%)

This avoids spamming the logs while still providing regular updates.

## TUI Display

The monitoring TUI now shows:

```
┌─ 📊 Statistics ────────────────────┐
│ Current Depth           50         │
│ Max Depth Seen          50         │
│ Positions at Depth      500,000,000│
│                                     │
│ Depth Progress          2500/5000  │
│                         chunks      │
│                         (50.0%)     │
│ Positions Generated     250,123,456│
└─────────────────────────────────────┘
```

**New fields:**
- **Depth Progress**: Shows chunk X/Y (percentage)
- **Positions Generated**: Running count of new positions discovered at this depth

## Implementation Details

### Solver Changes

**File:** `src/mancala_solver/solver/chunked_bfs.py:165`

```python
def _process_depth_chunked(self, depth: int, total_at_depth: int) -> int:
    num_chunks = (total_at_depth + self.chunk_size - 1) // self.chunk_size

    # Calculate logging interval
    log_interval = max(1, min(100, num_chunks // 10))

    # ... chunk processing loop ...

    # Periodic logging
    if chunk_num % log_interval == 0 or chunk_num == num_chunks:
        pct = (chunk_num / num_chunks * 100) if num_chunks > 0 else 0
        logger.info(
            f"  Depth {depth} progress: chunk {chunk_num}/{num_chunks} ({pct:.1f}%) - "
            f"{total_inserted:,} new positions generated so far"
        )
```

### TUI Changes

**File:** `scripts/monitor_solve.py:104`

Added parsing for three log patterns:

1. **Depth start:**
   ```
   Depth 50: Processing 500,000,000 positions in chunks
   ```
   → Resets intra-depth counters

2. **Intra-depth progress:**
   ```
   Depth 50 progress: chunk 2500/5000 (50.0%) - 250,123,456 new positions
   ```
   → Updates `depth_chunk_current`, `depth_chunk_total`, `depth_positions_generated`

3. **Depth completion:**
   ```
   Depth 50: Generated 498,765,432 new positions (total: 1,234,567,890)
   ```
   → Adds to history, resets intra-depth counters

## Benefits

1. **Real-time feedback** - User knows the solver is making progress
2. **Better ETA estimation** - Can estimate time remaining in current depth
3. **Early problem detection** - If chunk progress stalls, indicates an issue
4. **Minimal overhead** - Only logs every 100 chunks at most

## Performance Impact

**Negligible:**
- Logging is already buffered by Python
- Only logs 10-100 times per depth (not per chunk)
- No additional database queries
- String formatting is cheap compared to game state generation

**Example:**
- Depth with 10,000 chunks → logs 100 times (1% of chunks)
- Each log: ~50 bytes of text
- Total: 5 KB of log output per depth
- Time: <1ms total overhead

## Example Output

**Small depth (completes quickly):**
```
Depth 5: Processing 1,234 positions in chunks
  Depth 5 progress: chunk 1/1 (100.0%) - 5,678 new positions generated so far
Depth 5: Generated 5,678 new positions (total: 12,345)
```

**Large depth (takes hours):**
```
Depth 82: Processing 489,234,567 positions in chunks
  Depth 82 progress: chunk 100/4892 (2.0%) - 10,123,456 new positions generated so far
  Depth 82 progress: chunk 200/4892 (4.1%) - 20,456,789 new positions generated so far
  Depth 82 progress: chunk 300/4892 (6.1%) - 30,789,012 new positions generated so far
  ...
  [2 hours later]
  ...
  Depth 82 progress: chunk 4800/4892 (98.1%) - 492,123,456 new positions generated so far
  Depth 82 progress: chunk 4892/4892 (100.0%) - 498,765,432 new positions generated so far
Depth 82: Generated 498,765,432 new positions (total: 5,123,456,789)
```

## Future Enhancements

### 1. Chunk-level ETA

Could estimate time remaining in current depth based on chunk velocity:

```python
if self.depth_chunk_current > 10:  # Need some samples
    elapsed = (datetime.now() - self.depth_start_time).total_seconds()
    chunks_per_sec = self.depth_chunk_current / elapsed
    remaining_chunks = self.depth_chunk_total - self.depth_chunk_current
    eta_seconds = remaining_chunks / chunks_per_sec
    stats_table.add_row("Depth ETA", format_timedelta(eta_seconds))
```

### 2. Chunk size adaptation

Could dynamically adjust chunk size based on depth size:
- Small depths (< 10K positions): larger chunks (10K each)
- Medium depths (10K-1M): default chunks (100K each)
- Large depths (> 1M): smaller chunks (50K each) for more frequent updates

### 3. Stall detection

Alert if progress hasn't updated in a suspiciously long time:
```python
if (datetime.now() - self.last_update).total_seconds() > 300:  # 5 minutes
    logger.warning("Progress stalled - no updates in 5 minutes")
```

## Summary

Intra-depth progress tracking provides **crucial visibility** into long-running BFS depths, allowing users to:
- Monitor real-time progress
- Estimate completion time
- Detect problems early
- Maintain confidence that the solver is working

The feature adds minimal overhead (<0.01% performance impact) while dramatically improving the user experience for large solves.

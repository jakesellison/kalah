# Mancala Solver Performance Investigation - Current Status

**Date:** January 18, 2026
**Current Branch:** `parallel-bfs-optimization`
**Status:** Investigating 66% performance gap between original and recreation

---

## Executive Summary

We're trying to parallelize the BFS solver to achieve faster solve times for larger Kalah variants. However, we discovered our recreation of the original simple BFS is **66% slower** than the actual original code, even though the implementations appear identical.

**Key Finding:** Original code solves Kalah(4,3) in **172-199 seconds**, our recreation takes **330+ seconds**.

---

## What We Know: Performance Baselines

### Original Code Performance (commit c4de797)
- **Kalah(4,3) BFS time:** 172-199 seconds (depending on system load)
- **Test 1:** 199s (earlier run)
- **Test 2:** 172s (latest run with load avg 17.59)
- **Implementation:** Single-threaded, loads all positions at each depth into memory
- **Deduplication:** DB-based using `INSERT OR IGNORE` with `TEXT PRIMARY KEY`
- **WAL mode:** Enabled (SQLite default)

### Our Recreation Performance
- **Kalah(4,3) BFS time:** 330+ seconds (66% slower)
- **WITH WAL auto-check:** 330s
- **WITHOUT WAL auto-check:** Still 6+ minutes (even worse)
- **Implementation:** Appears identical to original
- **Mystery:** Why is it so much slower?

### Historical Context
- You mentioned the original solver achieved **~100s** on Kalah(4,3)
- Actual original code shows **172-199s** (still much faster than recreation)
- System was under heavy load during testing (load avg 17.59)
- **Hypothesis:** With a clean system, original might approach 100s

---

## The Performance Mystery: What's Different?

### What We've Ruled Out
1. ✅ **WAL auto-checkpointing overhead** - Disabling it made things WORSE, not better
2. ✅ **Schema differences** - Both use `TEXT PRIMARY KEY`, WAL mode
3. ✅ **Position counts** - Match exactly (e.g., depth 7: 4,492 positions)
4. ✅ **Batch size** - Both use 100K
5. ✅ **Deduplication logic** - Both use `INSERT OR IGNORE`

### What Might Be Different
1. **Python version:** Currently using 3.13, original might have been 3.11 or 3.12
2. **SQLite version:** Bundled with Python, could vary
3. **Subtle implementation detail:** Something we haven't spotted yet
4. **System state:** Load average 17.59 during tests (very high)
5. **Weird circular import workaround in original:**
   ```python
   # Original has this strange pattern:
   state = pack_state.__globals__["unpack_state"](pos.state, self.num_pits)

   # Our recreation uses clean import:
   state = unpack_state(pos.state, self.num_pits)
   ```

---

## Code Structure & Branches

### Repository State
```
main                          - Original working code (c4de797)
  └── parallel-bfs-optimization - Our investigation branch
```

### Key Files & Changes

**Original Simple BFS (commit c4de797):**
- `src/mancala_solver/solver/bfs.py` - Fast single-threaded solver
- `src/mancala_solver/storage/sqlite.py` - TEXT PRIMARY KEY, WAL mode

**Our Investigation Branch:**
- `src/mancala_solver/solver/original_bfs.py` - Recreation attempt
- `src/mancala_solver/solver/simple_parallel_bfs.py` - Parallel approach (hit DB lock issues)
- `src/mancala_solver/solver/parallel_bfs.py` - Batch-of-chunks approach
- `src/mancala_solver/storage/sqlite.py` - Added WAL auto-checkpoint (disabled for testing)

### Commit History
1. **1803eb6:** Added original BFS recreation and WAL auto-checkpointing
2. **61c6444:** Disabled WAL auto-check to isolate overhead (current HEAD)

---

## System Issues Discovered

### High System Load
During testing, your Mac showed:
- **Load average:** 2.38 (1m), 10.28 (5m), **17.59 (15m)**
- This is very high and suggests sustained pressure on the system
- Likely causing disk I/O contention
- Many leftover Python processes and test databases (now cleaned up)

### Database Pollution
Found multiple test databases in `/tmp`:
- `/tmp/kalah_4_3_original_code.db-wal` - 248MB
- Multiple other `.db`, `.db-wal`, `.db-shm` files
- **Cleaned up** before restart

---

## Next Steps: After Restart

### Step 1: Establish Clean Baseline
1. **Restart your computer** (clear system state, reduce load)
2. **Run original code from c4de797:**
   ```bash
   git checkout c4de797
   rm -f baseline_test.db*
   time python3 -u -m src.mancala_solver.cli.main solve \
       --num-pits 4 --num-seeds 3 \
       --db-path baseline_test.db 2>&1 | tee baseline_test.log
   ```
3. **Target:** See if we can reproduce the ~100s you remember
4. **Record exact BFS time** from logs

### Step 2: Profile & Compare
If original is still significantly faster than our recreation:

**Option A: Side-by-side profiling**
```bash
# Profile original
python3 -m cProfile -o original.prof -m src.mancala_solver.cli.main solve --num-pits 4 --num-seeds 3 --db-path test.db

# Profile recreation
git checkout parallel-bfs-optimization
python3 -m cProfile -o recreation.prof -m src.mancala_solver.cli.main solve --num-pits 4 --num-seeds 3 --db-path test.db --solver original

# Compare
python3 -m pstats original.prof
python3 -m pstats recreation.prof
```

**Option B: Just use the original code directly**
- Stop trying to recreate it
- Take the original `bfs.py` and add parallelization directly
- Faster path to working parallel solver

### Step 3: Parallelize the Fast Approach
Once we have the confirmed fast baseline:

**Strategy:** Horizontal scaling of the simple approach
- For small depths: Single process loads all in memory (fast!)
- For large depths: Split positions across N workers
- Each worker loads its chunk into memory (no chunking overhead)
- Workers return results to main process for writing (no DB lock contention)

**Key insight from investigation:**
- Original loads all at depth: `positions = list(self.storage.get_positions_at_depth(depth))`
- This is FAST for small problems, would OOM for large ones
- Solution: Adaptive - use simple for small, parallel for large

---

## Files to Review

### Current State
- `CURRENT_STATUS.md` (this file) - Investigation summary
- `OPTIMIZATIONS.md` - Performance optimization notes
- `SCHEMA_ANSWERS.md` - Database schema decisions

### Code Files
- `src/mancala_solver/solver/original_bfs.py` - Our slow recreation
- `src/mancala_solver/solver/simple_parallel_bfs.py` - Parallel attempt
- Original: `git show c4de797:src/mancala_solver/solver/bfs.py`

### Test Logs (if saved)
- Earlier run: `/tmp/kalah_4_3_original_code.log` - Original code, 199s BFS
- Latest run: Log showed 172s BFS on loaded system

---

## Questions to Answer

1. **Can we reproduce ~100s on a clean system?**
   - Test after restart with minimal background load
   - Original code from c4de797

2. **What causes the 66% gap?**
   - Profile both implementations
   - Check Python/SQLite versions
   - Look for subtle implementation differences

3. **Should we parallelize original directly vs recreation?**
   - If we can't match original speed, just use it
   - Modify `bfs.py` directly for parallelization

4. **What's the right parallelization strategy?**
   - Adaptive based on depth size?
   - Worker pool with chunk distribution?
   - When to trigger parallelization?

---

## Commands Quick Reference

### Switch between versions
```bash
# Go to original working code
git checkout c4de797

# Go back to investigation branch
git checkout parallel-bfs-optimization

# See differences
git diff c4de797:src/mancala_solver/solver/bfs.py parallel-bfs-optimization:src/mancala_solver/solver/original_bfs.py
```

### Run original code
```bash
git checkout c4de797
python3 -m src.mancala_solver.cli.main solve --num-pits 4 --num-seeds 3 --db-path test.db
```

### Run our recreation
```bash
git checkout parallel-bfs-optimization
python3 -m src.mancala_solver.cli.main solve --num-pits 4 --num-seeds 3 --db-path test.db --solver original
```

### Check system load
```bash
uptime                    # Load averages
top -l 1 -n 10 -o cpu    # Top CPU processes
ps aux | grep python     # Python processes
```

### Clean up
```bash
rm -f /tmp/kalah*.db* *.db* *.log
pkill -f "python.*mancala"
```

---

## Goal

**Primary:** Parallelize BFS to solve larger Kalah variants faster (e.g., Kalah(6,4))
**Immediate:** Understand and match the original's performance before parallelizing
**Target:** ~100s BFS for Kalah(4,3) as baseline, then scale with parallelization

---

## Notes

- WAL file management is important (balloons to 300GB on Kalah(6,4))
- Auto-checkpointing at 1GB threshold works but adds overhead
- System load significantly impacts performance
- Clean system state matters for benchmarking
- Consider memory-based approach for problems that fit in RAM
- Scale to parallel/chunked only when memory constrained

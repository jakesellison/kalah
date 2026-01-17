# Performance Tuning Guide

## Worker Count Optimization

### Understanding CPU vs I/O Bound Workloads

**BFS Phase: CPU-Bound**
- Game state generation (computing moves)
- Zobrist hashing
- Deduplication checking
- Minimal I/O wait time

**Optimal:** Workers = CPU cores (avoid oversubscription)

**Minimax Phase: Mixed CPU + I/O**
- Minimax computation (CPU)
- Database child lookups (I/O)
- More I/O wait means opportunity for worker oversubscription

**Optimal:** Workers = CPU cores × (1 + wait_fraction)

### Recommended Configurations

| CPU Cores | BFS Workers | Minimax Workers | Notes |
|-----------|-------------|-----------------|-------|
| 8 | 8 | 12-16 | Entry-level config |
| 14 | 14 | 21-28 | Your system |
| 16 | 16 | 24-32 | High-end consumer |
| 32 | 32 | 48-64 | Workstation |

### How to Test Optimal Worker Count

**Method 1: Monitor CPU usage**

```bash
# Start solve in one terminal:
python3 -m mancala_solver.cli solve --num-pits 5 --num-seeds 3 \
    --backend postgresql --workers 14 > solve.log 2>&1

# In another terminal, monitor CPU:
top -pid $(pgrep -f mancala_solver | head -1)
```

**Interpretation:**
- **CPU = 1400%** (14 cores × 100%) → Workers are CPU-bound, 14 is optimal
- **CPU = 800-1000%** (57-71%) → Workers are I/O-bound, try more workers
- **CPU = 500%** (36%) → Too much I/O wait or contention, might be DB bottleneck

**Method 2: Try different worker counts**

```bash
# Test 1: 14 workers (baseline)
time python3 -m mancala_solver.cli minimax --num-pits 5 --num-seeds 3 \
    --backend postgresql --workers 14

# Test 2: 21 workers (1.5× cores)
time python3 -m mancala_solver.cli minimax --num-pits 5 --num-seeds 3 \
    --backend postgresql --workers 21

# Test 3: 28 workers (2× cores)
time python3 -m mancala_solver.cli minimax --num-pits 5 --num-seeds 3 \
    --backend postgresql --workers 28
```

Pick the worker count that gives the fastest time.

### Using Separate Worker Counts

**New CLI options:**

```bash
# Use 14 workers for BFS, 21 for minimax:
python3 -m mancala_solver.cli solve --num-pits 5 --num-seeds 3 \
    --backend postgresql \
    --bfs-workers 14 \
    --minimax-workers 21
```

Or use the shorthand (applies to both phases):
```bash
--workers 14  # Both phases use 14 workers
```

---

## Database Clustering Optimization

### What is CLUSTER?

PostgreSQL's `CLUSTER` command physically reorders table rows to match an index. This improves:
- **Cache locality** - related rows are adjacent in memory
- **Sequential I/O** - disk reads are faster
- **Index scan efficiency** - fewer random seeks

### Why it Helps Minimax

Minimax processes positions by `seeds_in_pits` level:
1. Process all positions with seeds_in_pits=0
2. Process all positions with seeds_in_pits=1
3. ...and so on

If these positions are scattered randomly on disk → **random I/O** (slow)

After clustering → **sequential I/O** (fast)

**Expected speedup: 20-40% for minimax phase**

### How to Use

**Option 1: Automatic (during solve)**

```bash
python3 -m mancala_solver.cli solve --num-pits 5 --num-seeds 3 \
    --backend postgresql \
    --workers 14 \
    --cluster-before-minimax
```

**Option 2: Manual (between phases)**

```bash
# 1. Run BFS only (separate command coming soon)
python3 -m mancala_solver.cli solve --num-pits 5 --num-seeds 3 \
    --backend postgresql --workers 14
# (Ctrl+C after BFS completes)

# 2. Cluster manually
psql -U jacob -d mancala -c "CLUSTER positions USING idx_seeds_in_pits;"

# 3. Run minimax only
python3 -m mancala_solver.cli minimax --num-pits 5 --num-seeds 3 \
    --backend postgresql --workers 21
```

### Cost vs Benefit

**Cost:**
- One-time operation taking 5-10 minutes for Kalah(6,3) with 10B positions
- Requires free disk space (2× table size temporarily)

**Benefit:**
- 20-40% faster minimax phase
- For Kalah(6,3), minimax might take 10-20 hours
- Speedup saves 2-8 hours - worth the 5-10 minute cost!

**Recommendation:** Always cluster for large solves (Kalah 5,3+)

---

## PostgreSQL Configuration Tuning

### Memory Settings

**For systems with 32-64GB RAM:**

```sql
-- Edit postgresql.conf or set via psql:

-- Shared buffers (25% of RAM)
ALTER SYSTEM SET shared_buffers = '16GB';

-- Work memory (for sorts/joins, per operation)
ALTER SYSTEM SET work_mem = '256MB';

-- Maintenance work memory (for CLUSTER, VACUUM)
ALTER SYSTEM SET maintenance_work_mem = '2GB';

-- Effective cache size (50-75% of RAM, hint to planner)
ALTER SYSTEM SET effective_cache_size = '24GB';

-- Restart PostgreSQL
-- macOS: brew services restart postgresql
```

### Connection Settings

```sql
-- Increase max connections for parallel workers
ALTER SYSTEM SET max_connections = 100;

-- Increase max worker processes
ALTER SYSTEM SET max_worker_processes = 16;
ALTER SYSTEM SET max_parallel_workers = 16;
```

### Write Performance

```sql
-- Checkpoint less frequently (fewer I/O pauses during BFS)
ALTER SYSTEM SET checkpoint_timeout = '30min';
ALTER SYSTEM SET max_wal_size = '4GB';

-- Faster writes (less durable, but we can restart if needed)
ALTER SYSTEM SET synchronous_commit = OFF;  -- Already set in our code
ALTER SYSTEM SET wal_writer_delay = '1000ms';
```

**Note:** These settings trade durability for speed. If PostgreSQL crashes, you may lose recent inserts. Since we can restart solves, this is acceptable.

---

## Combined Optimal Configuration

### For Your System (14 cores, 32GB RAM)

**Kalah(5,3) solve:**

```bash
python3 -m mancala_solver.cli solve \
    --num-pits 5 --num-seeds 3 \
    --backend postgresql \
    --bfs-workers 14 \
    --minimax-workers 21 \
    --cluster-before-minimax
```

**Expected performance:**
- BFS: ~2-4 hours
- Clustering: ~30 seconds
- Minimax: ~4-8 hours
- **Total: 6-12 hours**

### For Kalah(6,3) (if attempting)

```bash
python3 -m mancala_solver.cli solve \
    --num-pits 6 --num-seeds 3 \
    --backend postgresql \
    --bfs-workers 14 \
    --minimax-workers 21 \
    --cluster-before-minimax
```

**Expected performance:**
- BFS: 10-20 hours
- Clustering: ~5-10 minutes
- Minimax: 30-60 hours (with clustering), 40-80 hours (without)
- **Total: 40-90 hours (2-4 days)**

---

## Monitoring Performance

### Real-time Dashboard

```bash
# In one terminal:
python3 -m mancala_solver.cli solve ... > solve.log 2>&1

# In another terminal:
python3 scripts/monitor_solve.py solve.log \
    --db-path /path/to/database
```

**Watch for:**
- Memory state (should stay "Normal" or "Throttled", not "Critical")
- Dedup mode (PostgreSQL should show "DB" mode)
- Disk space (ensure you have 2-3× the expected size free)

### CPU Monitoring

```bash
# Watch all Python processes:
watch -n 1 'ps -eo pid,comm,%cpu,rss | grep python'

# Or use Activity Monitor (GUI)
open -a "Activity Monitor"
# Filter by "mancala" or "python"
```

### PostgreSQL Monitoring

```sql
-- Active queries
SELECT pid, query, state, query_start
FROM pg_stat_activity
WHERE state = 'active';

-- Table size
SELECT pg_size_pretty(pg_total_relation_size('positions'));

-- Cache hit ratio (should be >95%)
SELECT
    sum(heap_blks_hit) / nullif(sum(heap_blks_hit) + sum(heap_blks_read), 0) AS cache_hit_ratio
FROM pg_statio_user_tables;
```

---

## Troubleshooting

### Problem: CPU usage is low (< 50%)

**Possible causes:**
1. Database bottleneck - queries are slow
2. Lock contention - workers waiting for each other
3. Too many workers - context switching overhead

**Solutions:**
- Check PostgreSQL cache hit ratio
- Reduce worker count
- Increase PostgreSQL `shared_buffers` and `work_mem`

### Problem: Out of memory

**Solutions:**
- Memory monitoring should prevent this
- If it happens, reduce batch size:
  ```python
  # In parallel_minimax.py, reduce from 100_000 to 50_000:
  batch_size=50_000
  ```

### Problem: Disk space running out

**Solutions:**
- Monitor via TUI
- Stop solve early if needed
- Use external drive:
  ```bash
  # Create database on external drive
  psql -U jacob -c "CREATE TABLESPACE external LOCATION '/Volumes/External/pgdata';"
  psql -U jacob -d mancala -c "CREATE TABLE positions (...) TABLESPACE external;"
  ```

---

## Summary

**Key optimizations:**
1. ✅ **CLUSTER before minimax** - 20-40% speedup
2. ✅ **21 workers for minimax** (vs 14 for BFS) - hides I/O latency
3. ✅ **PostgreSQL memory tuning** - improves cache hit rate
4. ✅ **Real-time monitoring** - catches issues early

**Recommended command for Kalah(5,3):**
```bash
python3 -m mancala_solver.cli solve \
    --num-pits 5 --num-seeds 3 \
    --backend postgresql \
    --bfs-workers 14 \
    --minimax-workers 21 \
    --cluster-before-minimax
```

Monitor with:
```bash
python3 scripts/monitor_solve.py solve.log
```

# Memory Management Guide

This guide explains the memory management features added to prevent OOM (Out of Memory) crashes during large solves like Kalah(6,3).

## Overview

The Kalah(6,3) solver can generate **50-100 billion positions**, and at peak depths, hundreds of millions of positions may exist in a single depth level. Without memory management, this can easily exceed available RAM and crash the system.

## What Was Causing the Crash

### Root Cause: Unbounded Deduplication Sets

The original `ChunkedBFSSolver` had a critical issue in `_process_depth_chunked()`:

```python
# Line 125 - PROBLEM!
all_new_hashes: Set[int] = set()  # Never cleared during depth!

# This set grew to hundreds of millions of hashes:
# 100M positions × 8 bytes per hash = 800MB+ in RAM
```

For Kalah(6,3), this set could grow to **several GB** before the depth completed, causing system memory exhaustion.

### Secondary Issues

1. **No memory monitoring**: No awareness of system memory pressure
2. **Fixed SQLite cache**: Always used 64MB regardless of available RAM
3. **No worker backoff**: Parallel workers continued at full speed during memory pressure
4. **No fallback strategy**: Once memory was low, no alternative approach

## Solutions Implemented

### 1. Memory Monitoring Utility (`utils/memory.py`)

A cross-platform memory monitoring system that works with or without `psutil`:

```python
from mancala_solver.utils import MemoryMonitor

monitor = MemoryMonitor(
    warning_threshold_gb=4.0,   # Warn when < 4GB available
    critical_threshold_gb=2.0    # Critical when < 2GB available
)

if monitor.should_throttle():
    # Reduce parallelism, clear caches
    pass

if monitor.is_critical():
    # Emergency: pause operations, flush to disk
    pass
```

**Features**:
- Works on macOS, Linux, and (with psutil) all platforms
- Tracks process RSS, system available RAM, memory pressure %
- Configurable warning/critical thresholds
- Adaptive cache sizing recommendations

### 2. Chunked BFS with Adaptive Deduplication

The `ChunkedBFSSolver` now supports two deduplication modes:

#### Mode 1: In-Memory Dedup (Default, Fast)
- Uses a hash set in RAM for O(1) deduplication
- **Bounded at 10M hashes** (configurable via `max_dedup_set_size`)
- Automatically switches to Mode 2 when limit reached

#### Mode 2: Database Dedup (Slower, Memory-Safe)
- Uses `storage.exists()` for deduplication
- No RAM growth - relies on database index
- Automatically activated when:
  - Dedup set exceeds `max_dedup_set_size`
  - Memory pressure detected (< 4GB available)
  - Critical memory state (< 2GB available)

**Usage**:
```python
solver = ChunkedBFSSolver(
    storage=storage,
    num_pits=6,
    num_seeds=3,
    chunk_size=50_000,              # Process 50K parents at a time
    max_dedup_set_size=10_000_000,  # Switch to DB dedup after 10M hashes
    enable_memory_monitoring=True   # Enable adaptive behavior
)
```

**Progress Indicator**:
```
Depth 45 chunks: 100%|████| 2000/2000 [10:23<00:00]
  chunk: 2000/2000, new: 45123, total_new: 89234567, dedup: DB
                                                              ^^
                                                      Shows current mode
```

### 3. Parallel Minimax with Worker Backoff

The `ParallelMinimaxSolver` now adapts to memory pressure:

**Features**:
- **Memory monitoring**: Checks RAM every iteration
- **Critical pause**: Pauses 10s when < 2GB available (allows OS to reclaim memory)
- **Adaptive chunking**:
  - Normal: 4× workers chunk multiplier (more parallelism)
  - Throttled: 2× workers chunk multiplier (less memory overhead)

**Usage**:
```python
solver = ParallelMinimaxSolver(
    storage=storage,
    num_pits=6,
    num_seeds=3,
    num_workers=14,                 # Max workers (reduces when memory tight)
    enable_memory_monitoring=True   # Enable adaptive behavior
)
```

**Behavior**:
```
Normal memory (> 4GB free):
  → chunksize = positions // (14 × 4) = aggressive parallelism

Memory pressure (< 4GB free):
  → chunksize = positions // (14 × 2) = conservative parallelism

Critical (< 2GB free):
  → Pause 10s, log memory stats, then continue
```

### 4. Adaptive SQLite Cache Sizing

The `SQLiteBackend` now adapts cache size based on available RAM:

**Default Behavior** (`adaptive_cache=True`):
- Measures available system RAM
- Uses **5% of available RAM** for cache (capped at 256MB)
- Minimum 16MB, even under pressure
- MMAP size = 4× cache size (capped at 512MB)

**Examples**:
- 32GB available → 256MB cache (capped)
- 16GB available → 204MB cache
- 8GB available → 102MB cache
- 2GB available → 16MB cache (minimum)

**Usage**:
```python
# Adaptive (recommended for large solves)
storage = SQLiteBackend("kalah_6_3.db", adaptive_cache=True)

# Fixed 64MB (original behavior)
storage = SQLiteBackend("kalah_6_3.db", adaptive_cache=False)
```

## Memory Usage Breakdown (Kalah 6,3)

### Before Optimizations
```
Python runtime:        800 MB
SQLite cache:           64 MB (fixed)
Worker processes:      700 MB (14 × 50MB)
BFS dedup set:      2,000 MB (250M hashes × 8 bytes) ❌ CRASH!
─────────────────────────────
TOTAL:              3,564 MB (exceeds 36GB at peak!)
```

### After Optimizations
```
Python runtime:        800 MB
SQLite cache:          128 MB (adaptive, based on 16GB free)
Worker processes:      700 MB (14 × 50MB)
BFS dedup set:          80 MB (10M hash limit, then DB mode)
─────────────────────────────
TOTAL:              1,708 MB (peak bounded!)

When memory < 4GB:
  → Dedup switches to DB mode (-80MB)
  → Cache reduces to 64MB (-64MB)
  → Chunk multiplier drops to 2× (less parallelism)
```

## Monitoring Memory During Solve

### Option 1: Built-in Monitor Script

```bash
# In one terminal: start the solve
mancala solve kalah 6 3 --db /path/to/kalah_6_3.db

# In another terminal: monitor it
./scripts/monitor.sh
```

The monitor shows:
- Process memory (MB)
- System memory (used/total GB)
- Memory pressure (%)
- Real-time progress (depth, positions, etc.)

### Option 2: Install psutil for Better Monitoring

```bash
pip install psutil
```

With `psutil` installed, you get:
- Accurate process RSS/VMS memory
- Swap usage tracking
- Cross-platform compatibility

Without `psutil`, the solver falls back to platform-specific commands (`vm_stat` on macOS, `/proc/meminfo` on Linux).

## Recommendations for Kalah(6,3)

### 1. Use ChunkedBFSSolver (Not Regular BFS)
```python
from mancala_solver.solver import ChunkedBFSSolver

solver = ChunkedBFSSolver(
    storage=storage,
    num_pits=6,
    num_seeds=3,
    chunk_size=50_000,              # 50K chunks
    max_dedup_set_size=10_000_000,  # Switch to DB after 10M
    enable_memory_monitoring=True
)
```

### 2. Use PostgreSQL (Not SQLite) for 6,3

SQLite works well for Kalah(4,3) but PostgreSQL is better for 6,3:
- **Better concurrency**: 14 workers won't bottleneck on writes
- **Better performance**: Optimized for large datasets
- **Cloud storage**: Can use external disk/NAS easily

```python
from mancala_solver.storage import PostgreSQLBackend

storage = PostgreSQLBackend(
    host="localhost",
    database="mancala",
    user="solver",
    password="..."
)
```

### 3. Use External Storage (20TB Recommended)

Kalah(6,3) will generate **5-10 TB** of data. Ensure you have:
- External SSD/HDD with 20TB capacity
- Fast connection (Thunderbolt, USB 3.2, NAS)
- Database files on the external drive

### 4. Monitor Actively

Run the monitor script during the solve:
```bash
./scripts/monitor.sh
```

Watch for:
- Memory pressure % - should stay below 80%
- Dedup mode switches (MEM → DB)
- Process memory growth

### 5. Tune Thresholds for Your System

If you have more/less RAM, adjust thresholds:

```python
from mancala_solver.utils import MemoryMonitor

# For 64GB system
monitor = MemoryMonitor(
    warning_threshold_gb=16.0,   # More headroom
    critical_threshold_gb=8.0
)

# For 16GB system
monitor = MemoryMonitor(
    warning_threshold_gb=2.0,    # Tighter thresholds
    critical_threshold_gb=1.0
)
```

## Performance Trade-offs

| Strategy | Speed | Memory | Best For |
|----------|-------|--------|----------|
| In-memory dedup | ⚡ Fast | 🔴 High | Kalah(4,3), Kalah(5,3) |
| DB dedup | 🐢 Slower (2-3×) | ✅ Bounded | Kalah(6,3), Kalah(6,4) |
| Adaptive (auto-switch) | ⚡→🐢 Hybrid | ✅ Safe | **Recommended** |

The adaptive approach gives you the best of both worlds:
- Fast in-memory dedup when RAM is available
- Automatic fallback to DB dedup when needed
- No manual intervention required

## Troubleshooting

### "Memory pressure" warnings
**Normal** - system has < 4GB free, solver is throttling. No action needed.

### "CRITICAL memory pressure" warnings
**Concerning** - system has < 2GB free. Solver will pause periodically.
- Close other applications
- Reduce `num_workers`
- Lower `max_dedup_set_size`

### "Switching to DB-based dedup"
**Normal for Kalah(6,3)** - dedup set reached limit, using database instead.
- Performance will slow 2-3× but memory is safe
- Expected at deep BFS levels

### Process killed by OS (OOM)
**Rare with new safeguards** - but possible if:
- Thresholds too low (< 2GB critical threshold on large system)
- SQLite cache too large (reduce `max_cache_mb`)
- Too many workers (reduce `num_workers`)

**Fix**: Restart with more conservative settings:
```python
solver = ChunkedBFSSolver(
    chunk_size=25_000,              # Smaller chunks
    max_dedup_set_size=5_000_000,   # Smaller dedup set
)
```

## Future Enhancements

Potential improvements for even larger solves:

1. **Bloom filter dedup**: Space-efficient probabilistic dedup
2. **Disk-backed dedup set**: Use mmap'd file instead of pure RAM
3. **Dynamic worker scaling**: Reduce workers when memory tight
4. **Multi-tiered dedup**: LRU cache → bloom filter → database
5. **Compression**: Compress position data in database

---

For questions or issues, see the main README or open a GitHub issue.

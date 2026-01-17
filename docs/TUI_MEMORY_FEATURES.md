# TUI Memory Management Features

The monitoring TUI (`scripts/monitor.sh`) now displays real-time memory management information.

## Updated Display

### Header - Memory State Alerts

The header now shows memory state warnings:

**Normal state:**
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: BFS  |  Elapsed: 1:23:45 ─┐
```

**Throttled (< 4GB available):**
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: BFS  |  Elapsed: 1:23:45  |  ⚠ MEM THROTTLED ─┐
```

**Critical (< 2GB available):**
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: BFS  |  Elapsed: 1:23:45  |  ! MEM CRITICAL ─┐
```

### Statistics Panel - New Memory Metrics

The left statistics panel now includes:

```
┌─ 📊 Statistics ──────────────────────────────┐
│ Total Positions    89,234,567                │
│ Database Size      8.3 GB                    │
│ Current Depth      45                        │
│ Max Depth          45                        │
│ Positions at Depth 1,234,567                 │
│                                               │
│ Process Memory     1,234 MB                  │
│ System Memory      28.3GB / 36.0GB (78%)     │  ← Yellow when 60-80%, Red when >80%
│ Memory Headroom    7.7 GB                    │
│ CPU Cores          14                        │
│                                               │
│ Memory State       ✓ Normal                  │  ← Green ✓ / Yellow ⚠ / Red !
│ Dedup Mode         ⚡ MEM                     │  ← Green ⚡ (fast) / Yellow 💾 (DB mode)
│ SQLite Cache       128 MB                    │  ← Adaptive cache size
│ Memory Events      3 warnings, 1 critical    │  ← Only shown if events occurred
└───────────────────────────────────────────────┘
```

### Memory State Indicators

#### ✓ Normal (Green)
- Available RAM > 4GB
- Dedup using in-memory hash set
- Full parallelism enabled
- Optimal performance

#### ⚠ Throttled (Yellow)
- Available RAM between 2-4GB
- May switch to DB dedup mode
- Reduced chunk parallelism (2× instead of 4×)
- Slower but stable

#### ! Critical (Red)
- Available RAM < 2GB
- Forced DB dedup mode
- Pausing operations periodically for GC
- Emergency memory management active

### Dedup Mode Indicators

#### ⚡ MEM (Green) - In-Memory Dedup
- Fast O(1) hash set lookups
- Bounded at 10M hashes (~80MB RAM)
- Preferred mode for performance

#### 💾 DB (Yellow) - Database Dedup
- Slower database index lookups
- No RAM growth (memory-safe)
- Automatically activated when:
  - Hash set reaches 10M limit
  - Memory pressure detected
  - Critical memory state

## Example: Watching a Kalah(6,3) Solve

### Phase 1: Early depths (Normal)
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: BFS  |  Elapsed: 0:15:22 ─────────┐
└────────────────────────────────────────────────────────────────────────────────┘

┌─ 📊 Statistics ──────────────┐  ┌─ Recent Depth Progress ────────────┐
│ Total Positions    1,234,567 │  │ Depth   Positions      Total       │
│ Current Depth      15         │  │   10      45,123       567,890     │
│                               │  │   11      56,234       624,124     │
│ Process Memory     892 MB     │  │   12      67,345       691,469     │
│ System Memory      14.2 / 36  │  │   13      78,456       769,925     │
│ Memory Headroom    21.8 GB    │  │   14      89,567       859,492     │
│                               │  │   15      98,678       958,170     │
│ Memory State       ✓ Normal   │  └────────────────────────────────────┘
│ Dedup Mode         ⚡ MEM     │
│ SQLite Cache       256 MB     │
└───────────────────────────────┘
```

### Phase 2: Mid depths (Switching to DB mode)
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: BFS  |  Elapsed: 2:45:13 ─────────┐
└────────────────────────────────────────────────────────────────────────────────┘

┌─ 📊 Statistics ──────────────┐  ┌─ Recent Depth Progress ────────────┐
│ Total Positions    45,123,456│  │ Depth   Positions      Total       │
│ Current Depth      38         │  │   36    2,345,678      40,234,123  │
│                               │  │   37    2,456,789      42,690,912  │
│ Process Memory     1,456 MB   │  │   38    2,567,890      45,258,802  │
│ System Memory      30.2 / 36  │  │ (DB dedup mode activated)          │
│ Memory Headroom    5.8 GB     │  └────────────────────────────────────┘
│                               │
│ Memory State       ⚠ Throttled│  ← Switched to throttled mode
│ Dedup Mode         💾 DB      │  ← Switched to DB mode (hash set reached 10M)
│ SQLite Cache       128 MB     │  ← Cache reduced
│ Memory Events      1 warning  │
└───────────────────────────────┘
```

### Phase 3: Peak depths (Critical management)
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: BFS  |  Elapsed: 8:12:45  |  ! MEM CRITICAL ─┐
└──────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ 📊 Statistics ──────────────┐  ┌─ Recent Depth Progress ────────────┐
│ Total Positions    2.1 billion│  │ Depth   Positions      Total       │
│ Current Depth      67         │  │   65    45,234,567     1.9B        │
│                               │  │   66    48,345,678     2.0B        │
│ Process Memory     1,689 MB   │  │   67    52,456,789     2.1B        │
│ System Memory      34.8 / 36  │  ← High pressure (96%)
│ Memory Headroom    1.2 GB     │  ← Very low!
│                               │  └────────────────────────────────────┘
│ Memory State       ! Critical │  ← Critical state
│ Dedup Mode         💾 DB      │  ← DB mode active
│ SQLite Cache       64 MB      │  ← Cache reduced to minimum
│ Memory Events      12 warnings, 3 critical │
└───────────────────────────────┘

📝 Recent Logs:
[WARNING] Critical memory pressure at chunk 1234, clearing dedup set (size: 10,000,000)
[WARNING] Critical memory pressure detected, pausing 10s for GC
[INFO] Memory: Process=1689MB, System=1.2GB available (96% used)
```

### Phase 4: Minimax (Memory recovered)
```
┌─ 🎮 Mancala Strong Solver Monitor  |  Phase: Minimax  |  Elapsed: 15:34:22 ────┐
└────────────────────────────────────────────────────────────────────────────────┘

┌─ 📊 Statistics ──────────────┐  ┌─ Recent Seed Layers ───────────────┐
│ Total Positions    8.3 billion│  │ Seeds  Positions    Iterations     │
│ Seeds Processed    18 / 72    │  │   12   234,567      3              │
│ Progress           25.0%      │  │   13   245,678      4              │
│                               │  │   14   256,789      5              │
│ Process Memory     1,234 MB   │  │   15   267,890      3              │
│ System Memory      22.5 / 36  │  │   16   278,901      6              │
│ Memory Headroom    13.5 GB    │  │   17   289,012      4              │
│                               │  └────────────────────────────────────┘
│ Memory State       ✓ Normal   │  ← Back to normal
│ SQLite Cache       256 MB     │  ← Cache expanded again
│ Memory Events      12 warnings, 3 critical │ ← Historical count
└───────────────────────────────┘
```

## Color Coding

| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Memory State | ✓ Normal | ⚠ Throttled | ! Critical |
| System Memory % | < 60% | 60-80% | > 80% |
| Dedup Mode | ⚡ MEM (fast) | 💾 DB (safe) | N/A |
| Memory Events | 0 warnings | 1+ warnings | 1+ critical |

## Real-time Updates

The TUI updates **2× per second**, showing:
- Live memory pressure changes
- Dedup mode switches
- Memory state transitions
- Warning/critical event counts

## Starting the Monitor

While a solve is running:
```bash
# Auto-detect latest solve
./scripts/monitor.sh

# Or specify manually
python3 scripts/monitor_solve.py /path/to/task.output /path/to/database.db
```

Press Ctrl+C to exit the monitor (does not stop the solver).

## What Each Metric Means

- **Process Memory**: RAM used by Python solver process (workers + main)
- **System Memory**: Total RAM in use across entire system
- **Memory Headroom**: Free RAM available for new allocations
- **Memory State**: Current adaptive strategy (Normal/Throttled/Critical)
- **Dedup Mode**: Hash set (MEM) vs Database (DB) deduplication
- **SQLite Cache**: Current cache size (adapts based on available RAM)
- **Memory Events**: Count of warnings/critical events since solve started

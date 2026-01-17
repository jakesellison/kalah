# Quick Start Guide

## ✅ System Validated!

Your Mancala solver is **ready to go**! Validation completed successfully:
- ✅ 5,283,478 positions solved for Kalah(4,3)
- ✅ Player 1 wins by 6 (correct)
- ✅ Best opening move: Pit 1 (correct)
- ✅ Total time: 4.6 minutes

## 🎯 Next Steps

### 1. Order External Drive ($280)
**Recommended: [Seagate Expansion 20TB](https://www.amazon.com/Seagate-Expansion-External-Services-STKP20000400/dp/B0B2PZWD81)**
- 20TB capacity
- USB 3.0 (220-230 MB/s)
- 3-year warranty + data recovery
- Enough for Kalah(6,3) or possibly Kalah(6,4)

### 2. When Drive Arrives

Follow setup in: `EXTERNAL_DRIVE_SETUP.md`

**Quick version:**
```bash
# Format drive
diskutil eraseDisk APFS MancalaData /dev/disk4

# Optional: Install PostgreSQL on drive for better performance
initdb -D /Volumes/MancalaData/postgres_data
pg_ctl -D /Volumes/MancalaData/postgres_data start
createdb mancala
```

### 3. Run a Solve

**Easy way (with auto-monitoring):**
```bash
./scripts/solve_with_monitor.sh 6 3
```

**Manual way:**
```bash
# Start solve in background
python3 -u -m src.mancala_solver.cli.main solve \
  --num-pits 6 \
  --num-seeds 3 \
  --backend sqlite \
  --db-path /Volumes/MancalaData/kalah_6_3.db \
  --workers 14 &

# Monitor progress
./scripts/monitor.sh
```

### 4. Monitor Progress

**Option 1: TUI Dashboard (recommended)**
```bash
./scripts/monitor.sh
```

**Option 2: Manual log check**
```bash
tail -f /tmp/kalah_6_3_*.log
```

**Option 3: Database check**
```bash
python3 -m src.mancala_solver.cli.main query \
  --num-pits 6 \
  --num-seeds 3 \
  --db-path /Volumes/MancalaData/kalah_6_3.db
```

## 📊 Expected Timeline

| Variant | Estimated Positions | Storage | Time (14 workers) |
|---------|---------------------|---------|-------------------|
| Kalah(6,3) | 50-100 billion | 5-10 TB | 1-2 weeks |
| Kalah(6,4) | 100-300 billion | 10-30 TB | 1-2 months |

## 🐛 Troubleshooting

### TUI not updating?
The monitor refreshes every 0.5s, but Python buffers output. Use `-u` flag:
```bash
python3 -u -m src.mancala_solver.cli.main solve ...
```

Or set environment variable:
```bash
PYTHONUNBUFFERED=1 python3 -m src.mancala_solver.cli.main solve ...
```

### Out of disk space?
Check drive capacity:
```bash
df -h /Volumes/MancalaData
```

### Mac going to sleep?
Prevent sleep during solve:
```bash
caffeinate -d &  # Prevent display sleep
```

Or in System Settings → Energy Saver:
- ☑️ Prevent computer from sleeping automatically
- ☑️ Prevent disk from sleeping

### PostgreSQL not starting?
```bash
# Check status
pg_ctl -D /Volumes/MancalaData/postgres_data status

# Start manually
pg_ctl -D /Volumes/MancalaData/postgres_data start

# View logs
tail /Volumes/MancalaData/postgres.log
```

## 💰 Cost Comparison

| Solution | Cost | Time |
|----------|------|------|
| **20TB External Drive** | **$280** | **1-2 months** |
| Cloud SQL + Storage (10TB) | $5,780 | 2-3 months |
| Cloud SQL + Storage (30TB) | $15,940 | 2-3 months |

**You're saving $5,500-15,660 by going local!**

## 📈 Progress Tracking

The TUI shows:
- Current phase (BFS or Minimax)
- Progress bar with completion %
- Positions discovered/solved
- Memory usage
- Recent log entries

**Example output during solve:**
```
Phase: Minimax  Progress: 68% (17/25 seed layers)
Seeds-in-pits 17: 386,498 positions
Memory: 1,245 MB
Time elapsed: 2h 15m
```

## 🎓 Understanding Results

After solving, query the database:
```bash
python3 -m src.mancala_solver.cli.main query \
  --num-pits 6 \
  --num-seeds 3 \
  --db-path /Volumes/MancalaData/kalah_6_3.db
```

Output tells you:
- **Starting value**: Positive = Player 1 wins, Negative = Player 2 wins
- **Best opening move**: Optimal first move (pit number)
- **Total positions**: Complete game tree size

## 🚀 You're Ready!

1. ✅ Solver validated and working
2. 📦 Order 20TB drive ($280)
3. 💾 Run setup when it arrives
4. 🎮 Start solving Kalah(6,3)
5. 🏆 Become first person to strongly solve it!

Questions? Check:
- `EXTERNAL_DRIVE_SETUP.md` - Detailed setup instructions
- `SUMMARY.md` - Technical overview of what we built
- `RESULTS.md` - Kalah(4,3) performance data

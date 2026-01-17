# Mancala Solver - Results & Performance

## Kalah(4,3) - First Solve (Sequential Minimax)

### Configuration
- Variant: Kalah(4,3) with standard rules
- Workers: 14 (BFS only)
- Database: SQLite (533 MB)

### Results
- **Total Positions**: 5,283,478
- **Solution**: Player 1 wins by 6
- **Best Opening Move**: Pit 1

### Performance
- **Phase 1 (BFS)**: ~1 minute (14 parallel workers) ✅
- **Phase 2 (Minimax)**: ~7 minutes (1 core) ⏱️
- **Total Time**: ~8 minutes

### Phase 2 Breakdown (Sequential)
```
Seeds 0-3:   Fast (< 1s each)
Seeds 4-10:  Moderate (1-35s each)
Seeds 11-16: Slow (17-51s each) ← Peak complexity
Seeds 17-24: Fast again (< 1s each)
```

Peak at seeds 15-16 with ~775K positions each taking ~46 seconds.

---

## Kalah(4,3) - Second Solve (Parallel Minimax) ✅

### Configuration
- Variant: Kalah(4,3) with standard rules
- Workers: 14 (BFS + Minimax)
- Database: SQLite (533 MB)

### Results
- **Total Positions**: 5,283,478 (identical to sequential)
- **Solution**: Player 1 wins by 6 (verified ✓)
- **Best Opening Move**: Pit 1 (verified ✓)

### Performance
- **Phase 1 (BFS)**: ~1 minute (14 parallel workers) ✅
- **Phase 2 (Minimax)**: **3 minutes 23 seconds** (14 parallel workers) ✅
- **Total Time**: **~4.5 minutes**

### Speedup Analysis
- Sequential minimax: 7 minutes (420s)
- Parallel minimax: 3m 23s (203s)
- **Actual speedup: 2.07×** 🚀

**Why not 14×?**
1. **Iterative dependencies**: Within each seed level, positions must be solved iteratively (can't all be parallel)
2. **SQLite bottleneck**: 14 workers reading/writing same database file creates contention
3. **Multiprocessing overhead**: Process creation, communication, coordination

**Potential improvements for Kalah(6,4)**:
- Use PostgreSQL (better concurrent access)
- Use SSD-backed database (faster I/O)
- Optimize batch sizes and chunking
- Could achieve 3-5× speedup with better database backend

---

## Comparison with Published Results

### Irving et al. (2000) - "Solving Kalah"

**Their Results**:
- Configuration: Kalah(6,4) and Kalah(6,5)
- Kalah(6,4): Player 1 wins by 10
- Kalah(6,5): Player 1 wins by 12
- Database: Kalah(4,3) had 4,604,996 positions

**Our Results**:
- Configuration: Kalah(4,3)
- Positions: 5,283,478 (15% more than Irving reported)
- Player 1 wins by 6

**Potential Reasons for Discrepancy**:
1. **Different rule variants**: Capture rules or extra-turn implementation may differ
2. **Counting method**: We count unique game states; they may have used different canonicalization
3. **Transposition handling**: Different ways of handling position equivalence
4. **Game variant**: They may have used a different Kalah variant

**Validation Needed**:
- Compare exact rule implementations
- Verify starting position minimax value against other solvers
- Check if our extra-turn and capture logic matches standard Kalah

---

## Next Steps

1. ✅ Parallelize minimax (implemented)
2. ⏳ Validate parallel minimax performance
3. 📋 Solve Kalah(6,4) - compare with Irving's win-by-10 result
4. 🔄 Verify our rule implementation matches standard Kalah
5. ☁️ Scale to cloud infrastructure for larger variants

---

## System Specs

- **Machine**: MacBook Pro M3 Max
- **CPU**: 14 cores (10 performance + 4 efficiency)
- **Memory**: 36 GB LPDDR5
- **Storage**: 399 GB available

Excellent specs for strong solving - comparable to Rawlings' 32GB setup for Kalah(6,6).

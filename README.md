# Mancala Solver

A complete solver for Kalah variants using retrograde minimax analysis.

## What It Does

Solves Kalah (Mancala) variants by:
1. **BFS**: Explores all reachable game positions
2. **Minimax**: Computes optimal play values for each position
3. **Result**: Determines perfect play outcome and best opening moves

## Quick Start

Solve a game variant:

```bash
./scripts/solve_with_monitor.sh 4 3
```

This will:
- Build the game graph (BFS phase)
- Compute minimax values (solving phase)
- Show the result and best opening move

## Results

### Kalah(4,2) - 590K positions
- **Result**: Player 1 wins by 6
- **Best opening move**: Pit 3
- **Solve time**: ~22 seconds

### Kalah(4,3) - 5.3M positions
- **Result**: Player 1 wins by 6
- **Best opening move**: Pit 1
- **Solve time**: ~5 minutes

## Architecture

### Storage: SQLite
- Efficient on-disk storage
- In-memory deduplication for speed
- Handles millions of positions

### Solvers
- **ChunkedBFSSolver**: Memory-efficient BFS with async writes
- **ParallelMinimaxSolver**: Multi-process retrograde minimax
- **Default workers**: 14 parallel processes

### Game Rules
- Kalah(N,K): N pits per side, K seeds per pit
- Standard Kalah rules (capture, extra turns)
- Both players play optimally

## Project Structure

```
src/mancala_solver/
├── core/           # Game rules and state representation
├── storage/        # SQLite backend
└── solver/         # BFS and minimax solvers

scripts/
└── solve_with_monitor.sh   # Main solving script

data/databases/     # Solved game databases
```

## Requirements

```bash
pip install -r requirements.txt
```

## Advanced Usage

### Solve with custom parameters

```bash
python3 -m src.mancala_solver.cli.main solve \
    --num-pits 6 \
    --num-seeds 4 \
    --db-path data/databases/kalah_6_4.db \
    --workers 14
```

### Query a solved database

```bash
python3 -m src.mancala_solver.cli.main query \
    --num-pits 4 \
    --num-seeds 3 \
    --db-path data/databases/kalah_4_3.db
```

## How It Works

1. **State Representation**
   - Compact board encoding (tuple of pit values)
   - Zobrist hashing for fast position lookup
   - Depth and seeds_in_pits tracking

2. **BFS Phase**
   - Generate all reachable positions from start
   - Track parent→child edges
   - Deduplicate using in-memory hash set

3. **Minimax Phase**
   - Process positions by seeds_in_pits (low to high)
   - Iterate until convergence at each level
   - Parallel workers for speed
   - Terminal positions score: (P1 store - P2 store)

4. **Optimizations**
   - Async database writes (overlap I/O with computation)
   - Batch processing (100K positions per batch)
   - Memory monitoring (adaptive thresholds)
   - Efficient SQLite configuration

## Known Results

- **Kalah(6,6)**: Player 2 wins (proven by Irving et al., 2000)
- **Kalah(4,3)**: Player 1 wins by 6 ✓ (verified)
- **Kalah(4,2)**: Player 1 wins by 6 ✓ (verified)

## License

MIT

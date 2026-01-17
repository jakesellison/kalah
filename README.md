# Mancala Strong Solver

A complete strong solver for Kalah/Mancala variants using parallel breadth-first search and retrograde minimax analysis.

## Features

- **Complete game graph construction** via BFS
- **Retrograde minimax analysis** to solve all positions
- **Parallel computation** utilizing all CPU cores
- **Pluggable storage backends** (SQLite, PostgreSQL, Cloud Bigtable)
- **Checkpointing** for resumable long-running solves
- **Validation** against published results (Irving et al. 2000)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Solve Kalah(4,3) locally
python -m src.mancala_solver.cli.main solve --config configs/kalah_4_3.yaml

# Run tests
pytest tests/
```

## Project Structure

- `src/mancala_solver/` - Main package
  - `core/` - Game state representation and rules
  - `storage/` - Storage backend implementations
  - `solver/` - BFS and minimax solvers
  - `utils/` - Configuration, logging, checkpointing
  - `cli/` - Command-line interface
- `tests/` - Test suite
- `configs/` - Configuration files for variants
- `docs/` - Design documentation

## Documentation

- [Project Design](PROJECT_DESIGN.md)
- [Game State Design](GAME_STATE_DESIGN.md)
- [Cloud Architecture](CLOUD_ARCHITECTURE.md)
- [Storage Abstraction](STORAGE_ABSTRACTION.md)
- [Research Notes](MANCALA_SOLVING.md)

## Current Status

- ✅ Kalah(4,3) - In development
- ⏳ Kalah(6,4) - Planned
- ⏳ Kalah(6,5) - Planned
- ⏳ Kalah(6,6) - Planned

## References

- Irving, G., Donkers, J., & Uiterwijk, J. (2000). Solving Kalah. ICGA Journal.
- Rawlings, M. (2015). Kalah research. Mancala World.

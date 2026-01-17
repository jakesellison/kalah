#!/usr/bin/env python3
"""
Validate local solver on Kalah(4,3).

This tests the full pipeline:
1. Parallel BFS (14 workers)
2. Parallel Minimax (14 workers)
3. Verify result matches previous solve
"""

import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mancala_solver.storage import SQLiteBackend
from src.mancala_solver.solver import ParallelSolver, ParallelMinimaxSolver
from src.mancala_solver.core import init_zobrist_table, create_starting_state, zobrist_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,  # Force reconfiguration
)

# Ensure logs flush immediately (important for monitoring)
for handler in logging.root.handlers:
    handler.flush = lambda: None  # Already goes to stderr which is unbuffered

logger = logging.getLogger(__name__)


def main():
    # Configuration
    NUM_PITS = 4
    NUM_SEEDS = 3
    NUM_WORKERS = 14
    DB_PATH = "data/databases/kalah_4_3_validate.db"
    
    logger.info("=" * 70)
    logger.info("LOCAL SOLVER VALIDATION - Kalah(4,3)")
    logger.info("=" * 70)
    logger.info(f"Workers: {NUM_WORKERS}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("")
    
    # Initialize
    init_zobrist_table(NUM_PITS)
    
    # Create fresh database
    db_path = Path(DB_PATH)
    if db_path.exists():
        logger.info("Removing old database...")
        db_path.unlink()
    
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteBackend(str(db_path))
    
    try:
        start_time = time.time()
        
        # Phase 1: Parallel BFS
        logger.info("=" * 70)
        logger.info("PHASE 1: Parallel BFS")
        logger.info("=" * 70)
        
        bfs_start = time.time()
        bfs_solver = ParallelSolver(
            storage=storage,
            num_pits=NUM_PITS,
            num_seeds=NUM_SEEDS,
            num_workers=NUM_WORKERS,
        )
        
        total_positions = bfs_solver.build_game_graph()
        bfs_time = time.time() - bfs_start
        
        logger.info("")
        logger.info(f"✅ BFS Complete")
        logger.info(f"   Positions: {total_positions:,}")
        logger.info(f"   Time: {bfs_time:.1f}s")
        logger.info("")
        
        # Phase 2: Parallel Minimax
        logger.info("=" * 70)
        logger.info("PHASE 2: Parallel Minimax")
        logger.info("=" * 70)
        
        minimax_start = time.time()
        minimax_solver = ParallelMinimaxSolver(
            storage=storage,
            num_pits=NUM_PITS,
            num_seeds=NUM_SEEDS,
            num_workers=NUM_WORKERS,
        )
        
        starting_value = minimax_solver.solve()
        minimax_time = time.time() - minimax_start
        
        logger.info("")
        logger.info(f"✅ Minimax Complete")
        logger.info(f"   Time: {minimax_time:.1f}s")
        logger.info("")
        
        # Get best move
        start_state = create_starting_state(NUM_PITS, NUM_SEEDS)
        start_hash = zobrist_hash(start_state)
        start_pos = storage.get(start_hash)
        
        total_time = time.time() - start_time
        
        # Results
        logger.info("=" * 70)
        logger.info("VALIDATION RESULTS")
        logger.info("=" * 70)
        logger.info(f"Total Positions: {total_positions:,}")
        logger.info(f"Starting Value:  {starting_value}")
        logger.info(f"Best Move:       Pit {start_pos.best_move}")
        logger.info("")
        logger.info(f"Phase 1 (BFS):     {bfs_time:.1f}s")
        logger.info(f"Phase 2 (Minimax): {minimax_time:.1f}s")
        logger.info(f"Total Time:        {total_time:.1f}s")
        logger.info("")
        
        # Validate against known results
        EXPECTED_POSITIONS = 5_283_478
        EXPECTED_VALUE = 6
        EXPECTED_MOVE = 1
        
        success = True
        
        if total_positions == EXPECTED_POSITIONS:
            logger.info(f"✅ Position count correct: {total_positions:,}")
        else:
            logger.error(f"❌ Position count mismatch!")
            logger.error(f"   Expected: {EXPECTED_POSITIONS:,}")
            logger.error(f"   Got:      {total_positions:,}")
            success = False
        
        if starting_value == EXPECTED_VALUE:
            logger.info(f"✅ Starting value correct: {starting_value}")
        else:
            logger.error(f"❌ Starting value mismatch!")
            logger.error(f"   Expected: {EXPECTED_VALUE}")
            logger.error(f"   Got:      {starting_value}")
            success = False
        
        if start_pos.best_move == EXPECTED_MOVE:
            logger.info(f"✅ Best move correct: Pit {start_pos.best_move}")
        else:
            logger.error(f"❌ Best move mismatch!")
            logger.error(f"   Expected: Pit {EXPECTED_MOVE}")
            logger.error(f"   Got:      Pit {start_pos.best_move}")
            success = False
        
        logger.info("")
        
        if success:
            logger.info("🎉 VALIDATION PASSED - Solver working correctly!")
            logger.info("")
            logger.info("Ready to solve larger variants when external drive arrives:")
            logger.info("  • Kalah(6,3): ~5-10 TB (fits on 20TB drive)")
            logger.info("  • Kalah(6,4): ~10-30 TB (may need 2× 20TB drives)")
            return 0
        else:
            logger.error("❌ VALIDATION FAILED - Results don't match!")
            return 1
        
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Test chunked solver on Cloud SQL.

This validates the chunked architecture before scaling to Kalah(6,4).
"""

import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mancala_solver.storage import PostgreSQLBackend
from src.mancala_solver.solver import ChunkedBFSSolver, ChunkedParallelMinimaxSolver
from src.mancala_solver.core import init_zobrist_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def main():
    # Configuration
    NUM_PITS = 4
    NUM_SEEDS = 3
    CHUNK_SIZE = 10_000  # Small chunks for testing
    NUM_WORKERS = 14

    logger.info("=" * 60)
    logger.info("CHUNKED SOLVER TEST - Kalah(4,3)")
    logger.info("=" * 60)
    logger.info(f"Chunk size: {CHUNK_SIZE:,}")
    logger.info(f"Workers: {NUM_WORKERS}")

    # Initialize Zobrist hashing
    init_zobrist_table(NUM_PITS)

    # Connect to PostgreSQL
    storage = PostgreSQLBackend(
        host="localhost",
        port=5433,
        database="mancala",
        user="postgres",
        password="mancala-first-solve"
    )

    logger.info("✅ Connected to PostgreSQL")

    # Clear existing data
    logger.info("Clearing existing data...")
    import psycopg2
    with storage.conn.cursor() as cursor:
        cursor.execute("TRUNCATE TABLE positions")
    storage.conn.commit()
    logger.info("✅ Table cleared")

    try:
        # Phase 1: Chunked BFS
        logger.info("=" * 60)
        logger.info("PHASE 1: Chunked BFS")
        logger.info("=" * 60)

        bfs_solver = ChunkedBFSSolver(
            storage=storage,
            num_pits=NUM_PITS,
            num_seeds=NUM_SEEDS,
            chunk_size=CHUNK_SIZE
        )

        total_positions = bfs_solver.build_game_graph()

        logger.info(f"✅ BFS complete: {total_positions:,} positions")

        # Phase 2: Chunked Parallel Minimax
        logger.info("=" * 60)
        logger.info("PHASE 2: Chunked Parallel Minimax")
        logger.info("=" * 60)

        minimax_solver = ChunkedParallelMinimaxSolver(
            storage=storage,
            num_pits=NUM_PITS,
            num_seeds=NUM_SEEDS,
            chunk_size=CHUNK_SIZE,
            num_workers=NUM_WORKERS
        )

        starting_value = minimax_solver.solve()

        # Results
        logger.info("=" * 60)
        logger.info("SOLUTION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Total positions: {total_positions:,}")
        logger.info(f"Starting value: {starting_value}")

        if starting_value > 0:
            logger.info(f"Result: Player 1 wins by {starting_value}")
        elif starting_value < 0:
            logger.info(f"Result: Player 2 wins by {-starting_value}")
        else:
            logger.info("Result: Perfect play leads to a tie")

    finally:
        storage.close()


if __name__ == "__main__":
    main()

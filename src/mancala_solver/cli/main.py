"""
Main CLI for Mancala solver.
"""

import argparse
import logging
import sys
import os
from pathlib import Path

from ..storage import SQLiteBackend
from ..solver import ChunkedBFSSolver, ParallelMinimaxSolver, SimpleParallelBFSSolver, OriginalBFSSolver
from ..solver.parallel_bfs import ParallelBFSSolver
from ..solver.adaptive_parallel_bfs import AdaptiveParallelBFSSolver
from ..utils.resource_monitor import ResourceMonitor
from ..utils.rich_display import SolverDisplay


def setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def solve_command(args):
    """Solve a Kalah variant."""
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    # Determine worker counts for each phase
    bfs_workers = args.bfs_workers if args.bfs_workers is not None else args.workers
    minimax_workers = args.minimax_workers if args.minimax_workers is not None else args.workers

    # Initialize storage
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteBackend(str(db_path))

    # Initialize resource monitor and display
    resource_monitor = ResourceMonitor(
        db_path=str(db_path),
        min_disk_gb=50.0,  # Abort if < 50GB free
        max_ram_percent=90.0,  # Abort if > 90% RAM used
    )
    display = SolverDisplay(resource_monitor=resource_monitor)

    # Show header
    display.show_header(
        f"Mancala Solver - Kalah({args.num_pits},{args.num_seeds})",
        args.num_pits,
        args.num_seeds,
        bfs_workers
    )

    try:
        # Phase 1: BFS - select solver based on --solver flag
        if args.solver == "adaptive":
            display.log_info(f"Using adaptive parallel BFS solver (max {bfs_workers} workers)")
            bfs_solver = AdaptiveParallelBFSSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                max_workers=bfs_workers,
                display=display,
                resource_monitor=resource_monitor,
            )
            solver_name = "Adaptive Parallel BFS"
        elif args.solver == "parallel":
            logger.info(f"Using parallel BFS solver ({bfs_workers} workers)")
            bfs_solver = ParallelBFSSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                num_workers=bfs_workers,
            )
            solver_name = "Parallel BFS"
        elif args.solver == "simple":
            logger.info(f"Using simple parallel BFS solver ({bfs_workers} workers)")
            bfs_solver = SimpleParallelBFSSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                num_workers=bfs_workers,
            )
            solver_name = "Simple Parallel BFS"
        elif args.solver == "original":
            logger.info("Using original BFS solver (single-threaded, all-in-memory)")
            bfs_solver = OriginalBFSSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
            )
            solver_name = "Original BFS"
        else:  # chunked
            logger.info("Using chunked BFS solver (single-threaded)")
            bfs_solver = ChunkedBFSSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                chunk_size=100_000,
            )
            solver_name = "Chunked BFS"

        display.log("")
        display.log("[bold cyan]" + "=" * 60 + "[/bold cyan]")
        display.log(f"[bold cyan]PHASE 1: Building game graph ({solver_name})[/bold cyan]")
        display.log("[bold cyan]" + "=" * 60 + "[/bold cyan]")
        display.log("")
        total_positions_with_dups = bfs_solver.build_game_graph()

        # Dedup cleanup: remove duplicates, keep minimum depth
        logger.info("=" * 60)
        logger.info("PHASE 1.5: Deduplicating positions")
        logger.info("=" * 60)

        import sqlite3
        conn = sqlite3.connect(storage.db_path)
        cursor = conn.cursor()

        before_count = cursor.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        unique_count = cursor.execute("SELECT COUNT(DISTINCT state_hash) FROM positions").fetchone()[0]
        dupe_count = before_count - unique_count

        logger.info(f"Total with duplicates: {before_count:,}")
        logger.info(f"Unique positions: {unique_count:,}")
        logger.info(f"Duplicates: {dupe_count:,} ({dupe_count/before_count*100:.1f}%)")

        if dupe_count > 0:
            logger.info("Removing duplicates (keeping minimum depth)...")
            cursor.execute("""
                CREATE TABLE positions_dedup AS
                SELECT
                    state_hash,
                    MIN(state) as state,
                    MIN(depth) as depth,
                    MIN(seeds_in_pits) as seeds_in_pits,
                    MIN(minimax_value) as minimax_value,
                    MIN(best_move) as best_move
                FROM positions
                GROUP BY state_hash
            """)
            cursor.execute("DROP TABLE positions")
            cursor.execute("ALTER TABLE positions_dedup RENAME TO positions")
            cursor.execute("CREATE INDEX idx_state_hash ON positions(state_hash)")
            cursor.execute("CREATE INDEX idx_depth ON positions(depth)")
            cursor.execute("CREATE INDEX idx_seeds_in_pits ON positions(seeds_in_pits)")
            conn.commit()
            logger.info(f"Removed {dupe_count:,} duplicates")

        conn.close()
        total_positions = unique_count
        logger.info(f"Final: {total_positions:,} unique positions")

        # Phase 2: Minimax
        logger.info("=" * 60)
        logger.info("PHASE 2: Computing minimax values")
        logger.info("=" * 60)

        logger.info("Using parallel minimax solver")
        minimax_solver = ParallelMinimaxSolver(
            storage=storage,
            num_pits=args.num_pits,
            num_seeds=args.num_seeds,
            num_workers=minimax_workers,
        )

        starting_value = minimax_solver.solve()

        # Results
        logger.info("=" * 60)
        logger.info("SOLUTION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Total positions: {total_positions:,}")
        logger.info(f"Starting position value: {starting_value}")

        if starting_value > 0:
            logger.info(f"Result: Player 1 wins by {starting_value}")
        elif starting_value < 0:
            logger.info(f"Result: Player 2 wins by {-starting_value}")
        else:
            logger.info("Result: Perfect play leads to a tie")

    finally:
        storage.close()


def query_command(args):
    """Query a solved database."""
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    # Initialize storage
    logger.info(f"Backend: SQLite ({args.db_path})")
    storage = SQLiteBackend(args.db_path)

    try:
        total = storage.count_positions()
        max_depth = storage.get_max_depth()

        logger.info(f"Database: {args.db_path}")
        logger.info(f"Total positions: {total:,}")
        logger.info(f"Maximum depth: {max_depth}")

        # Get starting position
        from ..core import create_starting_state, zobrist_hash, init_zobrist_table

        init_zobrist_table(args.num_pits)
        start_state = create_starting_state(args.num_pits, args.num_seeds)
        start_hash = zobrist_hash(start_state)
        start_pos = storage.get(start_hash)

        if start_pos:
            logger.info(f"Starting position value: {start_pos.minimax_value}")
            logger.info(f"Best opening move: {start_pos.best_move}")
        else:
            logger.warning("Starting position not found in database")

    finally:
        storage.close()


def minimax_command(args):
    """Run minimax on existing positions."""
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    logger.info(f"Running minimax for Kalah({args.num_pits},{args.num_seeds})")
    logger.info(f"Workers: {args.workers}")

    # Initialize storage
    logger.info(f"Backend: SQLite ({args.db_path})")
    storage = SQLiteBackend(args.db_path)

    try:
        # Get total positions
        total_positions = storage.count_positions()
        logger.info(f"Total positions in database: {total_positions:,}")

        # Phase 2: Minimax only
        logger.info("=" * 60)
        logger.info("PHASE 2: Computing minimax values")
        logger.info("=" * 60)

        logger.info("Using parallel minimax solver")
        minimax_solver = ParallelMinimaxSolver(
            storage=storage,
            num_pits=args.num_pits,
            num_seeds=args.num_seeds,
            num_workers=args.workers,
        )

        starting_value = minimax_solver.solve()

        # Results
        logger.info("=" * 60)
        logger.info("MINIMAX COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Starting position value: {starting_value}")

        if starting_value > 0:
            logger.info(f"Result: Player 1 wins by {starting_value}")
        elif starting_value < 0:
            logger.info(f"Result: Player 2 wins by {-starting_value}")
        else:
            logger.info("Result: Perfect play leads to a tie")

    finally:
        storage.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Mancala Strong Solver")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Solve command
    solve_parser = subparsers.add_parser("solve", help="Solve a Kalah variant")
    solve_parser.add_argument(
        "--num-pits", type=int, required=True, help="Number of pits per player"
    )
    solve_parser.add_argument(
        "--num-seeds", type=int, required=True, help="Initial seeds per pit"
    )
    solve_parser.add_argument(
        "--db-path",
        default="data/databases/kalah.db",
        help="Path to SQLite database file",
    )
    solve_parser.add_argument(
        "--workers", type=int, default=14, help="Number of parallel workers"
    )
    solve_parser.add_argument(
        "--bfs-workers", type=int, default=None, help="Number of workers for BFS phase (defaults to --workers)"
    )
    solve_parser.add_argument(
        "--minimax-workers", type=int, default=None, help="Number of workers for minimax phase (defaults to --workers)"
    )
    solve_parser.add_argument(
        "--fast-mode", action="store_true", help="Disable durability for max speed (no crash recovery!)"
    )
    solve_parser.add_argument(
        "--solver",
        choices=["adaptive", "original", "simple", "parallel", "chunked"],
        default="adaptive",
        help="BFS solver to use (adaptive=scales workers based on depth size, original=single-thread baseline, simple=fast parallel, parallel=batch-of-chunks, chunked=single-threaded)"
    )
    solve_parser.set_defaults(func=solve_command)

    # Query command
    query_parser = subparsers.add_parser("query", help="Query a solved database")
    query_parser.add_argument("--num-pits", type=int, required=True)
    query_parser.add_argument("--num-seeds", type=int, required=True)
    query_parser.add_argument(
        "--db-path", required=True, help="Path to SQLite database file"
    )
    query_parser.set_defaults(func=query_command)

    # Minimax-only command
    minimax_parser = subparsers.add_parser("minimax", help="Run minimax on existing positions")
    minimax_parser.add_argument("--num-pits", type=int, required=True)
    minimax_parser.add_argument("--num-seeds", type=int, required=True)
    minimax_parser.add_argument(
        "--db-path", required=True, help="Path to SQLite database file"
    )
    minimax_parser.add_argument(
        "--workers", type=int, default=14, help="Number of parallel workers"
    )
    minimax_parser.set_defaults(func=minimax_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

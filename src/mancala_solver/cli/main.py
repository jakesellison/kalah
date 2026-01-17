"""
Main CLI for Mancala solver.
"""

import argparse
import logging
import sys
import os
from pathlib import Path

from ..storage import SQLiteBackend, PostgreSQLBackend
from ..solver import BFSSolver, MinimaxSolver, ChunkedBFSSolver, ParallelMinimaxSolver


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

    logger.info(f"Solving Kalah({args.num_pits},{args.num_seeds})")
    logger.info(f"BFS workers: {bfs_workers}, Minimax workers: {minimax_workers}")

    # Initialize storage
    if args.backend == "sqlite":
        db_path = Path(args.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Backend: SQLite ({args.db_path})")
        storage = SQLiteBackend(str(db_path))
    elif args.backend == "postgresql":
        logger.info(f"Backend: PostgreSQL ({args.pg_host}:{args.pg_port}/{args.pg_database})")
        storage = PostgreSQLBackend(
            host=args.pg_host,
            port=args.pg_port,
            database=args.pg_database,
            user=args.pg_user,
            password=args.pg_password,
        )
    else:
        raise ValueError(f"Unknown backend: {args.backend}")

    try:
        # Phase 1: BFS
        if bfs_workers > 1:
            logger.info("Using chunked parallel BFS solver (memory-efficient)")
            bfs_solver = ChunkedBFSSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                num_workers=bfs_workers,
            )
        else:
            logger.info("Using sequential BFS solver")
            bfs_solver = BFSSolver(
                storage=storage, num_pits=args.num_pits, num_seeds=args.num_seeds
            )

        logger.info("=" * 60)
        logger.info("PHASE 1: Building game graph (BFS)")
        logger.info("=" * 60)
        total_positions = bfs_solver.build_game_graph()

        # Optional: Cluster table before minimax for better performance
        if args.cluster_before_minimax and args.backend == "postgresql":
            logger.info("=" * 60)
            logger.info("CLUSTERING: Reorganizing table for minimax performance")
            logger.info("=" * 60)
            logger.info("Running CLUSTER command on positions table...")
            logger.info("This physically reorders rows by seeds_in_pits for better cache locality")

            import psycopg2
            conn = psycopg2.connect(
                host=args.pg_host,
                port=args.pg_port,
                database=args.pg_database,
                user=args.pg_user,
                password=args.pg_password,
            )
            cursor = conn.cursor()
            cursor.execute("CLUSTER positions USING idx_seeds_in_pits;")
            conn.commit()
            cursor.close()
            conn.close()

            logger.info("Clustering complete! Minimax queries will be faster.")

        # Phase 2: Minimax
        logger.info("=" * 60)
        logger.info("PHASE 2: Computing minimax values")
        logger.info("=" * 60)

        if minimax_workers > 1:
            logger.info("Using parallel minimax solver")
            minimax_solver = ParallelMinimaxSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                num_workers=minimax_workers,
            )
        else:
            logger.info("Using sequential minimax solver")
            minimax_solver = MinimaxSolver(
                storage=storage, num_pits=args.num_pits, num_seeds=args.num_seeds
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
    if args.backend == "sqlite":
        logger.info(f"Backend: SQLite ({args.db_path})")
        storage = SQLiteBackend(args.db_path)
    elif args.backend == "postgresql":
        logger.info(f"Backend: PostgreSQL ({args.pg_host}:{args.pg_port}/{args.pg_database})")
        storage = PostgreSQLBackend(
            host=args.pg_host,
            port=args.pg_port,
            database=args.pg_database,
            user=args.pg_user,
            password=args.pg_password,
        )
    else:
        raise ValueError(f"Unknown backend: {args.backend}")

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
    if args.backend == "sqlite":
        logger.info(f"Backend: SQLite ({args.db_path})")
        storage = SQLiteBackend(args.db_path)
    elif args.backend == "postgresql":
        logger.info(f"Backend: PostgreSQL ({args.pg_host}:{args.pg_port}/{args.pg_database})")
        storage = PostgreSQLBackend(
            host=args.pg_host,
            port=args.pg_port,
            database=args.pg_database,
            user=args.pg_user,
            password=args.pg_password,
        )
    else:
        raise ValueError(f"Unknown backend: {args.backend}")

    try:
        # Get total positions
        total_positions = storage.count_positions()
        logger.info(f"Total positions in database: {total_positions:,}")

        # Phase 2: Minimax only
        logger.info("=" * 60)
        logger.info("PHASE 2: Computing minimax values")
        logger.info("=" * 60)

        if args.workers > 1:
            logger.info("Using parallel minimax solver")
            minimax_solver = ParallelMinimaxSolver(
                storage=storage,
                num_pits=args.num_pits,
                num_seeds=args.num_seeds,
                num_workers=args.workers,
            )
        else:
            logger.info("Using sequential minimax solver")
            minimax_solver = MinimaxSolver(
                storage=storage, num_pits=args.num_pits, num_seeds=args.num_seeds
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
        "--backend",
        choices=["sqlite", "postgresql"],
        default="sqlite",
        help="Storage backend to use",
    )
    solve_parser.add_argument(
        "--db-path",
        default="data/databases/kalah.db",
        help="Path to database file (SQLite only)",
    )
    solve_parser.add_argument(
        "--pg-host", default="localhost", help="PostgreSQL host"
    )
    solve_parser.add_argument(
        "--pg-port", type=int, default=5432, help="PostgreSQL port"
    )
    solve_parser.add_argument(
        "--pg-database", default="mancala", help="PostgreSQL database name"
    )
    solve_parser.add_argument(
        "--pg-user", default=os.getenv("USER", "postgres"), help="PostgreSQL user"
    )
    solve_parser.add_argument(
        "--pg-password", default="", help="PostgreSQL password"
    )
    solve_parser.add_argument(
        "--workers", type=int, default=1, help="Number of parallel workers (1=sequential)"
    )
    solve_parser.add_argument(
        "--bfs-workers", type=int, default=None, help="Number of workers for BFS phase (defaults to --workers)"
    )
    solve_parser.add_argument(
        "--minimax-workers", type=int, default=None, help="Number of workers for minimax phase (defaults to --workers)"
    )
    solve_parser.add_argument(
        "--cluster-before-minimax", action="store_true",
        help="Run CLUSTER command on positions table before minimax (PostgreSQL only, improves minimax performance)"
    )
    solve_parser.set_defaults(func=solve_command)

    # Query command
    query_parser = subparsers.add_parser("query", help="Query a solved database")
    query_parser.add_argument("--num-pits", type=int, required=True)
    query_parser.add_argument("--num-seeds", type=int, required=True)
    query_parser.add_argument(
        "--backend",
        choices=["sqlite", "postgresql"],
        default="sqlite",
        help="Storage backend to use",
    )
    query_parser.add_argument(
        "--db-path", help="Path to database file (SQLite only)"
    )
    query_parser.add_argument(
        "--pg-host", default="localhost", help="PostgreSQL host"
    )
    query_parser.add_argument(
        "--pg-port", type=int, default=5432, help="PostgreSQL port"
    )
    query_parser.add_argument(
        "--pg-database", default="mancala", help="PostgreSQL database name"
    )
    query_parser.add_argument(
        "--pg-user", default=os.getenv("USER", "postgres"), help="PostgreSQL user"
    )
    query_parser.add_argument(
        "--pg-password", default="", help="PostgreSQL password"
    )
    query_parser.set_defaults(func=query_command)

    # Minimax-only command
    minimax_parser = subparsers.add_parser("minimax", help="Run minimax on existing positions")
    minimax_parser.add_argument("--num-pits", type=int, required=True)
    minimax_parser.add_argument("--num-seeds", type=int, required=True)
    minimax_parser.add_argument(
        "--backend",
        choices=["sqlite", "postgresql"],
        default="sqlite",
        help="Storage backend to use",
    )
    minimax_parser.add_argument(
        "--db-path", help="Path to database file (SQLite only)"
    )
    minimax_parser.add_argument(
        "--pg-host", default="localhost", help="PostgreSQL host"
    )
    minimax_parser.add_argument(
        "--pg-port", type=int, default=5432, help="PostgreSQL port"
    )
    minimax_parser.add_argument(
        "--pg-database", default="mancala", help="PostgreSQL database name"
    )
    minimax_parser.add_argument(
        "--pg-user", default=os.getenv("USER", "postgres"), help="PostgreSQL user"
    )
    minimax_parser.add_argument(
        "--pg-password", default="", help="PostgreSQL password"
    )
    minimax_parser.add_argument(
        "--workers", type=int, default=1, help="Number of parallel workers"
    )
    minimax_parser.set_defaults(func=minimax_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

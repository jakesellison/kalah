"""
Parallel retrograde minimax solver.

Parallelizes solving within each seed level - all positions whose children
are solved can be processed simultaneously across multiple cores.
"""

import logging
import time
from multiprocessing import Pool, cpu_count
from typing import Optional, List, Tuple
from tqdm import tqdm

from ..core import (
    GameState,
    generate_legal_moves,
    apply_move,
    is_terminal,
    evaluate_terminal,
    zobrist_hash,
    init_zobrist_table,
)
from ..core.game_state import unpack_state
from ..storage import StorageBackend, Position
from ..utils import MemoryMonitor

logger = logging.getLogger(__name__)


# Global storage for worker processes
_worker_storage = None
_worker_num_pits = None


def _worker_init(backend_type: str, backend_params: dict, num_pits: int) -> None:
    """Initialize worker process with its own storage connection."""
    global _worker_storage, _worker_num_pits
    from ..storage import SQLiteBackend

    # Workers don't create schema - main process already did
    _worker_storage = SQLiteBackend(backend_params["db_path"], create_schema=False)
    _worker_num_pits = num_pits
    init_zobrist_table(num_pits)


def _worker_check_solvable(pos: Position) -> Tuple[int, bool]:
    """
    Worker: Check if a position is solvable (all children solved).

    Returns:
        (state_hash, is_solvable)
    """
    state = unpack_state(pos.state, _worker_num_pits)

    # Terminal positions are always solvable
    if is_terminal(state):
        return (pos.state_hash, True)

    # Check if all children are solved
    for move in generate_legal_moves(state):
        next_state = apply_move(state, move)
        next_hash = zobrist_hash(next_state)
        child_pos = _worker_storage.get(next_hash)

        if child_pos is None or child_pos.minimax_value is None:
            return (pos.state_hash, False)

    return (pos.state_hash, True)


def _worker_solve_position(pos: Position) -> Tuple[int, int, Optional[int]]:
    """
    Worker: Solve a single position's minimax value.

    Returns:
        (state_hash, minimax_value, best_move)
    """
    state = unpack_state(pos.state, _worker_num_pits)

    # Terminal state
    if is_terminal(state):
        value = evaluate_terminal(state)
        return (pos.state_hash, value, None)

    # Minimax search
    legal_moves = generate_legal_moves(state)
    is_maximizing = state.player == 0  # P1 maximizes

    best_value = float("-inf") if is_maximizing else float("inf")
    best_move = None

    for move in legal_moves:
        next_state = apply_move(state, move)
        next_hash = zobrist_hash(next_state)

        child_pos = _worker_storage.get(next_hash)
        if child_pos is None or child_pos.minimax_value is None:
            raise RuntimeError(
                f"Child not solved during parallel solve: hash={next_hash}"
            )

        child_value = child_pos.minimax_value

        if is_maximizing:
            if child_value > best_value:
                best_value = child_value
                best_move = move
        else:
            if child_value < best_value:
                best_value = child_value
                best_move = move

    return (pos.state_hash, best_value, best_move)


class ParallelMinimaxSolver:
    """
    Parallel retrograde minimax solver.

    Solves positions in parallel within each seed level iteration.
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        num_workers: int = None,
        enable_memory_monitoring: bool = True,
        batch_size: int = 100_000,
    ):
        """
        Initialize parallel minimax solver.

        Args:
            storage: Storage backend (must be file-based, not :memory:)
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            num_workers: Number of worker processes (default: CPU count)
            enable_memory_monitoring: Enable adaptive memory management
            batch_size: Number of positions to load per batch (prevents OOM on large seed levels)
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.num_workers = num_workers or cpu_count()
        self.max_seeds_in_pits = num_pits * 2 * num_seeds
        self.enable_memory_monitoring = enable_memory_monitoring
        self.batch_size = batch_size

        # Memory monitoring
        if enable_memory_monitoring:
            self.memory_monitor = MemoryMonitor(
                warning_threshold_gb=4.0, critical_threshold_gb=2.0
            )
            logger.info("Memory monitoring enabled for minimax")
        else:
            self.memory_monitor = None

        # Detect backend type and extract parameters for workers
        from ..storage import SQLiteBackend

        self.backend_type = "sqlite"
        self.backend_params = {"db_path": storage.db_path}

        logger.info(f"Using {self.num_workers} worker processes for minimax")
        logger.info(f"Backend: {self.backend_type}")
        logger.info(f"Batch size: {self.batch_size:,} positions per batch (memory-efficient streaming)")

    def solve(self) -> int:
        """
        Solve all positions using parallel retrograde minimax.

        Returns:
            Value of starting position
        """
        logger.info("Starting parallel retrograde minimax analysis")
        logger.info(f"Max seeds in pits: {self.max_seeds_in_pits}")

        with Pool(
            processes=self.num_workers,
            initializer=_worker_init,
            initargs=(self.backend_type, self.backend_params, self.num_pits),
        ) as pool:
            with tqdm(
                total=self.max_seeds_in_pits + 1, desc="Minimax", unit=" seed_layer"
            ) as pbar:
                for seeds_in_pits in range(0, self.max_seeds_in_pits + 1):
                    # Count total positions at this seed level
                    total_at_seed_level = self.storage.count_unsolved_positions(seeds_in_pits)

                    if total_at_seed_level == 0:
                        pbar.update(1)
                        continue

                    pbar.set_description(
                        f"Seeds-in-pits {seeds_in_pits} ({total_at_seed_level:,} positions)"
                    )

                    # Iterative solving within this seed level
                    # Process in batches to avoid loading billions of positions into RAM
                    iterations = 0
                    total_solved = 0

                    while True:
                        # Count remaining unsolved positions
                        unsolved_count = self.storage.count_unsolved_positions(seeds_in_pits)

                        if unsolved_count == 0:
                            break  # All positions at this seed level solved!
                        iterations += 1

                        # Memory monitoring - pause if critical
                        if self.memory_monitor:
                            if self.memory_monitor.is_critical():
                                logger.warning(
                                    "Critical memory pressure detected, pausing 10s for GC"
                                )
                                self.memory_monitor.log_status()
                                time.sleep(10)  # Give OS time to reclaim memory

                            # Adaptive chunksize based on memory pressure
                            if self.memory_monitor.should_throttle():
                                chunk_multiplier = 2
                            else:
                                chunk_multiplier = 4
                        else:
                            chunk_multiplier = 4

                        # Process unsolved positions in batches to avoid OOM
                        # ======================================================
                        # For Kalah(6,3), seed levels can have billions of positions
                        # Loading all into RAM would require 50-100GB
                        # Instead: stream batches from database, solve, update
                        batch_solved_count = 0
                        offset = 0
                        batch_num = 0

                        while True:
                            batch_num += 1

                            # Disk space check every 10 batches
                            if batch_num % 10 == 0:
                                self._check_disk_space()

                            # Fetch batch of unsolved positions
                            batch = self.storage.get_unsolved_positions_batch(
                                seeds_in_pits, limit=self.batch_size, offset=offset
                            )

                            if not batch:
                                break  # No more unsolved in this iteration

                            # Parallel check: which positions in this batch are solvable?
                            solvability_results = pool.map(
                                _worker_check_solvable,
                                batch,
                                chunksize=max(1, len(batch) // (self.num_workers * chunk_multiplier))
                            )

                            # Filter to solvable positions
                            solvable_positions = [
                                batch[i] for i, (_, solvable) in enumerate(solvability_results) if solvable
                            ]

                            # Parallel solve: compute minimax values for solvable positions
                            if solvable_positions:
                                solve_results = pool.map(
                                    _worker_solve_position,
                                    solvable_positions,
                                    chunksize=max(1, len(solvable_positions) // (self.num_workers * chunk_multiplier))
                                )

                                # Update storage with results
                                for state_hash, value, best_move in solve_results:
                                    self.storage.update_solution(state_hash, value, best_move)
                                    batch_solved_count += 1

                                self.storage.flush()

                            offset += self.batch_size

                        total_solved += batch_solved_count

                        if batch_solved_count == 0:
                            # No progress made in this iteration
                            raise RuntimeError(
                                f"Circular dependency at seeds_in_pits={seeds_in_pits}, "
                                f"{unsolved_count} positions remaining after {iterations} iterations"
                            )

                    logger.info(
                        f"Seeds-in-pits {seeds_in_pits}: solved {total_solved:,} "
                        f"positions in {iterations} iterations "
                        f"({self.num_workers} workers, {self.batch_size:,} batch size)"
                    )

                    pbar.update(1)

        # Get starting position value
        from ..core import create_starting_state

        start_state = create_starting_state(self.num_pits, self.num_seeds)
        start_hash = zobrist_hash(start_state)
        start_pos = self.storage.get(start_hash)

        if start_pos and start_pos.minimax_value is not None:
            logger.info(
                f"Game solved! Starting position value: {start_pos.minimax_value}"
            )
            logger.info(f"Best opening move: {start_pos.best_move}")
            return start_pos.minimax_value
        else:
            raise RuntimeError("Failed to solve starting position")

    def _check_disk_space(self):
        """
        Check disk space and raise exception if critically low.

        Escape hatch to prevent filling up the disk during long solves.
        """
        import shutil
        from pathlib import Path

        try:
            # Get database path from storage backend
            if hasattr(self.storage, 'db_path'):
                db_path = Path(self.storage.db_path)
            else:
                # Can't check disk without path
                return

            # Get disk usage stats
            stat = shutil.disk_usage(db_path.parent)
            free_gb = stat.free / (1024**3)
            percent_free = (stat.free / stat.total) * 100 if stat.total > 0 else 0

            # Critical threshold: 5GB or 5% free (whichever is more restrictive)
            MIN_FREE_GB = 5.0
            MIN_FREE_PERCENT = 5.0

            if free_gb < MIN_FREE_GB or percent_free < MIN_FREE_PERCENT:
                logger.error(
                    f"DISK SPACE CRITICALLY LOW! "
                    f"Free: {free_gb:.1f}GB ({percent_free:.1f}%) - "
                    f"Stopping solve to prevent disk full"
                )
                raise RuntimeError(
                    f"Disk space too low: {free_gb:.1f}GB free ({percent_free:.1f}%). "
                    f"Need at least {MIN_FREE_GB}GB or {MIN_FREE_PERCENT}% free. "
                    f"Stopping to prevent filling disk."
                )

            # Warning threshold: 10GB or 10% free
            WARN_FREE_GB = 10.0
            WARN_FREE_PERCENT = 10.0

            if free_gb < WARN_FREE_GB or percent_free < WARN_FREE_PERCENT:
                logger.warning(
                    f"Disk space getting low: {free_gb:.1f}GB free ({percent_free:.1f}%)"
                )
        except RuntimeError:
            # Re-raise disk full errors
            raise
        except Exception as e:
            # Log but don't crash on disk check errors
            logger.debug(f"Could not check disk space: {e}")

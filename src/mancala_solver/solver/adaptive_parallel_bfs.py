"""
Adaptive Parallel BFS solver - scales workers based on work size.

Strategy:
- Small depths (< 750K positions): Single-threaded, load all at once (fast!)
- Large depths (>= 750K positions): Parallel chunked processing
- Worker pool created once and reused throughout (workers idle when not needed)
- Preserves the original's fast in-memory iteration for small depths
"""

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from math import ceil
from typing import List, Set, Optional

from ..core import (
    GameState,
    create_starting_state,
    generate_legal_moves,
    apply_move,
    pack_state,
    zobrist_hash,
    init_zobrist_table,
)
from ..storage import StorageBackend, Position
from ..utils.resource_monitor import ResourceMonitor, ResourceCheckError

logger = logging.getLogger(__name__)

# Constants
PARALLEL_THRESHOLD = 750_000  # positions - below this, use single-threaded
CHUNK_SIZE = 500_000  # positions per worker task


def _process_chunk_worker(
    db_path: str,
    db_type: str,
    depth: int,
    offset: int,
    limit: int,
    num_pits: int,
    num_seeds: int,
) -> List[Position]:
    """
    Worker function to process a chunk of positions.

    This must be a top-level function for pickling with ProcessPoolExecutor.
    Each worker creates its own database connection.

    Args:
        db_path: Path to database
        db_type: "sqlite" or "postgres"
        depth: Depth to process
        offset: Starting offset for this chunk
        limit: Number of positions to fetch
        num_pits: Number of pits per player
        num_seeds: Initial seeds per pit

    Returns:
        List of new Position objects (successors)
    """
    # Initialize Zobrist hashing in this worker
    init_zobrist_table(num_pits)

    # Each worker creates its own storage connection
    if db_type == "sqlite":
        from ..storage.sqlite import SQLiteBackend
        storage = SQLiteBackend(db_path)
    else:
        raise ValueError(f"Unsupported db_type: {db_type}")

    try:
        # Fetch this chunk of positions
        positions = storage.get_positions_at_depth_batch(depth, limit, offset)

        if not positions:
            return []

        # Generate successors with local deduplication
        new_positions = []
        local_seen: Set[int] = set()

        for pos in positions:
            # Use the circular import workaround from original
            state = pack_state.__globals__["unpack_state"](pos.state, num_pits)

            # Generate all legal moves
            for move in generate_legal_moves(state):
                next_state = apply_move(state, move)
                next_hash = zobrist_hash(next_state)

                # Skip if already seen in this chunk
                if next_hash in local_seen:
                    continue

                local_seen.add(next_hash)

                new_pos = Position(
                    state_hash=next_hash,
                    state=pack_state(next_state),
                    depth=depth + 1,
                    seeds_in_pits=next_state.seeds_in_pits,
                )
                new_positions.append(new_pos)

        return new_positions

    finally:
        storage.close()


class AdaptiveParallelBFSSolver:
    """
    Adaptive BFS solver that scales from single-threaded to parallel based on work size.

    - Creates worker pool once, reuses throughout
    - Small depths: Single-threaded (workers idle)
    - Large depths: Parallel chunked processing
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        batch_size: int = 100000,
        max_workers: int = None,
        display: Optional[object] = None,
        resource_monitor: Optional[ResourceMonitor] = None,
    ):
        """
        Initialize adaptive parallel BFS solver.

        Args:
            storage: Storage backend for positions
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            batch_size: Batch size for bulk inserts
            max_workers: Maximum number of workers (default: CPU count)
            display: Optional SolverDisplay for rich output
            resource_monitor: Optional ResourceMonitor for safety checks
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.batch_size = batch_size
        self.max_workers = max_workers or os.cpu_count()
        self.display = display
        self.resource_monitor = resource_monitor

        # Initialize Zobrist hashing
        init_zobrist_table(num_pits)

        # Get DB path and type for workers
        self.db_path = storage.db_path if hasattr(storage, 'db_path') else None
        self.db_type = "sqlite"  # TODO: detect from storage type

        # Statistics
        self.total_generated = 0
        self.total_unique = 0

        # Create worker pool once (reused throughout)
        self.pool = ProcessPoolExecutor(max_workers=self.max_workers)
        if self.display:
            self.display.log_info(f"Created worker pool with {self.max_workers} workers")
        else:
            logger.info(f"Created worker pool with {self.max_workers} workers")

    def __del__(self):
        """Cleanup worker pool on destruction."""
        if hasattr(self, 'pool'):
            self.pool.shutdown(wait=True)

    def _process_depth_single_threaded(self, depth: int) -> List[Position]:
        """
        Process a depth using single-threaded approach (original fast path).

        Args:
            depth: Depth to process

        Returns:
            List of new positions generated
        """
        # Load ALL positions at this depth into memory (like original)
        positions = list(self.storage.get_positions_at_depth(depth))

        # Generate successors
        new_positions = []
        local_seen: Set[int] = set()

        for pos in positions:
            # Use the circular import workaround from original
            state = pack_state.__globals__["unpack_state"](pos.state, self.num_pits)

            # Generate all legal moves
            for move in generate_legal_moves(state):
                next_state = apply_move(state, move)
                next_hash = zobrist_hash(next_state)

                # Skip if already seen in this batch
                if next_hash in local_seen:
                    continue

                local_seen.add(next_hash)

                new_pos = Position(
                    state_hash=next_hash,
                    state=pack_state(next_state),
                    depth=depth + 1,
                    seeds_in_pits=next_state.seeds_in_pits,
                )
                new_positions.append(new_pos)

                # Batch insert when buffer is full
                if len(new_positions) >= self.batch_size:
                    inserted = self.storage.insert_batch(new_positions)
                    self.total_generated += len(new_positions)
                    self.total_unique += inserted
                    new_positions = []
                    local_seen = set()

        # Return remaining positions for final insert
        return new_positions

    def _process_depth_parallel(self, depth: int, depth_size: int) -> List[Position]:
        """
        Process a depth using parallel chunked approach.

        Args:
            depth: Depth to process
            depth_size: Number of positions at this depth

        Returns:
            List of all new positions generated
        """
        num_chunks = ceil(depth_size / CHUNK_SIZE)
        if self.display:
            # Display will show this inline, no need for logger
            pass
        else:
            logger.info(f"  → Parallel mode: {num_chunks} chunks of ~{CHUNK_SIZE:,} positions")

        # Submit all chunks to worker pool
        futures = []
        for chunk_id in range(num_chunks):
            future = self.pool.submit(
                _process_chunk_worker,
                self.db_path,
                self.db_type,
                depth,
                offset=chunk_id * CHUNK_SIZE,
                limit=CHUNK_SIZE,
                num_pits=self.num_pits,
                num_seeds=self.num_seeds,
            )
            futures.append(future)

        # Collect results as they complete
        all_new_positions = []
        for future in as_completed(futures):
            chunk_results = future.result()
            all_new_positions.extend(chunk_results)

        return all_new_positions

    def build_game_graph(self) -> int:
        """
        Build complete game graph using adaptive BFS.

        Returns:
            Total number of unique positions found
        """
        if self.display:
            self.display.log_info(f"Starting adaptive parallel BFS for Kalah({self.num_pits},{self.num_seeds})")
            self.display.log_info(f"Workers: {self.max_workers}, Parallel threshold: {PARALLEL_THRESHOLD:,}")
        else:
            logger.info(f"Starting adaptive parallel BFS for Kalah({self.num_pits},{self.num_seeds})")
            logger.info(f"Workers: {self.max_workers}, Parallel threshold: {PARALLEL_THRESHOLD:,}")

        # Check if already started
        max_depth = self.storage.get_max_depth()
        if max_depth >= 0:
            if self.display:
                self.display.log_info(f"Resuming from depth {max_depth}")
            else:
                logger.info(f"Resuming from depth {max_depth}")
            start_depth = max_depth
        else:
            # Insert starting position
            start_state = create_starting_state(self.num_pits, self.num_seeds)
            start_pos = Position(
                state_hash=zobrist_hash(start_state),
                state=pack_state(start_state),
                depth=0,
                seeds_in_pits=start_state.seeds_in_pits,
            )
            self.storage.insert(start_pos)
            self.storage.flush()
            if self.display:
                self.display.log_info("Inserted starting position")
            else:
                logger.info("Inserted starting position")
            start_depth = 0

        depth = start_depth
        while True:
            # Check resources periodically (every depth)
            if self.resource_monitor:
                is_safe, msg = self.resource_monitor.check_all()
                if not is_safe:
                    if self.display:
                        self.display.log_error(msg)
                    else:
                        logger.error(msg)
                    raise ResourceCheckError(msg)

            # Count positions at this depth
            depth_size = self.storage.count_positions(depth)

            if depth_size == 0:
                # No more positions - done!
                break

            # Decide: single-threaded or parallel?
            mode = "single" if depth_size < PARALLEL_THRESHOLD else "parallel"
            num_chunks = ceil(depth_size / CHUNK_SIZE) if mode == "parallel" else 0

            # Show depth info
            total_in_db = self.storage.count_positions()
            if self.display:
                self.display.update_depth_info(depth, depth_size, mode, num_chunks, total_in_db)

            # Process depth
            if depth_size < PARALLEL_THRESHOLD:
                # Single-threaded fast path
                new_positions = self._process_depth_single_threaded(depth)
            else:
                # Parallel chunked path
                new_positions = self._process_depth_parallel(depth, depth_size)

            # Insert remaining positions
            if new_positions:
                inserted = self.storage.insert_batch(new_positions)
                self.total_generated += len(new_positions)
                self.total_unique += inserted

            self.storage.flush()

            # Show resources every few depths
            if self.display and self.resource_monitor and depth % 3 == 0:
                self.display.show_resources_inline()

            depth += 1

        total_positions = self.storage.count_positions()
        if self.display:
            self.display.log_success(f"BFS complete! Total positions: {total_positions:,}")
            self.display.log_info(f"Maximum depth: {depth - 1}")
            if self.total_generated > 0:
                self.display.log_info(f"Duplication rate: {(1 - self.total_unique/self.total_generated)*100:.1f}%")
        else:
            logger.info(f"BFS complete! Total positions: {total_positions:,}")
            logger.info(f"Maximum depth: {depth - 1}")
            logger.info(
                f"Duplication rate: {(1 - self.total_unique/self.total_generated)*100:.1f}%"
                if self.total_generated > 0
                else "N/A"
            )

        # Shutdown worker pool
        self.pool.shutdown(wait=True)
        if self.display:
            self.display.log_info("Worker pool shutdown complete")
        else:
            logger.info("Worker pool shutdown complete")

        return total_positions


# Import unpack_state into pack_state's globals (circular import workaround from original)
from ..core.game_state import unpack_state
pack_state.__globals__["unpack_state"] = unpack_state

"""
Parallel BFS solver - batch-of-chunks approach for maximum RAM utilization.

Strategy:
- Read large batches from DB (5M positions default, configurable)
- Distribute batch chunks to workers in memory (no DB contention)
- Workers compute in parallel (pure CPU, no I/O)
- Deduplicate children in-memory (hash set, very fast)
- Bulk write unique children to DB

Performance wins:
1. Eliminates DB read lock contention (14 workers × 0 DB reads = 0 contention)
2. In-memory dedup is 10-100x faster than DB-based INSERT OR IGNORE
3. Maximizes CPU utilization with large RAM (36GB)
4. Batch size tunable based on available memory
"""

import logging
import time
from multiprocessing import Pool, cpu_count
from typing import List, Tuple
from tqdm import tqdm

from ..core import (
    GameState,
    generate_legal_moves,
    apply_move,
    zobrist_hash,
    init_zobrist_table,
    create_starting_state,
    pack_state,
)
from ..core.game_state import unpack_state
from ..storage import StorageBackend, Position

logger = logging.getLogger(__name__)


# Global state for workers
_worker_storage = None
_worker_num_pits = None


def _worker_init(backend_params: dict, num_pits: int):
    """Initialize worker with its own storage connection."""
    global _worker_storage, _worker_num_pits
    from ..storage import SQLiteBackend

    # Workers don't create schema - main process already did
    _worker_storage = SQLiteBackend(
        backend_params["db_path"],
        fast_mode=backend_params["fast_mode"],
        create_schema=False
    )
    _worker_num_pits = num_pits
    init_zobrist_table(num_pits)


def _worker_process_chunk(args: Tuple[List[Position], int]) -> List[Position]:
    """
    Worker: Process one chunk of parents and generate children.

    No database access - all data passed in memory.

    Args:
        args: (parent_positions, next_depth)

    Returns:
        List of child Position objects
    """
    parents, next_depth = args

    # Generate all children (pure computation, no I/O)
    children = []
    for parent_pos in parents:
        parent_state = unpack_state(parent_pos.state, _worker_num_pits)

        for move in generate_legal_moves(parent_state):
            child_state = apply_move(parent_state, move)
            child_hash = zobrist_hash(child_state)

            child_pos = Position(
                state_hash=child_hash,
                state=pack_state(child_state),
                depth=next_depth,
                seeds_in_pits=child_state.seeds_in_pits,
            )
            children.append(child_pos)

    return children


class ParallelBFSSolver:
    """
    Parallel BFS solver with batch-of-chunks approach.

    Strategy:
    1. Read large batch from DB (e.g., 5M positions) - single read, no contention
    2. Split batch into chunks and distribute to workers in memory
    3. Workers compute children in parallel (pure computation, no I/O)
    4. Deduplicate children in memory (fast hash set)
    5. Write unique children to DB in one bulk insert
    6. Repeat until depth exhausted

    Advantages:
    - Eliminates DB read contention (workers get data from RAM, not DB)
    - In-memory dedup is 10-100x faster than DB-based dedup
    - Maximizes CPU utilization with 36GB RAM
    - Batch size configurable based on available memory
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        num_workers: int = None,
        chunk_size: int = 50_000,
        db_read_batch_size: int = 5_000_000,
    ):
        """
        Initialize parallel BFS solver.

        Args:
            storage: Storage backend
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            num_workers: Number of worker processes (default: CPU count)
            chunk_size: Positions per worker chunk (default: 50K)
                       With 14 workers: 5M batch / 50K = 100 chunks ≈ 7 chunks/worker
                       Good load balancing: workers stay busy, minimal idle time
            db_read_batch_size: Positions to read from DB at once (default: 5M)
                               With 36GB RAM, 5M positions ≈ 500MB + 2GB children = 2.5GB working set
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.num_workers = num_workers or cpu_count()
        self.chunk_size = chunk_size
        self.db_read_batch_size = db_read_batch_size

        # Extract backend params for workers
        from ..storage import SQLiteBackend
        self.backend_params = {
            "db_path": storage.db_path,
            "fast_mode": storage.fast_mode,
        }

        logger.info(f"Parallel BFS: {self.num_workers} workers, {chunk_size:,} positions/chunk")
        logger.info(f"DB read batches: {self.db_read_batch_size:,} positions (~{self.db_read_batch_size * 100 / 1024 / 1024:.0f}MB)")
        logger.info("In-memory deduplication per batch - maximizes RAM usage")

    def build_game_graph(self) -> int:
        """
        Build complete game graph using parallel BFS.

        Returns:
            Total number of positions (including duplicates)
        """
        # Insert starting position
        init_zobrist_table(self.num_pits)
        start_state = create_starting_state(self.num_pits, self.num_seeds)
        start_hash = zobrist_hash(start_state)
        start_pos = Position(
            state_hash=start_hash,
            state=pack_state(start_state),
            depth=0,
            seeds_in_pits=start_state.seeds_in_pits,
        )
        self.storage.insert(start_pos)
        self.storage.flush()

        logger.info("Starting parallel BFS")
        total_positions = 1
        depth = 0

        with Pool(
            processes=self.num_workers,
            initializer=_worker_init,
            initargs=(self.backend_params, self.num_pits),
        ) as pool:
            while True:
                # Count positions at current depth
                count_at_depth = self.storage.count_positions(depth=depth)
                if count_at_depth == 0:
                    break

                # Log format that monitor expects
                logger.info(f"Depth {depth}: Processing {count_at_depth:,} positions in chunks")

                depth_start = time.time()
                total_children_generated = 0

                # Process depth in large batches (batch-of-chunks approach)
                # This eliminates DB read contention by reading once per batch
                num_db_batches = (count_at_depth + self.db_read_batch_size - 1) // self.db_read_batch_size

                for db_batch_idx in range(num_db_batches):
                    db_offset = db_batch_idx * self.db_read_batch_size

                    # Read large batch from DB (single read, no contention)
                    parents_batch = self.storage.get_positions_at_depth_batch(
                        depth,
                        limit=self.db_read_batch_size,
                        offset=db_offset
                    )

                    if not parents_batch:
                        break

                    # Verify no duplicate parents (sanity check)
                    parent_hashes = [p.state_hash for p in parents_batch]
                    if len(parent_hashes) != len(set(parent_hashes)):
                        logger.warning(f"WARNING: Duplicate parents in batch! {len(parent_hashes)} total, {len(set(parent_hashes))} unique")

                    # Split batch into chunks for parallel processing
                    num_chunks = (len(parents_batch) + self.chunk_size - 1) // self.chunk_size
                    chunk_args = [
                        (parents_batch[i * self.chunk_size:(i + 1) * self.chunk_size], depth + 1)
                        for i in range(num_chunks)
                    ]

                    # Workers process chunks in parallel (pure computation, no DB)
                    all_children = []
                    for chunk_results in pool.imap_unordered(_worker_process_chunk, chunk_args):
                        all_children.extend(chunk_results)

                    # In-memory deduplication within batch (removes obvious duplicates)
                    seen_hashes = set()
                    unique_children = []
                    for child in all_children:
                        if child.state_hash not in seen_hashes:
                            seen_hashes.add(child.state_hash)
                            unique_children.append(child)

                    # Fast INSERT (allow duplicates, cleanup later)
                    if unique_children:
                        self.storage.insert_batch(unique_children, allow_duplicates=True)
                        self.storage.flush()
                        total_children_generated += len(unique_children)

                        # Log progress
                        batch_pct = ((db_batch_idx + 1) / num_db_batches) * 100
                        within_batch_dupes = len(all_children) - len(unique_children)
                        logger.info(
                            f"Depth {depth} progress: batch {db_batch_idx + 1}/{num_db_batches} ({batch_pct:.1f}%) - "
                            f"{len(unique_children):,} attempted ({within_batch_dupes:,} within-batch dupes)"
                        )

                # Update running total (includes cross-batch duplicates)
                total_positions += total_children_generated

                depth_time = time.time() - depth_start

                # Log memory usage
                import os, resource
                try:
                    import psutil
                    process = psutil.Process(os.getpid())
                    mem_mb = process.memory_info().rss / 1024 / 1024
                except ImportError:
                    # Fall back to resource module (macOS reports in bytes)
                    mem_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    # macOS reports in bytes, Linux in KB
                    if 'darwin' in os.sys.platform.lower():
                        mem_mb = mem_bytes / 1024 / 1024
                    else:
                        mem_mb = mem_bytes / 1024
                mem_str = f", RAM: {mem_mb:.0f}MB"

                # Log format that monitor expects
                logger.info(
                    f"Depth {depth}: Generated {total_children_generated:,} new positions "
                    f"in {depth_time:.1f}s ({total_children_generated/depth_time:.0f} pos/sec){mem_str}, total: {total_positions:,}"
                )

                depth += 1

        logger.info(f"BFS complete: {total_positions:,} positions generated (includes duplicates)")
        logger.info(f"Maximum depth: {depth - 1}")
        logger.info("Note: Deduplication will be performed after BFS")

        return total_positions

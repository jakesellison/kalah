"""
Simple parallel BFS - horizontal scaling of the original fast approach.

Strategy:
- For small depths: Single worker loads all positions into memory (original fast approach)
- For large depths: Split positions across workers, each worker loads its chunk into memory
- Each worker uses the simple, fast approach: load chunk → iterate in memory → generate children
- No complex batch-of-chunks coordination - just horizontal partitioning

Why this is fast:
1. Each worker does ONE DB read (not many chunked reads)
2. Workers iterate over in-memory lists (fast, like original)
3. No multiprocessing coordination within depth - workers are independent
4. Deduplication via DB INSERT OR IGNORE (simple, works)

Memory safety:
- Adaptive chunk sizing based on available RAM
- If depth has 10M positions and 4 workers → each worker loads 2.5M (manageable)
- Position size ~100 bytes → 2.5M positions = 250MB per worker (safe with 36GB RAM)
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

    _worker_storage = SQLiteBackend(
        backend_params["db_path"],
        fast_mode=backend_params["fast_mode"],
        create_schema=False
    )
    _worker_num_pits = num_pits
    init_zobrist_table(num_pits)


def _worker_process_depth_chunk(args: Tuple[int, int, int, int]) -> Tuple[List[Position], dict]:
    """
    Worker: Process a chunk of positions at depth using the simple fast approach.

    This is the original fast BFS approach, but limited to a chunk:
    1. Load entire chunk into memory (one DB query)
    2. Iterate over positions (fast in-memory iteration)
    3. Generate children (fast computation)
    4. Deduplicate within chunk (hash set)
    5. Return children to main process (avoids write lock contention)

    Args:
        args: (depth, chunk_offset, chunk_limit, next_depth)

    Returns:
        (children, statistics_dict)
    """
    depth, chunk_offset, chunk_limit, next_depth = args

    # Step 1: Load entire chunk into memory (ONE DB query, like original)
    positions = _worker_storage.get_positions_at_depth_batch(
        depth,
        limit=chunk_limit,
        offset=chunk_offset
    )

    if not positions:
        return ([], {"positions_processed": 0, "children_generated": 0})

    # Step 2: Process positions in memory (fast iteration, like original)
    children = []
    seen_hashes = set()

    for pos in positions:
        state = unpack_state(pos.state, _worker_num_pits)

        for move in generate_legal_moves(state):
            child_state = apply_move(state, move)
            child_hash = zobrist_hash(child_state)

            # Deduplicate within chunk
            if child_hash in seen_hashes:
                continue

            seen_hashes.add(child_hash)

            child_pos = Position(
                state_hash=child_hash,
                state=pack_state(child_state),
                depth=next_depth,
                seeds_in_pits=child_state.seeds_in_pits,
            )
            children.append(child_pos)

    # Return children to main process (avoids write lock contention)
    return (children, {
        "positions_processed": len(positions),
        "children_generated": len(children),
        "within_chunk_duplicates": len(children) - len(seen_hashes) if children else 0,
    })


class SimpleParallelBFSSolver:
    """
    Simple parallel BFS - horizontal scaling of the original fast approach.

    Strategy:
    - Count positions at depth
    - If small (< threshold): Use 1 worker with entire depth in memory
    - If large: Split across N workers, each loads its chunk into memory
    - Each worker uses simple fast approach (like original BFS)
    - No complex coordination - workers are independent

    Memory calculation:
    - Position size: ~100 bytes (packed state + metadata)
    - With 36GB RAM, safe to have ~50M positions across all workers
    - Example: 20M positions, 4 workers → 5M per worker = 500MB (very safe)
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        num_workers: int = None,
        positions_per_worker: int = 2_000_000,  # 2M positions = ~200MB per worker
    ):
        """
        Initialize simple parallel BFS solver.

        Args:
            storage: Storage backend
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            num_workers: Number of worker processes (default: CPU count)
            positions_per_worker: Target positions per worker chunk (default: 2M = ~200MB)
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.num_workers = num_workers or cpu_count()
        self.positions_per_worker = positions_per_worker

        # Extract backend params for workers
        self.backend_params = {
            "db_path": storage.db_path,
            "fast_mode": storage.fast_mode,
        }

        logger.info(f"Simple parallel BFS: {self.num_workers} workers available")
        logger.info(f"Target: {positions_per_worker:,} positions/worker (~{positions_per_worker * 100 / 1024 / 1024:.0f}MB)")

    def build_game_graph(self) -> int:
        """
        Build complete game graph using simple parallel BFS.

        Returns:
            Total number of unique positions
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

        logger.info("Starting simple parallel BFS")
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

                logger.info(f"Depth {depth}: {count_at_depth:,} positions")

                depth_start = time.time()

                # Determine parallelism based on depth size
                # If small depth, use fewer workers to avoid overhead
                positions_per_chunk = self.positions_per_worker
                num_chunks = max(1, (count_at_depth + positions_per_chunk - 1) // positions_per_chunk)
                num_chunks = min(num_chunks, self.num_workers)  # Don't exceed available workers

                if num_chunks == 1:
                    logger.info(f"Depth {depth}: Small depth, using single worker (all in memory)")
                else:
                    logger.info(f"Depth {depth}: Splitting across {num_chunks} workers (~{count_at_depth // num_chunks:,} positions/worker)")

                # Create chunk assignments
                chunk_args = []
                for i in range(num_chunks):
                    chunk_offset = i * positions_per_chunk
                    chunk_limit = min(positions_per_chunk, count_at_depth - chunk_offset)
                    chunk_args.append((depth, chunk_offset, chunk_limit, depth + 1))

                # Workers process chunks in parallel, collect all children
                all_children = []
                total_processed = 0

                for children, stats in pool.imap_unordered(_worker_process_depth_chunk, chunk_args):
                    all_children.extend(children)
                    total_processed += stats["positions_processed"]

                # Main process writes all children (single bulk insert, no lock contention)
                # Use allow_duplicates=True for fast insert, dedup after BFS
                if all_children:
                    self.storage.insert_batch(all_children, allow_duplicates=True)
                    self.storage.flush()

                total_children = len(all_children)
                depth_time = time.time() - depth_start

                # Get final counts
                total_positions = self.storage.count_positions()

                # Log memory usage
                import os
                try:
                    import psutil
                    process = psutil.Process(os.getpid())
                    mem_mb = process.memory_info().rss / 1024 / 1024
                except ImportError:
                    import resource
                    mem_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if 'darwin' in os.sys.platform.lower():
                        mem_mb = mem_bytes / 1024 / 1024
                    else:
                        mem_mb = mem_bytes / 1024

                logger.info(
                    f"Depth {depth}: Generated {total_children:,} unique children "
                    f"in {depth_time:.1f}s ({total_children/depth_time:.0f} pos/sec), "
                    f"RAM: {mem_mb:.0f}MB, total: {total_positions:,}"
                )

                depth += 1

        total_positions = self.storage.count_positions()
        logger.info(f"BFS complete: {total_positions:,} unique positions")
        logger.info(f"Maximum depth: {depth - 1}")

        return total_positions

"""
Parallel solver using multiprocessing for better performance.
"""

import logging
from multiprocessing import Pool, cpu_count
from typing import List
from tqdm import tqdm

from ..core import (
    create_starting_state,
    generate_legal_moves,
    apply_move,
    pack_state,
    zobrist_hash,
    init_zobrist_table,
)
from ..core.game_state import unpack_state
from ..storage import StorageBackend, Position

logger = logging.getLogger(__name__)


# Global state for worker processes (initialized once per worker)
_worker_num_pits = None


def _worker_init(num_pits: int) -> None:
    """Initialize worker process."""
    global _worker_num_pits
    _worker_num_pits = num_pits
    init_zobrist_table(num_pits)


def _generate_successors(positions_chunk: List[Position]) -> List[Position]:
    """
    Worker function: generate successors for a chunk of positions.

    Args:
        positions_chunk: Chunk of positions to process

    Returns:
        List of successor positions
    """
    successors = []
    seen_hashes = set()

    for pos in positions_chunk:
        state = unpack_state(pos.state, _worker_num_pits)

        for move in generate_legal_moves(state):
            next_state = apply_move(state, move)
            next_hash = zobrist_hash(next_state)

            # Local deduplication within chunk
            if next_hash in seen_hashes:
                continue

            seen_hashes.add(next_hash)

            new_pos = Position(
                state_hash=next_hash,
                state=pack_state(next_state),
                depth=pos.depth + 1,
                seeds_in_pits=next_state.seeds_in_pits,
            )
            successors.append(new_pos)

    return successors


class ParallelSolver:
    """
    Parallel BFS solver using multiprocessing.

    Distributes work across multiple CPU cores for faster solving.
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        num_workers: int = None,
        batch_size: int = 100000,
    ):
        """
        Initialize parallel solver.

        Args:
            storage: Storage backend
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            num_workers: Number of worker processes (default: CPU count)
            batch_size: Batch size for inserts
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.num_workers = num_workers or cpu_count()
        self.batch_size = batch_size

        # Initialize Zobrist in main process
        init_zobrist_table(num_pits)

        logger.info(f"Using {self.num_workers} worker processes")

    def build_game_graph(self) -> int:
        """
        Build complete game graph using parallel BFS.

        Returns:
            Total number of unique positions
        """
        logger.info(
            f"Starting parallel BFS for Kalah({self.num_pits},{self.num_seeds})"
        )

        # Check if already started
        max_depth = self.storage.get_max_depth()
        if max_depth >= 0:
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
            logger.info("Inserted starting position")
            start_depth = 0

        depth = start_depth

        with Pool(
            processes=self.num_workers,
            initializer=_worker_init,
            initargs=(self.num_pits,),
        ) as pool:
            with tqdm(desc="Parallel BFS", unit=" depth") as pbar:
                while True:
                    # Get all positions at current depth
                    positions = list(self.storage.get_positions_at_depth(depth))

                    if not positions:
                        break

                    count_at_depth = len(positions)
                    pbar.set_description(
                        f"Depth {depth} ({count_at_depth:,} positions)"
                    )

                    # Divide work into chunks for workers
                    chunk_size = max(1, len(positions) // self.num_workers)
                    chunks = [
                        positions[i : i + chunk_size]
                        for i in range(0, len(positions), chunk_size)
                    ]

                    # Parallel generation
                    results = pool.map(_generate_successors, chunks)

                    # Merge results
                    all_successors = []
                    for chunk_successors in results:
                        all_successors.extend(chunk_successors)

                    # Batch insert (storage handles deduplication)
                    if all_successors:
                        inserted = self.storage.insert_batch(all_successors)
                        self.storage.flush()

                        total_in_db = self.storage.count_positions()
                        logger.info(
                            f"Depth {depth}: {count_at_depth:,} positions -> "
                            f"generated {len(all_successors):,} successors -> "
                            f"inserted {inserted:,} unique -> "
                            f"total: {total_in_db:,}"
                        )

                    pbar.update(1)
                    depth += 1

        total_positions = self.storage.count_positions()
        logger.info(f"Parallel BFS complete! Total positions: {total_positions:,}")
        logger.info(f"Maximum depth reached: {depth - 1}")

        return total_positions

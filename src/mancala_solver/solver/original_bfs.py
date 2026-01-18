"""
Original simple BFS solver - recreation of the fast ~100s approach.

This is the original implementation that achieved ~100s on Kalah(4,3).
Single-threaded, loads all positions at each depth into memory.
"""

import logging
import time
from typing import Set

from ..core import (
    GameState,
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


class OriginalBFSSolver:
    """
    Original simple BFS solver - recreation for baseline performance testing.

    Strategy:
    - Load ALL positions at each depth into memory
    - Iterate in memory (very fast)
    - Generate children with local deduplication
    - Batch insert with INSERT OR IGNORE (DB-based cross-batch dedup)

    This is simple, fast for problems that fit in RAM.
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        batch_size: int = 100000,
    ):
        """
        Initialize original BFS solver.

        Args:
            storage: Storage backend for positions
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            batch_size: Batch size for bulk inserts
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.batch_size = batch_size

        # Initialize Zobrist hashing
        init_zobrist_table(num_pits)

    def build_game_graph(self) -> int:
        """
        Build complete game graph using BFS.

        Returns:
            Total number of unique positions found
        """
        logger.info(f"Starting original BFS for Kalah({self.num_pits},{self.num_seeds})")

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

        depth = 0
        total_time = 0

        while True:
            depth_start = time.time()

            # KEY: Load ALL positions at this depth into memory (like original)
            positions = list(self.storage.get_positions_at_depth(depth))

            if not positions:
                # No more positions - done!
                break

            count_at_depth = len(positions)
            logger.info(f"Depth {depth}: {count_at_depth:,} positions")

            # Generate successors
            new_positions = []
            local_seen: Set[int] = set()  # Deduplicate within batch

            for pos in positions:
                state = unpack_state(pos.state, self.num_pits)

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
                        self.storage.insert_batch(new_positions, )
                        new_positions = []
                        local_seen = set()

            # Insert remaining positions
            if new_positions:
                self.storage.insert_batch(new_positions, )

            # Flush commits + auto-checkpoints WAL if > 1GB (memory-based)
            self.storage.flush()

            depth_time = time.time() - depth_start
            total_time += depth_time

            total_in_db = self.storage.count_positions()
            logger.info(
                f"Depth {depth}: Generated {len(local_seen):,} unique children "
                f"in {depth_time:.1f}s ({len(local_seen)/depth_time:.0f} pos/sec), "
                f"total: {total_in_db:,}"
            )

            depth += 1

        total_positions = self.storage.count_positions()
        logger.info(f"BFS complete! Total positions: {total_positions:,}")
        logger.info(f"Maximum depth: {depth - 1}")
        logger.info(f"Total BFS time: {total_time:.1f}s")

        return total_positions

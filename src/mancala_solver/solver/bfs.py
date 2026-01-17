"""
Breadth-First Search solver for building complete game graph.
"""

import logging
from typing import Set
from tqdm import tqdm

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

logger = logging.getLogger(__name__)


class BFSSolver:
    """
    BFS-based game graph builder.

    Explores the game tree level-by-level (by depth), generating all
    reachable positions from the starting state.
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        batch_size: int = 100000,
    ):
        """
        Initialize BFS solver.

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

        # Statistics
        self.total_generated = 0
        self.total_unique = 0

    def build_game_graph(self) -> int:
        """
        Build complete game graph using BFS.

        Returns:
            Total number of unique positions found
        """
        logger.info(f"Starting BFS for Kalah({self.num_pits},{self.num_seeds})")

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
        with tqdm(desc="BFS", unit=" depth") as pbar:
            while True:
                # Get all positions at current depth
                positions = list(self.storage.get_positions_at_depth(depth))

                if not positions:
                    # No more positions - done!
                    break

                count_at_depth = len(positions)
                pbar.set_description(f"Depth {depth} ({count_at_depth:,} positions)")

                # Generate successors
                new_positions = []
                local_seen: Set[int] = set()  # Deduplicate within batch

                for pos in positions:
                    state = pack_state.__globals__["unpack_state"](
                        pos.state, self.num_pits
                    )

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

                # Insert remaining positions
                if new_positions:
                    inserted = self.storage.insert_batch(new_positions)
                    self.total_generated += len(new_positions)
                    self.total_unique += inserted

                self.storage.flush()

                total_in_db = self.storage.count_positions()
                logger.info(
                    f"Depth {depth}: {count_at_depth:,} positions -> "
                    f"generated {len(new_positions):,} successors -> "
                    f"total in DB: {total_in_db:,}"
                )

                pbar.update(1)
                depth += 1

        total_positions = self.storage.count_positions()
        logger.info(f"BFS complete! Total positions: {total_positions:,}")
        logger.info(f"Maximum depth: {depth - 1}")
        logger.info(
            f"Duplication rate: {(1 - self.total_unique/self.total_generated)*100:.1f}%"
            if self.total_generated > 0
            else "N/A"
        )

        return total_positions


# Import unpack_state into pack_state's globals (circular import workaround)
from ..core.game_state import unpack_state

pack_state.__globals__["unpack_state"] = unpack_state

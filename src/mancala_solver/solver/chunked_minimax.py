"""
Chunked parallel minimax solver for cloud databases.

Processes each seed level in chunks with parallel workers to bound
memory usage and enable progress tracking.
"""

import logging
from typing import Dict, List, Optional, Tuple
from multiprocessing import Pool, cpu_count
from tqdm import tqdm

from ..core import (
    GameState,
    generate_legal_moves,
    apply_move,
    is_terminal,
    evaluate_terminal,
    zobrist_hash,
    init_zobrist_table,
    create_starting_state,
)
from ..core.game_state import unpack_state
from ..storage import StorageBackend, Position

logger = logging.getLogger(__name__)


# Global storage for worker processes
_worker_storage = None
_worker_num_pits = None
_worker_backend_type = None
_worker_backend_params = None


def _worker_init(backend_type: str, backend_params: dict, num_pits: int) -> None:
    """Initialize worker process with its own storage connection."""
    global _worker_storage, _worker_num_pits, _worker_backend_type, _worker_backend_params
    from ..storage import SQLiteBackend, PostgreSQLBackend

    if backend_type == "sqlite":
        _worker_storage = SQLiteBackend(backend_params["db_path"])
    elif backend_type == "postgresql":
        _worker_storage = PostgreSQLBackend(**backend_params)
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")

    _worker_num_pits = num_pits
    _worker_backend_type = backend_type
    _worker_backend_params = backend_params
    init_zobrist_table(num_pits)


def _worker_solve_position(args: Tuple[Position, Dict[int, int]]) -> Tuple[int, int, Optional[int]]:
    """
    Worker: Solve a single position using provided child cache.

    Args:
        args: (position, child_cache) where child_cache is {hash -> minimax_value}

    Returns:
        (state_hash, minimax_value, best_move)
    """
    pos, child_cache = args
    state = unpack_state(pos.state, _worker_num_pits)

    # Terminal state
    if is_terminal(state):
        value = evaluate_terminal(state)
        return (pos.state_hash, value, None)

    # Minimax search using cache
    legal_moves = generate_legal_moves(state)
    is_maximizing = state.player == 0  # P1 maximizes

    best_value = float("-inf") if is_maximizing else float("inf")
    best_move = None

    for move in legal_moves:
        next_state = apply_move(state, move)
        next_hash = zobrist_hash(next_state)

        # Look up child value from cache
        if next_hash not in child_cache:
            # Child not in cache - fetch from storage
            child_pos = _worker_storage.get(next_hash)
            if child_pos is None or child_pos.minimax_value is None:
                raise RuntimeError(f"Child not solved: hash={next_hash}, seeds={pos.seeds_in_pits}")
            child_value = child_pos.minimax_value
        else:
            child_value = child_cache[next_hash]

        # Update best
        if is_maximizing:
            if child_value > best_value:
                best_value = child_value
                best_move = move
        else:
            if child_value < best_value:
                best_value = child_value
                best_move = move

    return (pos.state_hash, best_value, best_move)


class ChunkedParallelMinimaxSolver:
    """
    Chunked parallel minimax solver.

    Strategy:
    - For each seed level (bottom-up):
      - Fetch positions in chunks
      - Pre-fetch child cache for chunk
      - Solve in parallel
      - Batch UPDATE to storage
    - Bounded memory usage
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        chunk_size: int = 50_000,
        num_workers: Optional[int] = None,
    ):
        """
        Initialize chunked parallel minimax solver.

        Args:
            storage: Storage backend
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            chunk_size: Positions to solve per chunk
            num_workers: Number of parallel workers
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.chunk_size = chunk_size
        self.num_workers = num_workers or cpu_count()
        self.max_seeds_in_pits = num_pits * 2 * num_seeds

        # Detect backend type for workers
        from ..storage import SQLiteBackend, PostgreSQLBackend

        if isinstance(storage, SQLiteBackend):
            self.backend_type = "sqlite"
            self.backend_params = {"db_path": storage.db_path}
        elif isinstance(storage, PostgreSQLBackend):
            self.backend_type = "postgresql"
            self.backend_params = {
                "host": storage.host,
                "port": storage.port,
                "database": storage.database,
                "user": storage.user,
                "password": storage.password
            }
        else:
            raise ValueError(f"Unsupported storage backend: {type(storage)}")

        logger.info(f"Chunked minimax: {chunk_size:,} positions per chunk")
        logger.info(f"Using {self.num_workers} parallel workers")

    def solve(self) -> int:
        """
        Solve all positions using chunked parallel minimax.

        Returns:
            Value of starting position
        """
        logger.info("Starting chunked parallel minimax")
        logger.info(f"Max seeds in pits: {self.max_seeds_in_pits}")

        with Pool(
            processes=self.num_workers,
            initializer=_worker_init,
            initargs=(self.backend_type, self.backend_params, self.num_pits),
        ) as pool:
            # Process each seed level bottom-up
            for seeds_in_pits in range(0, self.max_seeds_in_pits + 1):
                self._solve_seed_level_chunked(seeds_in_pits, pool)

        # Return starting position value
        start_state = create_starting_state(self.num_pits, self.num_seeds)
        start_hash = zobrist_hash(start_state)
        start_pos = self.storage.get(start_hash)

        if start_pos is None or start_pos.minimax_value is None:
            raise RuntimeError("Starting position not solved!")

        logger.info(f"Starting position value: {start_pos.minimax_value}")
        return start_pos.minimax_value

    def _solve_seed_level_chunked(self, seeds_in_pits: int, pool: Pool) -> None:
        """
        Solve all positions at a seed level in chunks.

        Args:
            seeds_in_pits: Seed level to solve
            pool: Worker pool for parallel solving
        """
        # Get all positions at this level
        positions = list(self.storage.get_positions_by_seeds_in_pits(seeds_in_pits))

        if not positions:
            return

        logger.info(f"Seeds {seeds_in_pits}: {len(positions):,} positions")

        num_chunks = (len(positions) + self.chunk_size - 1) // self.chunk_size

        with tqdm(total=len(positions), desc=f"Seeds {seeds_in_pits}", unit="pos") as pbar:
            for chunk_idx in range(num_chunks):
                start_idx = chunk_idx * self.chunk_size
                end_idx = min(start_idx + self.chunk_size, len(positions))
                chunk = positions[start_idx:end_idx]

                # Build child cache for this chunk
                child_cache = self._build_child_cache(chunk)

                # Solve chunk in parallel
                solve_args = [(pos, child_cache) for pos in chunk]
                results = pool.map(_worker_solve_position, solve_args)

                # Batch update storage
                for state_hash, value, best_move in results:
                    self.storage.update_solution(state_hash, value, best_move)

                self.storage.flush()
                pbar.update(len(chunk))

    def _build_child_cache(self, positions: List[Position]) -> Dict[int, int]:
        """
        Build cache of all potential children for a chunk of positions.

        Args:
            positions: Chunk of positions to solve

        Returns:
            Dictionary mapping child_hash -> minimax_value
        """
        child_hashes: set[int] = set()

        # Collect all potential children
        for pos in positions:
            state = unpack_state(pos.state, self.num_pits)

            if is_terminal(state):
                continue

            for move in generate_legal_moves(state):
                next_state = apply_move(state, move)
                next_hash = zobrist_hash(next_state)
                child_hashes.add(next_hash)

        # Fetch all children from storage
        cache = {}
        for child_hash in child_hashes:
            child_pos = self.storage.get(child_hash)
            if child_pos and child_pos.minimax_value is not None:
                cache[child_hash] = child_pos.minimax_value

        logger.debug(f"Built child cache: {len(cache):,} entries")
        return cache

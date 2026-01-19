"""
Zobrist hashing for fast state hashing and transposition table lookups.

Zobrist hashing uses pre-generated random numbers to create unique hashes
for game positions. It's incremental and collision-resistant.
"""

import random
from typing import Dict, Tuple
from .game_state import GameState


# Global Zobrist table (initialized once per configuration)
_zobrist_table: Dict[Tuple[int, int, int], int] = {}
_zobrist_player: Tuple[int, int] = (0, 0)


def init_zobrist_table(num_pits: int, max_seeds: int = None, seed: int = 42) -> None:
    """
    Initialize Zobrist hash table with random 64-bit numbers.

    Args:
        num_pits: Number of pits per player
        max_seeds: Maximum seeds per position (default: auto-calculate from num_pits)
        seed: Random seed for reproducibility
    """
    global _zobrist_table, _zobrist_player

    rng = random.Random(seed)
    _zobrist_table = {}

    # Auto-calculate max_seeds if not provided
    # Need to support all possible seed counts (stores can hold all seeds)
    if max_seeds is None:
        # Estimate: total seeds in game + safety margin
        # For Kalah(num_pits, seeds_per_pit), worst case is all seeds in one position
        # We'll use a conservative estimate of 64 seeds max per position
        max_seeds = 64

    num_positions = 2 * num_pits + 2  # Total board positions

    # Generate random 64-bit number for each (position, seed_count) pair
    # IMPORTANT: range(max_seeds + 1) to include max_seeds value itself
    for position in range(num_positions):
        for seeds in range(max_seeds + 1):
            _zobrist_table[(num_pits, position, seeds)] = rng.getrandbits(64)

    # Random numbers for player turn
    _zobrist_player = (rng.getrandbits(64), rng.getrandbits(64))


def zobrist_hash(state: GameState) -> int:
    """
    Compute Zobrist hash for a game state.

    The hash is computed by XORing random numbers corresponding to:
    - Each position's seed count
    - Current player

    Args:
        state: GameState to hash

    Returns:
        64-bit hash value
    """
    if not _zobrist_table:
        # Auto-initialize if not done already
        init_zobrist_table(state.num_pits)

    h = 0

    # XOR hash for each position's seed count
    for position, seeds in enumerate(state.board):
        if seeds > 0:  # Optimization: skip empty positions
            h ^= _zobrist_table[(state.num_pits, position, seeds)]

    # XOR hash for current player
    h ^= _zobrist_player[state.player]

    return h


def hash_state(state: GameState) -> int:
    """Alias for zobrist_hash for convenience."""
    return zobrist_hash(state)

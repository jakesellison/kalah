"""Core game state representation and rules."""

from .game_state import GameState, pack_state, unpack_state
from .hash import zobrist_hash, init_zobrist_table
from .rules import (
    create_starting_state,
    generate_legal_moves,
    apply_move,
    is_terminal,
    evaluate_terminal,
    get_opposite_pit,
)

__all__ = [
    "GameState",
    "pack_state",
    "unpack_state",
    "zobrist_hash",
    "init_zobrist_table",
    "create_starting_state",
    "generate_legal_moves",
    "apply_move",
    "is_terminal",
    "evaluate_terminal",
    "get_opposite_pit",
]

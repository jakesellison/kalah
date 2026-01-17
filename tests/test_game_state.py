"""Tests for game state representation."""

import pytest
from src.mancala_solver.core import GameState, pack_state, unpack_state


def test_create_game_state():
    """Test basic game state creation."""
    board = tuple([4] * 4 + [0] + [4] * 4 + [0])
    state = GameState(num_pits=4, board=board, player=0)

    assert state.num_pits == 4
    assert len(state.board) == 10  # 4 + 1 + 4 + 1
    assert state.player == 0
    assert state.total_seeds == 32


def test_pack_unpack_state():
    """Test packing and unpacking is lossless."""
    board = tuple([3] * 4 + [2] + [5] * 4 + [1])
    state = GameState(num_pits=4, board=board, player=1)

    packed = pack_state(state)
    unpacked = unpack_state(packed, num_pits=4)

    assert unpacked.num_pits == state.num_pits
    assert unpacked.board == state.board
    assert unpacked.player == state.player


def test_pack_unpack_various_states():
    """Test with various board configurations."""
    test_cases = [
        # Empty board
        tuple([0] * 4 + [0] + [0] * 4 + [0]),
        # All seeds in stores
        tuple([0] * 4 + [24] + [0] * 4 + [24]),
        # Asymmetric
        tuple([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
    ]

    for board in test_cases:
        state = GameState(num_pits=4, board=board, player=0)
        packed = pack_state(state)
        unpacked = unpack_state(packed, num_pits=4)

        assert unpacked.board == state.board
        assert unpacked.player == state.player


def test_player_pits():
    """Test getting player pit indices."""
    state = GameState(num_pits=4, board=tuple([0] * 10), player=0)

    p1_pits = state.get_player_pits(0)
    p2_pits = state.get_player_pits(1)

    assert p1_pits == [0, 1, 2, 3]
    assert p2_pits == [5, 6, 7, 8]


def test_player_stores():
    """Test getting player store indices."""
    state = GameState(num_pits=4, board=tuple([0] * 10), player=0)

    assert state.get_player_store(0) == 4
    assert state.get_player_store(1) == 9


def test_state_validation():
    """Test state validation catches errors."""
    # Wrong board size
    with pytest.raises(ValueError):
        GameState(num_pits=4, board=tuple([0] * 5), player=0)

    # Invalid player
    with pytest.raises(ValueError):
        GameState(num_pits=4, board=tuple([0] * 10), player=2)

    # Negative seeds
    with pytest.raises(ValueError):
        GameState(num_pits=4, board=tuple([0, -1, 0, 0, 0, 0, 0, 0, 0, 0]), player=0)

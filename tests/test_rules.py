"""Tests for game rules."""

import pytest
from src.mancala_solver.core import (
    create_starting_state,
    generate_legal_moves,
    apply_move,
    is_terminal,
    evaluate_terminal,
    get_opposite_pit,
    GameState,
)


def test_create_starting_state():
    """Test starting state creation."""
    state = create_starting_state(num_pits=4, num_seeds=3)

    assert state.num_pits == 4
    assert state.player == 0
    assert state.total_seeds == 24  # 4 * 2 * 3

    # P1 pits should have 3 seeds each
    for i in range(4):
        assert state.board[i] == 3

    # Stores should be empty
    assert state.board[4] == 0
    assert state.board[9] == 0

    # P2 pits should have 3 seeds each
    for i in range(5, 9):
        assert state.board[i] == 3


def test_legal_moves():
    """Test legal move generation."""
    state = create_starting_state(num_pits=4, num_seeds=3)

    # All pits should be legal at start
    moves = generate_legal_moves(state)
    assert moves == [0, 1, 2, 3]

    # Empty pits should not be legal
    board = list(state.board)
    board[0] = 0
    board[2] = 0
    state_with_empty = GameState(num_pits=4, board=tuple(board), player=0)

    moves = generate_legal_moves(state_with_empty)
    assert moves == [1, 3]


def test_opposite_pit():
    """Test opposite pit calculation."""
    # For num_pits=4:
    # P1 pits: 0,1,2,3
    # P2 pits: 5,6,7,8
    assert get_opposite_pit(0, 4) == 8
    assert get_opposite_pit(1, 4) == 7
    assert get_opposite_pit(2, 4) == 6
    assert get_opposite_pit(3, 4) == 5

    assert get_opposite_pit(5, 4) == 3
    assert get_opposite_pit(8, 4) == 0


def test_simple_move():
    """Test basic move without capture or extra turn."""
    state = create_starting_state(num_pits=4, num_seeds=3)

    # Move from pit 0 (3 seeds)
    # Seeds go to pits 1, 2, 3
    next_state = apply_move(state, 0)

    assert next_state.board[0] == 0  # Empty
    assert next_state.board[1] == 4  # 3 + 1
    assert next_state.board[2] == 4  # 3 + 1
    assert next_state.board[3] == 4  # 3 + 1
    assert next_state.player == 1  # Switched to P2


def test_extra_turn():
    """Test landing in own store gives extra turn."""
    # Set up state where move lands in store
    # num_pits=4, so store is at index 4
    # Put 4 seeds in pit 0 to land exactly in store
    board = [4, 3, 3, 3, 0, 3, 3, 3, 3, 0]
    state = GameState(num_pits=4, board=tuple(board), player=0)

    next_state = apply_move(state, 0)

    assert next_state.board[4] == 1  # Seed in store
    assert next_state.player == 0  # Still P1's turn (extra turn)


def test_capture():
    """Test capture rule."""
    # Set up capture scenario:
    # - Land in empty own pit (pit 3)
    # - Opposite pit (5) has seeds
    # P1 pits: 0,1,2,3; P1 store: 4
    # P2 pits: 5,6,7,8; P2 store: 9
    # Opposite of pit 3 is pit 5 (2*4 - 3 = 5)
    board = [0, 2, 0, 0, 0, 5, 0, 0, 0, 0]  # P1 has 2 seeds in pit 1, pit 3 is empty
    # P2 has 5 seeds in pit 5 (opposite of pit 3)
    state = GameState(num_pits=4, board=tuple(board), player=0)

    next_state = apply_move(state, 1)  # Move from pit 1

    # Seeds go to pits 2, 3
    # Pit 3 gets the last seed, was empty, opposite pit 5 has 5
    # Should capture: 1 + 5 = 6 seeds to store
    assert next_state.board[1] == 0  # Empty (picked up)
    assert next_state.board[3] == 0  # Empty (captured)
    assert next_state.board[5] == 0  # Empty (captured from opposite)
    assert next_state.board[4] == 6  # Store gets captured seeds


def test_terminal_state():
    """Test terminal state detection."""
    # One side empty
    board = [0, 0, 0, 0, 10, 3, 3, 3, 3, 2]
    state = GameState(num_pits=4, board=tuple(board), player=0)

    assert is_terminal(state) is True


def test_non_terminal_state():
    """Test non-terminal state."""
    state = create_starting_state(num_pits=4, num_seeds=3)
    assert is_terminal(state) is False


def test_evaluate_terminal():
    """Test terminal state evaluation."""
    # P1 has more seeds in store
    board = [0, 0, 0, 0, 15, 0, 0, 0, 0, 9]
    state = GameState(num_pits=4, board=tuple(board), player=0)

    value = evaluate_terminal(state)
    assert value == 6  # 15 - 9 = 6 (P1 wins by 6)


def test_evaluate_terminal_with_remaining_seeds():
    """Test that remaining seeds are collected."""
    # P1 side empty, P2 has seeds remaining
    board = [0, 0, 0, 0, 10, 2, 3, 4, 5, 5]
    state = GameState(num_pits=4, board=tuple(board), player=0)

    value = evaluate_terminal(state)
    # P1: 10 (store)
    # P2: 5 (store) + 2+3+4+5 (remaining) = 19
    # Value: 10 - 19 = -9
    assert value == -9

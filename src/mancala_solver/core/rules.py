"""
Kalah game rules implementation.

Implements standard Kalah rules:
- Counter-clockwise sowing
- Capture when landing in empty own pit with seeds opposite
- Extra turn when landing in own store
- Game ends when one side is empty
"""

from typing import List, Optional, Tuple
from .game_state import GameState


def create_starting_state(num_pits: int, num_seeds: int) -> GameState:
    """
    Create the initial game state.

    Args:
        num_pits: Number of pits per player
        num_seeds: Initial seeds per pit

    Returns:
        Starting GameState
    """
    board = [num_seeds] * num_pits  # P1 pits
    board.append(0)  # P1 store
    board.extend([num_seeds] * num_pits)  # P2 pits
    board.append(0)  # P2 store

    return GameState(num_pits=num_pits, board=tuple(board), player=0)


def get_opposite_pit(pit_idx: int, num_pits: int) -> int:
    """
    Get the opposite pit index for capture rule.

    Formula: opposite_of(pit_i) = (2 * num_pits) - pit_i

    Args:
        pit_idx: Pit index
        num_pits: Number of pits per player

    Returns:
        Opposite pit index
    """
    # Validate it's a pit, not a store
    p1_store = num_pits
    p2_store = 2 * num_pits + 1

    if pit_idx == p1_store or pit_idx == p2_store:
        raise ValueError(f"Cannot get opposite of store {pit_idx}")

    return (2 * num_pits) - pit_idx


def generate_legal_moves(state: GameState) -> List[int]:
    """
    Generate all legal moves for the current player.

    A move is legal if the chosen pit:
    - Belongs to the current player
    - Contains at least one seed

    Args:
        state: Current game state

    Returns:
        List of legal pit indices to move from
    """
    legal_moves = []
    player_pits = state.get_player_pits(state.player)

    for pit in player_pits:
        if state.board[pit] > 0:
            legal_moves.append(pit)

    return legal_moves


def apply_move(state: GameState, move: int) -> GameState:
    """
    Apply a move and return the resulting state.

    Implements full Kalah rules:
    1. Pick up all seeds from chosen pit
    2. Sow counter-clockwise, one seed per pit
    3. Skip opponent's store
    4. If last seed lands in own store: extra turn
    5. If last seed lands in own empty pit with seeds opposite: capture

    Args:
        state: Current game state
        move: Pit index to move from

    Returns:
        New GameState after move
    """
    # Validate move
    if move not in generate_legal_moves(state):
        raise ValueError(f"Illegal move {move} for state")

    # Create mutable board copy
    board = list(state.board)
    current_player = state.player
    next_player = current_player  # May change if no extra turn

    # Pick up seeds
    seeds_in_hand = board[move]
    board[move] = 0

    # Determine sowing path (skip opponent's store)
    opponent_store = state.get_player_store(1 - current_player)
    current_pos = move

    # Sow seeds
    while seeds_in_hand > 0:
        current_pos = (current_pos + 1) % len(board)

        # Skip opponent's store
        if current_pos == opponent_store:
            continue

        board[current_pos] += 1
        seeds_in_hand -= 1

    # Check for extra turn (last seed in own store)
    own_store = state.get_player_store(current_player)
    if current_pos == own_store:
        # Extra turn - player doesn't change
        next_player = current_player
    else:
        # Check for capture
        player_pits = state.get_player_pits(current_player)

        if (
            current_pos in player_pits  # Landed in own pit
            and board[current_pos] == 1  # Pit was empty (now has 1 seed)
        ):
            opposite_pit = get_opposite_pit(current_pos, state.num_pits)

            if board[opposite_pit] > 0:  # Opposite pit has seeds
                # Capture!
                captured = board[opposite_pit] + board[current_pos]
                board[opposite_pit] = 0
                board[current_pos] = 0
                board[own_store] += captured

        # No extra turn - switch player
        next_player = 1 - current_player

    return GameState(num_pits=state.num_pits, board=tuple(board), player=next_player)


def is_terminal(state: GameState) -> bool:
    """
    Check if the game has ended.

    Game ends when one player's side (all pits) is empty.

    Args:
        state: Game state to check

    Returns:
        True if game is over
    """
    p1_pits = state.get_player_pits(0)
    p2_pits = state.get_player_pits(1)

    p1_empty = all(state.board[pit] == 0 for pit in p1_pits)
    p2_empty = all(state.board[pit] == 0 for pit in p2_pits)

    return p1_empty or p2_empty


def evaluate_terminal(state: GameState) -> int:
    """
    Evaluate terminal state value.

    When game ends, remaining seeds on each side go to that player's store.
    Value = P1_store - P2_store

    Args:
        state: Terminal game state

    Returns:
        Game value from P1's perspective (positive = P1 wins, negative = P2 wins, 0 = tie)
    """
    if not is_terminal(state):
        raise ValueError("Cannot evaluate non-terminal state")

    # Create mutable copy
    board = list(state.board)

    # Collect remaining seeds
    p1_pits = state.get_player_pits(0)
    p2_pits = state.get_player_pits(1)

    p1_store = state.p1_store_idx
    p2_store = state.p2_store_idx

    # P1's remaining seeds
    for pit in p1_pits:
        board[p1_store] += board[pit]
        board[pit] = 0

    # P2's remaining seeds
    for pit in p2_pits:
        board[p2_store] += board[pit]
        board[pit] = 0

    # Return value from P1's perspective
    return board[p1_store] - board[p2_store]


def get_game_result(state: GameState) -> Optional[str]:
    """
    Get human-readable game result.

    Args:
        state: Game state

    Returns:
        Result string or None if not terminal
    """
    if not is_terminal(state):
        return None

    value = evaluate_terminal(state)

    if value > 0:
        return f"Player 1 wins by {value}"
    elif value < 0:
        return f"Player 2 wins by {-value}"
    else:
        return "Tie game"

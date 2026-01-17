"""
Retrograde minimax analysis solver.

Computes optimal values for all positions by working backwards from
terminal positions (endgame) to starting position.
"""

import logging
from typing import Optional
from tqdm import tqdm

from ..core import (
    GameState,
    generate_legal_moves,
    apply_move,
    is_terminal,
    evaluate_terminal,
    zobrist_hash,
    pack_state,
)
from ..core.game_state import unpack_state
from ..storage import StorageBackend

logger = logging.getLogger(__name__)


class MinimaxSolver:
    """
    Retrograde minimax solver.

    Works backwards from terminal positions, computing optimal values
    for each position based on already-computed child values.
    """

    def __init__(self, storage: StorageBackend, num_pits: int, num_seeds: int):
        """
        Initialize minimax solver.

        Args:
            storage: Storage backend (must already contain full game graph)
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.max_seeds_in_pits = num_pits * 2 * num_seeds  # All seeds start in pits

    def solve(self) -> int:
        """
        Solve all positions using retrograde minimax.

        Works backwards from terminal positions (0 seeds in pits) to starting position.
        Since moves only decrease seeds in pits, this guarantees all children are solved
        before their parents.

        Returns:
            Value of starting position
        """
        logger.info("Starting retrograde minimax analysis")
        logger.info(f"Max seeds in pits: {self.max_seeds_in_pits}")

        # Process positions by seeds_in_pits (ascending: 0, 1, 2, ...)
        # 0 seeds = terminal positions (all in stores), trivial to evaluate
        # Higher seeds = earlier in game, depend on lower seed positions
        with tqdm(
            total=self.max_seeds_in_pits + 1, desc="Minimax", unit=" seed_layer"
        ) as pbar:
            for seeds_in_pits in range(0, self.max_seeds_in_pits + 1):
                positions = list(self.storage.get_positions_by_seeds_in_pits(seeds_in_pits))

                if not positions:
                    pbar.update(1)
                    continue

                pbar.set_description(
                    f"Seeds-in-pits {seeds_in_pits} ({len(positions):,} positions)"
                )

                # Iterative solving within this seed level
                # (Extra turns can create same-seed dependencies)
                unsolved = {p.state_hash: p for p in positions}
                iterations = 0

                while unsolved:
                    iterations += 1
                    progress_made = False

                    for state_hash in list(unsolved.keys()):
                        pos = unsolved[state_hash]
                        state = unpack_state(pos.state, self.num_pits)

                        # Check if all children are solved
                        if self._all_children_solved(state):
                            value, best_move = self._minimax_position(state)
                            self.storage.update_solution(pos.state_hash, value, best_move)
                            del unsolved[state_hash]
                            progress_made = True

                    if not progress_made and unsolved:
                        raise RuntimeError(
                            f"Circular dependency at seeds_in_pits={seeds_in_pits}, "
                            f"{len(unsolved)} unsolved positions remaining"
                        )

                self.storage.flush()
                logger.info(
                    f"Seeds-in-pits {seeds_in_pits}: solved {len(positions):,} positions "
                    f"in {iterations} iterations"
                )

                pbar.update(1)

        # Get starting position value
        from ..core import create_starting_state

        start_state = create_starting_state(self.num_pits, self.num_seeds)
        start_hash = zobrist_hash(start_state)
        start_pos = self.storage.get(start_hash)

        if start_pos and start_pos.minimax_value is not None:
            logger.info(
                f"Game solved! Starting position value: {start_pos.minimax_value}"
            )
            logger.info(f"Best opening move: {start_pos.best_move}")
            return start_pos.minimax_value
        else:
            raise RuntimeError("Failed to solve starting position")

    def _all_children_solved(self, state: GameState) -> bool:
        """Check if all child positions are solved."""
        if is_terminal(state):
            return True

        for move in generate_legal_moves(state):
            next_state = apply_move(state, move)
            next_hash = zobrist_hash(next_state)
            child_pos = self.storage.get(next_hash)

            if child_pos is None or child_pos.minimax_value is None:
                return False

        return True

    def _minimax_position(self, state: GameState) -> tuple[int, Optional[int]]:
        """
        Compute minimax value for a position.

        Assumes all child positions are already solved.

        Args:
            state: Position to evaluate

        Returns:
            (minimax_value, best_move)
        """
        # Terminal state?
        if is_terminal(state):
            return evaluate_terminal(state), None

        legal_moves = generate_legal_moves(state)
        if not legal_moves:
            # Shouldn't happen if is_terminal is correct
            raise RuntimeError(f"No legal moves but not terminal: {state}")

        # Minimax search
        is_maximizing = state.player == 0  # P1 maximizes

        best_value = float("-inf") if is_maximizing else float("inf")
        best_move = None

        for move in legal_moves:
            next_state = apply_move(state, move)
            next_hash = zobrist_hash(next_state)

            # Lookup child value
            child_pos = self.storage.get(next_hash)

            if child_pos is None:
                raise RuntimeError(f"Child position not found: hash={next_hash}")

            if child_pos.minimax_value is None:
                raise RuntimeError(
                    f"Child position not solved: hash={next_hash}, "
                    f"seeds_in_pits={child_pos.seeds_in_pits}"
                )

            child_value = child_pos.minimax_value

            # Update best
            if is_maximizing:
                if child_value > best_value:
                    best_value = child_value
                    best_move = move
            else:
                if child_value < best_value:
                    best_value = child_value
                    best_move = move

        return best_value, best_move

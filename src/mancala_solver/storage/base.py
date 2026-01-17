"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from typing import List, Optional, Iterator
from dataclasses import dataclass


@dataclass
class Position:
    """
    Represents a position in the game database.
    """

    state_hash: int  # 8-byte hash
    state: bytes  # Packed state representation
    depth: int  # BFS depth from starting position
    seeds_in_pits: int  # Seeds remaining in pits (NOT in stores) - for retrograde analysis
    minimax_value: Optional[int] = None  # Minimax value (computed in phase 2)
    best_move: Optional[int] = None  # Best move from this position


class StorageBackend(ABC):
    """Abstract interface for position storage."""

    @abstractmethod
    def insert(self, position: Position) -> bool:
        """
        Insert single position.

        Args:
            position: Position to insert

        Returns:
            True if inserted, False if duplicate
        """
        pass

    @abstractmethod
    def insert_batch(self, positions: List[Position]) -> int:
        """
        Bulk insert positions, auto-deduplicating.

        Args:
            positions: List of positions to insert

        Returns:
            Number of new positions inserted
        """
        pass

    @abstractmethod
    def exists(self, state_hash: int) -> bool:
        """
        Check if position already stored.

        Args:
            state_hash: Hash of state

        Returns:
            True if exists
        """
        pass

    @abstractmethod
    def get(self, state_hash: int) -> Optional[Position]:
        """
        Retrieve position by hash.

        Args:
            state_hash: Hash of state

        Returns:
            Position or None if not found
        """
        pass

    @abstractmethod
    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        """
        Iterate all positions at given depth.

        Args:
            depth: BFS depth

        Yields:
            Positions at specified depth
        """
        pass

    @abstractmethod
    def get_positions_at_depth_batch(
        self, depth: int, limit: int, offset: int = 0
    ) -> List[Position]:
        """
        Get batch of positions at a given depth (for chunked BFS).

        Args:
            depth: BFS depth
            limit: Maximum number of positions to fetch
            offset: Starting offset (for pagination)

        Returns:
            List of positions at specified depth
        """
        pass

    @abstractmethod
    def get_positions_by_seeds_in_pits(self, seeds_in_pits: int) -> Iterator[Position]:
        """
        Iterate all positions with given seeds remaining in pits.

        Args:
            seeds_in_pits: Seeds in pits (not stores)

        Yields:
            Positions with specified seeds_in_pits
        """
        pass

    @abstractmethod
    def get_unsolved_positions_batch(
        self, seeds_in_pits: int, limit: int, offset: int = 0
    ) -> List[Position]:
        """
        Get a batch of unsolved positions at a seed level.

        Used for memory-efficient minimax processing - loads positions in batches
        instead of loading all positions at a seed level into RAM.

        Args:
            seeds_in_pits: Seeds in pits (not stores)
            limit: Maximum positions to return
            offset: Starting offset (for pagination)

        Returns:
            List of unsolved positions (minimax_value IS NULL)
        """
        pass

    @abstractmethod
    def count_unsolved_positions(self, seeds_in_pits: int) -> int:
        """
        Count unsolved positions at a seed level.

        Args:
            seeds_in_pits: Seeds in pits (not stores)

        Returns:
            Number of positions where minimax_value IS NULL
        """
        pass

    @abstractmethod
    def update_solution(self, state_hash: int, minimax_value: int, best_move: int) -> None:
        """
        Update position with solved minimax value.

        Args:
            state_hash: Hash of state
            minimax_value: Computed minimax value
            best_move: Best move from this position
        """
        pass

    @abstractmethod
    def count_positions(self, depth: Optional[int] = None) -> int:
        """
        Count total positions, optionally filtered by depth.

        Args:
            depth: Optional depth filter

        Returns:
            Position count
        """
        pass

    @abstractmethod
    def get_max_depth(self) -> int:
        """
        Get maximum depth in database.

        Returns:
            Maximum depth, or -1 if empty
        """
        pass

    @abstractmethod
    def flush(self) -> None:
        """Ensure all pending writes are persisted."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Cleanup and close connection."""
        pass

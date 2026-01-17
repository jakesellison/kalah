"""
Game state representation with bit-packing for memory efficiency.

A Kalah game state consists of:
- Board positions (pits + stores)
- Current player turn

We use bit-packing to minimize memory footprint:
- 5 bits per pit (supports 0-31 seeds)
- 1 bit for player turn
- Total: ~9 bytes for Kalah(6,4)
"""

from typing import List, Tuple
from dataclasses import dataclass


@dataclass(frozen=True)
class GameState:
    """
    Immutable game state representation.

    Board layout for num_pits=6:
          P2 Pits (12-7)
       [12][11][10][9][8][7]
    [13]                    [6]  <- Stores
       [0] [1] [2] [3][4][5]
          P1 Pits (0-5)

    Indices:
    - P1 pits: 0 to num_pits-1
    - P1 store: num_pits
    - P2 pits: num_pits+1 to 2*num_pits
    - P2 store: 2*num_pits+1
    """

    num_pits: int  # Number of pits per player
    board: Tuple[int, ...]  # Seeds in each position (immutable)
    player: int  # Current player (0 = P1, 1 = P2)

    def __post_init__(self) -> None:
        """Validate state invariants."""
        expected_size = 2 * self.num_pits + 2  # pits + stores
        if len(self.board) != expected_size:
            raise ValueError(
                f"Board size {len(self.board)} doesn't match expected {expected_size}"
            )
        if self.player not in (0, 1):
            raise ValueError(f"Invalid player {self.player}, must be 0 or 1")
        if any(seeds < 0 for seeds in self.board):
            raise ValueError("Negative seed count not allowed")

    @property
    def p1_store_idx(self) -> int:
        """Index of player 1's store."""
        return self.num_pits

    @property
    def p2_store_idx(self) -> int:
        """Index of player 2's store."""
        return 2 * self.num_pits + 1

    @property
    def total_seeds(self) -> int:
        """Total seeds on the board."""
        return sum(self.board)

    @property
    def seeds_in_pits(self) -> int:
        """Seeds remaining in pits (not in stores)."""
        # Exclude the two stores from the sum
        total = sum(self.board)
        total -= self.board[self.p1_store_idx]
        total -= self.board[self.p2_store_idx]
        return total

    def get_player_pits(self, player: int) -> List[int]:
        """Get pit indices for a player."""
        if player == 0:  # P1
            return list(range(self.num_pits))
        else:  # P2
            return list(range(self.num_pits + 1, 2 * self.num_pits + 1))

    def get_player_store(self, player: int) -> int:
        """Get store index for a player."""
        return self.p1_store_idx if player == 0 else self.p2_store_idx

    def __str__(self) -> str:
        """Human-readable board representation."""
        p2_pits = list(reversed(self.board[self.num_pits + 1 : 2 * self.num_pits + 1]))
        p1_pits = list(self.board[: self.num_pits])
        p1_store = self.board[self.p1_store_idx]
        p2_store = self.board[self.p2_store_idx]

        # Format board
        pit_width = 3
        p2_str = " ".join(f"{s:>{pit_width}}" for s in p2_pits)
        p1_str = " ".join(f"{s:>{pit_width}}" for s in p1_pits)
        store_width = len(p2_str)

        board_str = f"""
      {p2_str}
[{p2_store:>2}] {' ' * store_width} [{p1_store:>2}]
      {p1_str}

Player {self.player + 1}'s turn
"""
        return board_str


def pack_state(state: GameState) -> bytes:
    """
    Pack game state into compact byte representation.

    Uses 5 bits per position (supports 0-31 seeds), plus 1 bit for player.
    For Kalah(6,4): 14 positions × 5 bits + 1 bit = 71 bits ≈ 9 bytes

    Args:
        state: GameState to pack

    Returns:
        Packed bytes representation
    """
    num_positions = len(state.board)
    bits_per_position = 5
    total_bits = num_positions * bits_per_position + 1  # +1 for player bit

    # Calculate byte array size
    num_bytes = (total_bits + 7) // 8  # Ceiling division
    packed = bytearray(num_bytes)

    # Pack each position (5 bits each)
    bit_offset = 0
    for seeds in state.board:
        if seeds > 31:
            raise ValueError(f"Cannot pack {seeds} seeds (max 31 with 5 bits)")

        # Write 5 bits for this position
        for i in range(bits_per_position):
            if seeds & (1 << i):
                byte_idx = bit_offset // 8
                bit_in_byte = bit_offset % 8
                packed[byte_idx] |= 1 << bit_in_byte
            bit_offset += 1

    # Pack player bit
    if state.player == 1:
        byte_idx = bit_offset // 8
        bit_in_byte = bit_offset % 8
        packed[byte_idx] |= 1 << bit_in_byte

    return bytes(packed)


def unpack_state(packed: bytes, num_pits: int) -> GameState:
    """
    Unpack byte representation back to GameState.

    Args:
        packed: Packed bytes from pack_state()
        num_pits: Number of pits per player

    Returns:
        Reconstructed GameState
    """
    num_positions = 2 * num_pits + 2
    bits_per_position = 5

    board = []
    bit_offset = 0

    # Unpack each position (5 bits each)
    for _ in range(num_positions):
        seeds = 0
        for i in range(bits_per_position):
            byte_idx = bit_offset // 8
            bit_in_byte = bit_offset % 8

            if byte_idx < len(packed):
                if packed[byte_idx] & (1 << bit_in_byte):
                    seeds |= 1 << i

            bit_offset += 1

        board.append(seeds)

    # Unpack player bit
    byte_idx = bit_offset // 8
    bit_in_byte = bit_offset % 8
    player = 0
    if byte_idx < len(packed) and (packed[byte_idx] & (1 << bit_in_byte)):
        player = 1

    return GameState(num_pits=num_pits, board=tuple(board), player=player)

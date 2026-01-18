"""Game solving algorithms."""

from .parallel_minimax import ParallelMinimaxSolver
from .chunked_bfs import ChunkedBFSSolver

__all__ = [
    "ParallelMinimaxSolver",
    "ChunkedBFSSolver",
]

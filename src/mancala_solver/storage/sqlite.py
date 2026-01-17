"""SQLite storage backend for local development."""

import sqlite3
import logging
from typing import List, Optional, Iterator
from .base import StorageBackend, Position

logger = logging.getLogger(__name__)


class SQLiteBackend(StorageBackend):
    """
    SQLite storage implementation.

    Optimized for:
    - Bulk inserts (BFS phase)
    - Depth-based queries (BFS)
    - Seed-count-based queries (minimax)
    - Fast lookups by hash (minimax child lookups)
    """

    def __init__(
        self, db_path: str = "mancala.db", adaptive_cache: bool = True
    ):
        """
        Initialize SQLite backend.

        Args:
            db_path: Path to database file (use ":memory:" for in-memory)
            adaptive_cache: Use adaptive cache sizing based on available RAM
        """
        self.db_path = db_path
        self.adaptive_cache = adaptive_cache
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access
        self._create_schema()
        self._optimize()

    def _create_schema(self) -> None:
        """Create database schema."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                state_hash TEXT PRIMARY KEY,
                state BLOB NOT NULL,
                depth INTEGER NOT NULL,
                seeds_in_pits INTEGER NOT NULL,
                minimax_value INTEGER,
                best_move INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_depth ON positions(depth);
            CREATE INDEX IF NOT EXISTS idx_seeds_in_pits ON positions(seeds_in_pits);
        """
        )
        self.conn.commit()

    def _optimize(self) -> None:
        """Apply SQLite performance optimizations."""
        # Determine cache size
        if self.adaptive_cache:
            try:
                from ..utils import MemoryMonitor

                monitor = MemoryMonitor()
                cache_size_mb = monitor.get_adaptive_cache_size_mb(max_cache_mb=256)
                logger.info(f"Using adaptive cache size: {cache_size_mb}MB")
            except Exception as e:
                logger.warning(f"Failed to get adaptive cache size: {e}, using 64MB")
                cache_size_mb = 64
        else:
            cache_size_mb = 64

        # Convert to KB (negative value for KB)
        cache_size_kb = -int(cache_size_mb * 1024)

        # Calculate mmap size (4x cache size, capped at 512MB)
        mmap_size = min(cache_size_mb * 4 * 1024 * 1024, 512 * 1024 * 1024)

        self.conn.executescript(
            f"""
            PRAGMA journal_mode = WAL;           -- Write-Ahead Logging
            PRAGMA synchronous = NORMAL;         -- Balanced durability
            PRAGMA cache_size = {cache_size_kb}; -- Adaptive cache
            PRAGMA temp_store = MEMORY;          -- Temp tables in memory
            PRAGMA mmap_size = {mmap_size};      -- Adaptive memory-mapped I/O
        """
        )

        logger.debug(f"SQLite optimizations: cache={cache_size_mb}MB, mmap={mmap_size // (1024*1024)}MB")

    def insert(self, position: Position) -> bool:
        """Insert single position."""
        try:
            self.conn.execute(
                """
                INSERT INTO positions (state_hash, state, depth, seeds_in_pits)
                VALUES (?, ?, ?, ?)
            """,
                (str(position.state_hash), position.state, position.depth, position.seeds_in_pits),
            )
            return True
        except sqlite3.IntegrityError:  # Duplicate primary key
            return False

    def insert_batch(self, positions: List[Position]) -> int:
        """Bulk insert with deduplication."""
        # Use INSERT OR IGNORE for automatic deduplication
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO positions (state_hash, state, depth, seeds_in_pits)
            VALUES (?, ?, ?, ?)
        """,
            [
                (str(p.state_hash), p.state, p.depth, p.seeds_in_pits)
                for p in positions
            ],
        )
        # SQLite doesn't provide rowcount for executemany with OR IGNORE
        # We'll return the number we attempted to insert
        # For accurate count, query before/after (expensive) or accept approximation
        return cursor.rowcount if cursor.rowcount > 0 else len(positions)

    def exists(self, state_hash: int) -> bool:
        """Check if position exists."""
        cursor = self.conn.execute(
            "SELECT 1 FROM positions WHERE state_hash = ?", (str(state_hash),)
        )
        return cursor.fetchone() is not None

    def get(self, state_hash: int) -> Optional[Position]:
        """Retrieve position by hash."""
        cursor = self.conn.execute(
            "SELECT * FROM positions WHERE state_hash = ?", (str(state_hash),)
        )
        row = cursor.fetchone()
        if row:
            return Position(
                state_hash=int(row["state_hash"]),
                state=row["state"],
                depth=row["depth"],
                seeds_in_pits=row["seeds_in_pits"],
                minimax_value=row["minimax_value"],
                best_move=row["best_move"],
            )
        return None

    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        """Iterate positions at depth."""
        cursor = self.conn.execute("SELECT * FROM positions WHERE depth = ?", (depth,))
        for row in cursor:
            yield Position(
                state_hash=int(row["state_hash"]),
                state=row["state"],
                depth=row["depth"],
                seeds_in_pits=row["seeds_in_pits"],
                minimax_value=row["minimax_value"],
                best_move=row["best_move"],
            )

    def get_positions_at_depth_batch(
        self, depth: int, limit: int, offset: int = 0
    ) -> List[Position]:
        """Get batch of positions at depth (for chunked BFS)."""
        cursor = self.conn.execute(
            """
            SELECT * FROM positions
            WHERE depth = ?
            LIMIT ? OFFSET ?
            """,
            (depth, limit, offset),
        )
        positions = []
        for row in cursor:
            positions.append(
                Position(
                    state_hash=int(row["state_hash"]),
                    state=row["state"],
                    depth=row["depth"],
                    seeds_in_pits=row["seeds_in_pits"],
                    minimax_value=row["minimax_value"],
                    best_move=row["best_move"],
                )
            )
        return positions

    def get_positions_by_seeds_in_pits(self, seeds_in_pits: int) -> Iterator[Position]:
        """Iterate positions by seeds in pits."""
        cursor = self.conn.execute(
            "SELECT * FROM positions WHERE seeds_in_pits = ?", (seeds_in_pits,)
        )
        for row in cursor:
            yield Position(
                state_hash=int(row["state_hash"]),
                state=row["state"],
                depth=row["depth"],
                seeds_in_pits=row["seeds_in_pits"],
                minimax_value=row["minimax_value"],
                best_move=row["best_move"],
            )

    def get_unsolved_positions_batch(
        self, seeds_in_pits: int, limit: int, offset: int = 0
    ) -> List[Position]:
        """Get batch of unsolved positions."""
        cursor = self.conn.execute(
            """
            SELECT * FROM positions
            WHERE seeds_in_pits = ? AND minimax_value IS NULL
            LIMIT ? OFFSET ?
            """,
            (seeds_in_pits, limit, offset),
        )
        positions = []
        for row in cursor:
            positions.append(
                Position(
                    state_hash=int(row["state_hash"]),
                    state=row["state"],
                    depth=row["depth"],
                    seeds_in_pits=row["seeds_in_pits"],
                    minimax_value=row["minimax_value"],
                    best_move=row["best_move"],
                )
            )
        return positions

    def count_unsolved_positions(self, seeds_in_pits: int) -> int:
        """Count unsolved positions at seed level."""
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) FROM positions
            WHERE seeds_in_pits = ? AND minimax_value IS NULL
            """,
            (seeds_in_pits,),
        )
        return cursor.fetchone()[0]

    def update_solution(
        self, state_hash: int, minimax_value: int, best_move: int
    ) -> None:
        """Update position with solution."""
        self.conn.execute(
            """
            UPDATE positions
            SET minimax_value = ?, best_move = ?
            WHERE state_hash = ?
        """,
            (minimax_value, best_move, str(state_hash)),
        )

    def count_positions(self, depth: Optional[int] = None) -> int:
        """Count positions."""
        if depth is None:
            cursor = self.conn.execute("SELECT COUNT(*) FROM positions")
        else:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM positions WHERE depth = ?", (depth,)
            )
        return cursor.fetchone()[0]

    def get_max_depth(self) -> int:
        """Get maximum depth."""
        cursor = self.conn.execute("SELECT MAX(depth) FROM positions")
        result = cursor.fetchone()[0]
        return result if result is not None else -1

    def flush(self) -> None:
        """Commit pending transactions."""
        self.conn.commit()

    def close(self) -> None:
        """Close database connection."""
        self.conn.commit()
        self.conn.close()

    def __enter__(self):
        """Context manager support."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager cleanup."""
        self.close()

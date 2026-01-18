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
        self, db_path: str = "mancala.db", adaptive_cache: bool = True, fast_mode: bool = False,
        create_schema: bool = True
    ):
        """
        Initialize SQLite backend.

        Args:
            db_path: Path to database file (use ":memory:" for in-memory)
            adaptive_cache: Use adaptive cache sizing based on available RAM
            fast_mode: Disable durability (journal_mode=OFF, synchronous=OFF) for maximum speed
                      WARNING: No crash recovery! Use for batch jobs where you can re-run if needed.
            create_schema: If False, skip schema creation (for worker processes)
        """
        self.db_path = db_path
        self.adaptive_cache = adaptive_cache
        self.fast_mode = fast_mode
        self.is_main_process = create_schema  # Only main process creates schema
        # Set timeout to 30 seconds to handle concurrent access from worker processes
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access
        if create_schema:
            self._create_schema()
        self._optimize()

    def _create_schema(self) -> None:
        """Create database schema (optimized for storage)."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                state_hash BLOB NOT NULL,          -- 8 bytes (unsigned 64-bit)
                state BLOB NOT NULL,               -- 9 bytes for Kalah(6,4) (bit-packed)
                depth INTEGER NOT NULL,            -- 1 byte (depths 0-127)
                seeds_in_pits INTEGER NOT NULL,    -- 1 byte (6-48 for Kalah(6,4))
                minimax_value INTEGER,             -- 1 byte (-128 to 127)
                best_move INTEGER                  -- 1 byte (0-5)
            );

            CREATE INDEX IF NOT EXISTS idx_state_hash ON positions(state_hash);
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

        if self.fast_mode:
            # FAST MODE: No durability, maximum speed
            # WARNING: Database can be corrupted if process crashes!
            # Trade-off: 5-10x faster writes, but no crash recovery
            # NOTE: No EXCLUSIVE locking - we need parallel worker access
            self.conn.executescript(
                f"""
                PRAGMA journal_mode = OFF;           -- No journal (NO CRASH RECOVERY!)
                PRAGMA synchronous = OFF;            -- Don't wait for disk writes (DANGEROUS!)
                PRAGMA read_uncommitted = ON;        -- Allow dirty reads (reduces lock contention)
                PRAGMA cache_size = {cache_size_kb}; -- Adaptive cache
                PRAGMA temp_store = MEMORY;          -- Temp tables in memory
                PRAGMA mmap_size = {mmap_size};      -- Adaptive memory-mapped I/O
            """
            )
            # Only log warning in main process (workers would spam logs)
            if self.is_main_process:
                logger.warning("⚠️  FAST MODE ENABLED: No crash recovery! Database may corrupt if process dies.")
                logger.info(f"SQLite fast mode: cache={cache_size_mb}MB, mmap={mmap_size // (1024*1024)}MB, journal=OFF, sync=OFF")
        else:
            # SAFE MODE: Standard durability with WAL
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
            # Convert hash to bytes (8 bytes, big-endian, unsigned)
            hash_bytes = position.state_hash.to_bytes(8, 'big', signed=False)
            self.conn.execute(
                """
                INSERT INTO positions (state_hash, state, depth, seeds_in_pits)
                VALUES (?, ?, ?, ?)
            """,
                (hash_bytes, position.state, position.depth, position.seeds_in_pits),
            )
            return True
        except sqlite3.IntegrityError:  # Duplicate primary key
            return False

    def insert_batch(self, positions: List[Position], allow_duplicates: bool = False) -> int:
        """Bulk insert with optional deduplication.

        Args:
            positions: List of positions to insert
            allow_duplicates: If True, use plain INSERT (faster, allows duplicates)
                            If False, use INSERT OR IGNORE (deduplicates, slower)

        Returns:
            Number of positions attempted to insert (approximate)
        """
        cursor = self.conn.cursor()

        if allow_duplicates:
            # Plain INSERT - fast but allows duplicates
            # Duplicates will be cleaned up later
            cursor.executemany(
                """
                INSERT INTO positions (state_hash, state, depth, seeds_in_pits)
                VALUES (?, ?, ?, ?)
            """,
                [
                    (p.state_hash.to_bytes(8, 'big', signed=False), p.state, p.depth, p.seeds_in_pits)
                    for p in positions
                ],
            )
        else:
            # INSERT OR IGNORE - deduplicates
            cursor.executemany(
                """
                INSERT OR IGNORE INTO positions (state_hash, state, depth, seeds_in_pits)
                VALUES (?, ?, ?, ?)
            """,
                [
                    (p.state_hash.to_bytes(8, 'big', signed=False), p.state, p.depth, p.seeds_in_pits)
                    for p in positions
                ],
            )

        # Return attempted count (not necessarily actual inserts with OR IGNORE)
        return cursor.rowcount if cursor.rowcount > 0 else len(positions)

    def exists(self, state_hash: int) -> bool:
        """Check if position exists."""
        hash_bytes = state_hash.to_bytes(8, 'big', signed=False)
        cursor = self.conn.execute(
            "SELECT 1 FROM positions WHERE state_hash = ?", (hash_bytes,)
        )
        return cursor.fetchone() is not None

    def get(self, state_hash: int) -> Optional[Position]:
        """Retrieve position by hash."""
        hash_bytes = state_hash.to_bytes(8, 'big', signed=False)
        cursor = self.conn.execute(
            "SELECT * FROM positions WHERE state_hash = ?", (hash_bytes,)
        )
        row = cursor.fetchone()
        if row:
            return Position(
                state_hash=int.from_bytes(row["state_hash"], 'big', signed=False),
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
                state_hash=int.from_bytes(row["state_hash"], 'big', signed=False),
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
                    state_hash=int.from_bytes(row["state_hash"], 'big', signed=False),
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
                state_hash=int.from_bytes(row["state_hash"], 'big', signed=False),
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
                    state_hash=int.from_bytes(row["state_hash"], 'big', signed=False),
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
        hash_bytes = state_hash.to_bytes(8, 'big', signed=False)
        self.conn.execute(
            """
            UPDATE positions
            SET minimax_value = ?, best_move = ?
            WHERE state_hash = ?
        """,
            (minimax_value, best_move, hash_bytes),
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

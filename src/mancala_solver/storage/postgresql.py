"""PostgreSQL storage backend for cloud scalability."""

import psycopg2
import psycopg2.extras
from typing import List, Optional, Iterator
from .base import StorageBackend, Position


def _to_signed_int64(n: int) -> int:
    """Convert unsigned 64-bit to signed 64-bit for PostgreSQL BIGINT."""
    if n > 0x7FFFFFFFFFFFFFFF:  # If > 2^63 - 1
        return n - 0x10000000000000000  # Subtract 2^64 to make negative
    return n


def _from_signed_int64(n: int) -> int:
    """Convert signed 64-bit from PostgreSQL BIGINT to unsigned."""
    if n < 0:
        return n + 0x10000000000000000  # Add 2^64
    return n


class PostgreSQLBackend(StorageBackend):
    """
    PostgreSQL storage implementation.

    Optimized for:
    - High concurrency (parallel workers)
    - Bulk inserts (BFS phase)
    - Seed-count-based queries (minimax)
    - Fast lookups by hash (minimax child lookups)
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "mancala",
        user: str = "postgres",
        password: str = "",
        unlogged: bool = True,
    ):
        """
        Initialize PostgreSQL backend.

        Args:
            host: Database host (use localhost with Cloud SQL Proxy)
            port: Database port
            database: Database name
            user: Database user
            password: Database password
            unlogged: Use UNLOGGED tables (3-5× faster writes, no crash recovery)
        """
        # Store connection parameters for worker processes
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.unlogged = unlogged

        self.conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        self.conn.autocommit = False  # Manual transaction control for performance
        self._create_schema()
        self._optimize()

    def _create_schema(self) -> None:
        """Create database schema."""
        unlogged_keyword = "UNLOGGED" if self.unlogged else ""
        with self.conn.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE {unlogged_keyword} TABLE IF NOT EXISTS positions (
                    state_hash BIGINT PRIMARY KEY,           -- 8 bytes (was NUMERIC 20 bytes)
                    state BYTEA NOT NULL,                     -- 9 bytes (board state)
                    depth INTEGER NOT NULL,                   -- 4 bytes (BFS depth)
                    seeds_in_pits SMALLINT NOT NULL,          -- 2 bytes (was 4 bytes) - max 144 for Kalah(6,6)
                    minimax_value SMALLINT,                   -- 2 bytes (was 4 bytes) - score range is small
                    best_move SMALLINT                        -- 2 bytes (was 4 bytes) - max pit index is small
                );

                CREATE INDEX IF NOT EXISTS idx_depth ON positions(depth);
                CREATE INDEX IF NOT EXISTS idx_seeds_in_pits ON positions(seeds_in_pits);
            """
            )
            self.conn.commit()

    def _optimize(self) -> None:
        """Apply PostgreSQL performance optimizations."""
        with self.conn.cursor() as cursor:
            # Increase work memory for faster sorts/joins
            cursor.execute("SET work_mem = '256MB';")
            # Disable synchronous commit for faster writes (data in memory, not disk)
            # Safe for our use case since we can rebuild if interrupted
            cursor.execute("SET synchronous_commit = OFF;")
            self.conn.commit()

    def insert(self, position: Position) -> bool:
        """Insert single position."""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO positions (state_hash, state, depth, seeds_in_pits)
                    VALUES (%s, %s, %s, %s)
                """,
                    (_to_signed_int64(position.state_hash), position.state, position.depth, position.seeds_in_pits),
                )
                return True
        except psycopg2.IntegrityError:  # Duplicate primary key
            self.conn.rollback()
            return False

    def insert_batch(self, positions: List[Position]) -> int:
        """Bulk insert with deduplication."""
        if not positions:
            return 0

        with self.conn.cursor() as cursor:
            # Use execute_values for fast bulk insert
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO positions (state_hash, state, depth, seeds_in_pits)
                VALUES %s
                ON CONFLICT (state_hash) DO NOTHING
            """,
                [(_to_signed_int64(p.state_hash), p.state, p.depth, p.seeds_in_pits) for p in positions],
                page_size=1000,
            )
            return cursor.rowcount if cursor.rowcount > 0 else len(positions)

    def exists(self, state_hash: int) -> bool:
        """Check if position exists."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM positions WHERE state_hash = %s", (_to_signed_int64(state_hash),)
            )
            return cursor.fetchone() is not None

    def get(self, state_hash: int) -> Optional[Position]:
        """Retrieve position by hash."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM positions WHERE state_hash = %s", (_to_signed_int64(state_hash),)
            )
            row = cursor.fetchone()
            if row:
                return Position(
                    state_hash=_from_signed_int64(row[0]),
                    state=bytes(row[1]),
                    depth=row[2],
                    seeds_in_pits=row[3],
                    minimax_value=row[4],
                    best_move=row[5],
                )
            return None

    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        """Iterate positions at depth."""
        with self.conn.cursor(name='depth_cursor') as cursor:
            cursor.execute("SELECT * FROM positions WHERE depth = %s", (depth,))
            for row in cursor:
                yield Position(
                    state_hash=_from_signed_int64(row[0]),
                    state=bytes(row[1]),
                    depth=row[2],
                    seeds_in_pits=row[3],
                    minimax_value=row[4],
                    best_move=row[5],
                )

    def get_positions_at_depth_batch(
        self, depth: int, limit: int, offset: int = 0
    ) -> List[Position]:
        """Get batch of positions at depth (for chunked BFS)."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM positions
                WHERE depth = %s
                LIMIT %s OFFSET %s
                """,
                (depth, limit, offset),
            )
            positions = []
            for row in cursor:
                positions.append(
                    Position(
                        state_hash=_from_signed_int64(row[0]),
                        state=bytes(row[1]),
                        depth=row[2],
                        seeds_in_pits=row[3],
                        minimax_value=row[4],
                        best_move=row[5],
                    )
                )
            return positions

    def get_positions_by_seeds_in_pits(self, seeds_in_pits: int) -> Iterator[Position]:
        """Iterate positions by seeds in pits."""
        with self.conn.cursor(name='seeds_cursor') as cursor:
            cursor.execute(
                "SELECT * FROM positions WHERE seeds_in_pits = %s", (seeds_in_pits,)
            )
            for row in cursor:
                yield Position(
                    state_hash=_from_signed_int64(row[0]),
                    state=bytes(row[1]),
                    depth=row[2],
                    seeds_in_pits=row[3],
                    minimax_value=row[4],
                    best_move=row[5],
                )

    def get_unsolved_positions_batch(
        self, seeds_in_pits: int, limit: int, offset: int = 0
    ) -> List[Position]:
        """Get batch of unsolved positions."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM positions
                WHERE seeds_in_pits = %s AND minimax_value IS NULL
                LIMIT %s OFFSET %s
                """,
                (seeds_in_pits, limit, offset),
            )
            positions = []
            for row in cursor:
                positions.append(
                    Position(
                        state_hash=_from_signed_int64(row[0]),
                        state=bytes(row[1]),
                        depth=row[2],
                        seeds_in_pits=row[3],
                        minimax_value=row[4],
                        best_move=row[5],
                    )
                )
            return positions

    def count_unsolved_positions(self, seeds_in_pits: int) -> int:
        """Count unsolved positions at seed level."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) FROM positions
                WHERE seeds_in_pits = %s AND minimax_value IS NULL
                """,
                (seeds_in_pits,),
            )
            return cursor.fetchone()[0]

    def update_solution(
        self, state_hash: int, minimax_value: int, best_move: int
    ) -> None:
        """Update position with solution."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE positions
                SET minimax_value = %s, best_move = %s
                WHERE state_hash = %s
            """,
                (minimax_value, best_move, _to_signed_int64(state_hash)),
            )

    def count_positions(self, depth: Optional[int] = None) -> int:
        """Count positions."""
        with self.conn.cursor() as cursor:
            if depth is None:
                cursor.execute("SELECT COUNT(*) FROM positions")
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM positions WHERE depth = %s", (depth,)
                )
            return cursor.fetchone()[0]

    def get_max_depth(self) -> int:
        """Get maximum depth."""
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT MAX(depth) FROM positions")
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

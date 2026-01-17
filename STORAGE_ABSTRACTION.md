# Storage Layer Abstraction Design

## Goal: Swap backends without changing solver logic

The key is to define a **storage interface** that all backends implement.

## Core Operations Needed

### BFS Phase (Write-Heavy)
1. `insert_position(state, depth, parent_hash)` - Add new position
2. `insert_batch(positions)` - Bulk insert (critical for performance)
3. `get_positions_at_depth(depth)` - Retrieve all positions at given depth
4. `position_exists(state_hash)` - Check for duplicates
5. `count_at_depth(depth)` - Statistics

### Minimax Phase (Read-Heavy + Updates)
1. `get_position(state_hash)` - Lookup specific position
2. `get_positions_by_seed_count(seed_count)` - Batch retrieval
3. `update_value(state_hash, minimax_value, best_move)` - Store solution
4. `get_child_values(child_hashes)` - Batch lookup for minimax

### Metadata
1. `get_stats()` - Total positions, depth range, etc.
2. `flush()` - Ensure data persisted
3. `close()` - Cleanup

---

## Python Interface (Abstract Base Class)

```python
from abc import ABC, abstractmethod
from typing import List, Optional, Iterator
from dataclasses import dataclass

@dataclass
class Position:
    state_hash: int      # 8-byte hash
    state: bytes         # 9-byte packed state
    depth: int
    seed_count: int      # Derived: sum of seeds
    minimax_value: Optional[int] = None
    best_move: Optional[int] = None

class StorageBackend(ABC):
    """Abstract interface for position storage"""

    @abstractmethod
    def insert(self, position: Position) -> bool:
        """Insert single position. Returns False if duplicate."""
        pass

    @abstractmethod
    def insert_batch(self, positions: List[Position]) -> int:
        """
        Bulk insert positions, auto-deduplicating.
        Returns number of new positions inserted.
        """
        pass

    @abstractmethod
    def exists(self, state_hash: int) -> bool:
        """Check if position already stored"""
        pass

    @abstractmethod
    def get(self, state_hash: int) -> Optional[Position]:
        """Retrieve position by hash"""
        pass

    @abstractmethod
    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        """Iterate all positions at given depth"""
        pass

    @abstractmethod
    def get_positions_by_seed_count(self, seed_count: int) -> Iterator[Position]:
        """Iterate all positions with given seed count"""
        pass

    @abstractmethod
    def update_solution(self, state_hash: int, minimax_value: int, best_move: int):
        """Update position with solved minimax value"""
        pass

    @abstractmethod
    def count_positions(self, depth: Optional[int] = None) -> int:
        """Count total positions, optionally filtered by depth"""
        pass

    @abstractmethod
    def flush(self):
        """Ensure all pending writes are persisted"""
        pass

    @abstractmethod
    def close(self):
        """Cleanup and close connection"""
        pass
```

---

## Backend Implementations

### 1. SQLite Backend (Local Development)

```python
import sqlite3
from typing import List, Optional, Iterator

class SQLiteBackend(StorageBackend):
    def __init__(self, db_path: str = "mancala.db"):
        self.conn = sqlite3.connect(db_path)
        self._create_schema()
        self._optimize()

    def _create_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                state_hash INTEGER PRIMARY KEY,
                state BLOB NOT NULL,
                depth INTEGER NOT NULL,
                seed_count INTEGER NOT NULL,
                minimax_value INTEGER,
                best_move INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_depth ON positions(depth);
            CREATE INDEX IF NOT EXISTS idx_seed_count ON positions(seed_count);
        """)

    def _optimize(self):
        """SQLite performance tuning"""
        self.conn.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
            PRAGMA cache_size = -64000;  -- 64 MB cache
            PRAGMA temp_store = MEMORY;
        """)

    def insert(self, position: Position) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO positions (state_hash, state, depth, seed_count) VALUES (?, ?, ?, ?)",
                (position.state_hash, position.state, position.depth, position.seed_count)
            )
            return True
        except sqlite3.IntegrityError:  # Duplicate
            return False

    def insert_batch(self, positions: List[Position]) -> int:
        # Use INSERT OR IGNORE for deduplication
        cursor = self.conn.executemany(
            "INSERT OR IGNORE INTO positions (state_hash, state, depth, seed_count) VALUES (?, ?, ?, ?)",
            [(p.state_hash, p.state, p.depth, p.seed_count) for p in positions]
        )
        return cursor.rowcount

    def exists(self, state_hash: int) -> bool:
        cursor = self.conn.execute("SELECT 1 FROM positions WHERE state_hash = ?", (state_hash,))
        return cursor.fetchone() is not None

    def get(self, state_hash: int) -> Optional[Position]:
        cursor = self.conn.execute(
            "SELECT * FROM positions WHERE state_hash = ?", (state_hash,)
        )
        row = cursor.fetchone()
        if row:
            return Position(*row)
        return None

    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        cursor = self.conn.execute(
            "SELECT * FROM positions WHERE depth = ?", (depth,)
        )
        for row in cursor:
            yield Position(*row)

    def get_positions_by_seed_count(self, seed_count: int) -> Iterator[Position]:
        cursor = self.conn.execute(
            "SELECT * FROM positions WHERE seed_count = ?", (seed_count,)
        )
        for row in cursor:
            yield Position(*row)

    def update_solution(self, state_hash: int, minimax_value: int, best_move: int):
        self.conn.execute(
            "UPDATE positions SET minimax_value = ?, best_move = ? WHERE state_hash = ?",
            (minimax_value, best_move, state_hash)
        )

    def count_positions(self, depth: Optional[int] = None) -> int:
        if depth is None:
            cursor = self.conn.execute("SELECT COUNT(*) FROM positions")
        else:
            cursor = self.conn.execute("SELECT COUNT(*) FROM positions WHERE depth = ?", (depth,))
        return cursor.fetchone()[0]

    def flush(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()
```

### 2. Cloud Bigtable Backend

```python
from google.cloud import bigtable
from google.cloud.bigtable import row_filters
import struct

class BigtableBackend(StorageBackend):
    def __init__(self, project_id: str, instance_id: str, table_id: str = "positions"):
        client = bigtable.Client(project=project_id, admin=True)
        instance = client.instance(instance_id)
        self.table = instance.table(table_id)
        self._create_table_if_needed()

    def _create_table_if_needed(self):
        # Column families: 'meta' for metadata, 'sol' for solution
        if not self.table.exists():
            self.table.create(column_families={'meta': None, 'sol': None})

    def _row_key(self, state_hash: int) -> bytes:
        """Convert hash to row key"""
        return struct.pack('>Q', state_hash)  # Big-endian for sorted scans

    def insert(self, position: Position) -> bool:
        row_key = self._row_key(position.state_hash)
        row = self.table.direct_row(row_key)

        # Check if exists (Bigtable doesn't have native INSERT OR IGNORE)
        if self.exists(position.state_hash):
            return False

        row.set_cell('meta', 'state', position.state)
        row.set_cell('meta', 'depth', str(position.depth).encode())
        row.set_cell('meta', 'seed_count', str(position.seed_count).encode())
        row.commit()
        return True

    def insert_batch(self, positions: List[Position]) -> int:
        rows = []
        for p in positions:
            row = self.table.direct_row(self._row_key(p.state_hash))
            row.set_cell('meta', 'state', p.state)
            row.set_cell('meta', 'depth', str(p.depth).encode())
            row.set_cell('meta', 'seed_count', str(p.seed_count).encode())
            rows.append(row)

        # Batch write
        statuses = self.table.mutate_rows(rows)
        return sum(1 for status in statuses if status.code == 0)

    def exists(self, state_hash: int) -> bool:
        row_key = self._row_key(state_hash)
        row = self.table.read_row(row_key)
        return row is not None

    def get(self, state_hash: int) -> Optional[Position]:
        row_key = self._row_key(state_hash)
        row = self.table.read_row(row_key)
        if not row:
            return None

        return Position(
            state_hash=state_hash,
            state=row.cells['meta'][b'state'][0].value,
            depth=int(row.cells['meta'][b'depth'][0].value),
            seed_count=int(row.cells['meta'][b'seed_count'][0].value),
            minimax_value=int(row.cells['sol'][b'value'][0].value) if b'value' in row.cells.get('sol', {}) else None,
            best_move=int(row.cells['sol'][b'move'][0].value) if b'move' in row.cells.get('sol', {}) else None
        )

    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        # Note: Bigtable doesn't efficiently support filtering by depth
        # Would need secondary index or scan all rows
        # This is a limitation - consider using Cloud Storage + Parquet instead
        raise NotImplementedError("Bigtable doesn't efficiently support depth queries")

    def get_positions_by_seed_count(self, seed_count: int) -> Iterator[Position]:
        # Same limitation as above
        raise NotImplementedError("Use Cloud Storage + Parquet for range scans")

    def update_solution(self, state_hash: int, minimax_value: int, best_move: int):
        row_key = self._row_key(state_hash)
        row = self.table.direct_row(row_key)
        row.set_cell('sol', 'value', str(minimax_value).encode())
        row.set_cell('sol', 'move', str(best_move).encode())
        row.commit()

    def flush(self):
        pass  # Bigtable commits immediately

    def close(self):
        pass  # No explicit close needed
```

### 3. PostgreSQL Backend (Cloud or Local)

```python
import psycopg2
from psycopg2.extras import execute_batch

class PostgreSQLBackend(StorageBackend):
    def __init__(self, connection_string: str):
        self.conn = psycopg2.connect(connection_string)
        self._create_schema()
        self._optimize()

    def _create_schema(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    state_hash BIGINT PRIMARY KEY,
                    state BYTEA NOT NULL,
                    depth INTEGER NOT NULL,
                    seed_count INTEGER NOT NULL,
                    minimax_value INTEGER,
                    best_move SMALLINT
                );

                CREATE INDEX IF NOT EXISTS idx_depth ON positions(depth);
                CREATE INDEX IF NOT EXISTS idx_seed_count ON positions(seed_count);
            """)
        self.conn.commit()

    def _optimize(self):
        """PostgreSQL tuning for bulk operations"""
        with self.conn.cursor() as cur:
            cur.execute("SET synchronous_commit = OFF")  # Faster, acceptable risk

    def insert(self, position: Position) -> bool:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO positions (state_hash, state, depth, seed_count) VALUES (%s, %s, %s, %s)",
                    (position.state_hash, position.state, position.depth, position.seed_count)
                )
            return True
        except psycopg2.IntegrityError:
            self.conn.rollback()
            return False

    def insert_batch(self, positions: List[Position]) -> int:
        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                "INSERT INTO positions (state_hash, state, depth, seed_count) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(p.state_hash, p.state, p.depth, p.seed_count) for p in positions]
            )
            return cur.rowcount

    def exists(self, state_hash: int) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM positions WHERE state_hash = %s", (state_hash,))
            return cur.fetchone() is not None

    def get(self, state_hash: int) -> Optional[Position]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE state_hash = %s", (state_hash,))
            row = cur.fetchone()
            if row:
                return Position(*row)
        return None

    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE depth = %s", (depth,))
            for row in cur:
                yield Position(*row)

    def get_positions_by_seed_count(self, seed_count: int) -> Iterator[Position]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE seed_count = %s", (seed_count,))
            for row in cur:
                yield Position(*row)

    def update_solution(self, state_hash: int, minimax_value: int, best_move: int):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE positions SET minimax_value = %s, best_move = %s WHERE state_hash = %s",
                (minimax_value, best_move, state_hash)
            )

    def count_positions(self, depth: Optional[int] = None) -> int:
        with self.conn.cursor() as cur:
            if depth is None:
                cur.execute("SELECT COUNT(*) FROM positions")
            else:
                cur.execute("SELECT COUNT(*) FROM positions WHERE depth = %s", (depth,))
            return cur.fetchone()[0]

    def flush(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()
```

### 4. Cloud Storage + Parquet Backend (Hybrid)

```python
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage

class CloudStorageBackend(StorageBackend):
    """
    Write-optimized backend using Parquet files on GCS.
    Good for BFS phase, less ideal for random lookups in minimax phase.
    """
    def __init__(self, bucket_name: str, base_path: str = "positions"):
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.base_path = base_path
        self.local_buffer = []
        self.buffer_size = 100_000  # Flush every 100k positions

    def insert(self, position: Position) -> bool:
        self.local_buffer.append(position)
        if len(self.local_buffer) >= self.buffer_size:
            self.flush()
        return True  # Can't easily detect duplicates

    def insert_batch(self, positions: List[Position]) -> int:
        self.local_buffer.extend(positions)
        if len(self.local_buffer) >= self.buffer_size:
            self.flush()
        return len(positions)

    def flush(self):
        if not self.local_buffer:
            return

        # Convert to Parquet
        table = pa.Table.from_pydict({
            'state_hash': [p.state_hash for p in self.local_buffer],
            'state': [p.state for p in self.local_buffer],
            'depth': [p.depth for p in self.local_buffer],
            'seed_count': [p.seed_count for p in self.local_buffer],
        })

        # Write to GCS
        import time
        filename = f"{self.base_path}/batch_{int(time.time())}.parquet"
        blob = self.bucket.blob(filename)

        import io
        buf = io.BytesIO()
        pq.write_table(table, buf, compression='snappy')
        blob.upload_from_string(buf.getvalue())

        self.local_buffer.clear()

    def get_positions_at_depth(self, depth: int) -> Iterator[Position]:
        # Read all parquet files, filter by depth
        # This is slow but works for retrograde analysis
        blobs = self.bucket.list_blobs(prefix=self.base_path)
        for blob in blobs:
            import io
            buf = io.BytesIO(blob.download_as_bytes())
            table = pq.read_table(buf)
            df = table.to_pandas()
            for _, row in df[df['depth'] == depth].iterrows():
                yield Position(
                    state_hash=row['state_hash'],
                    state=row['state'],
                    depth=row['depth'],
                    seed_count=row['seed_count']
                )

    # ... implement other methods similarly
```

---

## Solver Code (Backend-Agnostic)

```python
class MancalaSolver:
    def __init__(self, storage: StorageBackend, num_pits: int, num_seeds: int):
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds

    def solve(self):
        """Main solving routine - works with ANY backend"""
        print("Phase 1: Building game graph (BFS)...")
        self.build_game_graph()

        print("Phase 2: Computing minimax values (retrograde)...")
        self.compute_minimax()

        print("Solve complete!")

    def build_game_graph(self):
        # Initialize with starting position
        start_state = self.create_start_state()
        start_pos = Position(
            state_hash=hash_state(start_state),
            state=pack_state(start_state),
            depth=0,
            seed_count=self.num_pits * 2 * self.num_seeds
        )
        self.storage.insert(start_pos)

        depth = 0
        while True:
            positions = list(self.storage.get_positions_at_depth(depth))
            if not positions:
                break

            print(f"Depth {depth}: {len(positions)} positions")

            new_positions = []
            for pos in positions:
                state = unpack_state(pos.state)
                for move in generate_legal_moves(state):
                    next_state = apply_move(state, move)
                    next_pos = Position(
                        state_hash=hash_state(next_state),
                        state=pack_state(next_state),
                        depth=depth + 1,
                        seed_count=sum(next_state)
                    )
                    new_positions.append(next_pos)

            # Batch insert handles deduplication
            count = self.storage.insert_batch(new_positions)
            print(f"  Added {count} new positions")

            self.storage.flush()
            depth += 1

    def compute_minimax(self):
        # Work backwards from endgame
        max_seeds = self.num_pits * 2 * self.num_seeds
        for seed_count in range(0, max_seeds + 1):
            positions = list(self.storage.get_positions_by_seed_count(seed_count))
            print(f"Seed count {seed_count}: {len(positions)} positions")

            for pos in positions:
                value, best_move = self.minimax(pos)
                self.storage.update_solution(pos.state_hash, value, best_move)

            self.storage.flush()

# Usage - swap backends easily!
if __name__ == "__main__":
    # Local development
    storage = SQLiteBackend("kalah_4_3.db")

    # Or cloud development
    # storage = PostgreSQLBackend("postgresql://user:pass@host/db")

    # Or production scale
    # storage = BigtableBackend("my-project", "mancala-instance")

    solver = MancalaSolver(storage, num_pits=4, num_seeds=3)
    solver.solve()
    storage.close()
```

---

## Summary: Easy to Swap?

**YES** ✅ - if you build the abstraction layer first!

### What Changes:
- 1 line: `storage = SQLiteBackend() → storage = PostgreSQLBackend()`
- Backend implementation file

### What Doesn't Change:
- All solver logic
- GameState class
- Move generation
- Minimax algorithm
- Statistics/reporting

### Recommended Approach:
1. **Start**: Build abstraction interface + SQLite backend
2. **Test**: Solve Kalah(4,3) with SQLite
3. **Scale**: Implement PostgreSQL or Bigtable backend
4. **Swap**: Change 1 line, re-run solver

### Key Design Principle:
**Program to an interface, not an implementation** - this is what makes swapping easy!

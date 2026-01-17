"""
Memory-efficient chunked BFS solver optimized for PostgreSQL.

Processes each depth in chunks to bound memory usage and enable
progress tracking for large-scale solves.

PostgreSQL Optimizations:
- ON CONFLICT DO NOTHING for zero-RAM deduplication
- Async write queue to hide database I/O latency
- Efficient LIMIT/OFFSET batch queries
- MVCC allows concurrent inserts without lock contention
"""

import logging
import threading
from queue import Queue
from typing import List, Optional
from tqdm import tqdm

from ..core import (
    GameState,
    create_starting_state,
    generate_legal_moves,
    apply_move,
    zobrist_hash,
    pack_state,
)
from ..storage import PostgreSQLBackend, Position
from ..utils import MemoryMonitor

logger = logging.getLogger(__name__)


class AsyncWriter:
    """
    Background writer thread for async database inserts.

    Workers can queue positions without blocking on DB I/O.
    Writer thread continuously pulls from queue and inserts.
    """

    def __init__(self, storage: PostgreSQLBackend):
        self.storage = storage
        self.queue: Queue = Queue(maxsize=1000)  # Bounded to prevent memory explosion
        self.total_queued = 0
        self.total_written = 0
        self.stop_flag = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[Exception] = None

    def start(self):
        """Start the background writer thread."""
        self.thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.thread.start()

    def _writer_loop(self):
        """Background thread that continuously writes from queue to database."""
        try:
            while not self.stop_flag.is_set() or not self.queue.empty():
                try:
                    # Wait up to 0.1s for item (allows checking stop_flag)
                    batch = self.queue.get(timeout=0.1)
                    if batch is None:  # Sentinel value to stop
                        break

                    # Write batch to database
                    self.storage.insert_batch(batch)
                    self.storage.flush()
                    self.total_written += len(batch)
                    self.queue.task_done()

                except Exception as e:
                    if not self.stop_flag.is_set():
                        logger.error(f"AsyncWriter error: {e}")
                        self.error = e
                        break
        except Exception as e:
            logger.error(f"AsyncWriter fatal error: {e}")
            self.error = e

    def put(self, positions: List[Position]) -> None:
        """Queue positions for async writing."""
        if self.error:
            raise self.error

        self.queue.put(positions)
        self.total_queued += len(positions)

    def wait_until_empty(self) -> None:
        """Block until all queued writes complete."""
        self.queue.join()

        if self.error:
            raise self.error

    def stop(self) -> None:
        """Stop the writer thread gracefully."""
        self.stop_flag.set()
        self.queue.put(None)  # Sentinel to wake up thread
        if self.thread:
            self.thread.join(timeout=10)


class ChunkedBFSSolver:
    """
    Memory-efficient BFS solver optimized for PostgreSQL.

    Strategy:
    - For each depth level:
      - Fetch parents in chunks from storage (LIMIT/OFFSET)
      - Generate all children for chunk
      - Queue for async write (non-blocking)
      - Continue immediately to next chunk
    - Bounded memory usage regardless of depth size
    """

    def __init__(
        self,
        storage: PostgreSQLBackend,
        num_pits: int,
        num_seeds: int,
        num_workers: int = 1,
        chunk_size: int = 50_000,
    ):
        """
        Initialize chunked BFS solver.

        Args:
            storage: PostgreSQL storage backend
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            num_workers: Number of parallel workers (not used yet, for future)
            chunk_size: Number of positions to process per chunk
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.num_workers = num_workers
        self.chunk_size = chunk_size

        # Memory monitoring
        self.memory_monitor = MemoryMonitor(
            warning_threshold_gb=4.0, critical_threshold_gb=2.0
        )

        logger.info(f"Chunked BFS solver initialized")
        logger.info(f"Workers: {num_workers} (currently single-threaded)")
        logger.info(f"Chunk size: {chunk_size:,} positions per chunk")
        logger.info(f"PostgreSQL deduplication: ON CONFLICT DO NOTHING (zero RAM overhead)")
        logger.info(f"Async writes: enabled (background writer thread)")
        logger.info(f"Memory monitoring: enabled")

    def build_game_graph(self) -> int:
        """
        Build complete game graph using chunked processing.

        Returns:
            Total number of positions discovered
        """
        logger.info("Starting chunked BFS")

        # Insert starting position
        start_state = create_starting_state(self.num_pits, self.num_seeds)
        start_hash = zobrist_hash(start_state)
        start_pos = Position(
            state_hash=start_hash,
            state=pack_state(start_state),
            depth=0,
            seeds_in_pits=start_state.seeds_in_pits,
        )
        self.storage.insert(start_pos)
        self.storage.flush()
        logger.info("Inserted starting position")

        current_depth = 0
        total_positions = 1

        while True:
            # Count positions at current depth
            positions_at_depth = self.storage.count_positions(depth=current_depth)

            if positions_at_depth == 0:
                logger.info(f"Depth {current_depth}: No positions - BFS complete")
                break

            logger.info(
                f"Depth {current_depth}: Processing {positions_at_depth:,} positions in chunks"
            )

            # Process this depth in chunks
            new_positions_count = self._process_depth_chunked(current_depth, positions_at_depth)

            total_positions += new_positions_count
            logger.info(
                f"Depth {current_depth}: Generated {new_positions_count:,} new positions (total: {total_positions:,})"
            )

            current_depth += 1

        logger.info(f"Chunked BFS complete! Total positions: {total_positions:,}")
        return total_positions

    def _process_depth_chunked(self, depth: int, total_at_depth: int) -> int:
        """
        Process all positions at a depth in chunks.

        Args:
            depth: Current depth to process
            total_at_depth: Total positions at this depth

        Returns:
            Number of new positions generated
        """
        num_chunks = (total_at_depth + self.chunk_size - 1) // self.chunk_size

        # Calculate logging interval for intra-depth progress
        log_interval = max(1, min(100, num_chunks // 10))

        # Start async writer
        async_writer = AsyncWriter(self.storage)
        async_writer.start()
        logger.info(f"Async writer started: database writes will not block chunk processing")

        total_inserted = 0
        offset = 0

        # Progress bar for this depth
        with tqdm(total=num_chunks, desc=f"Depth {depth}", unit="chunk") as pbar:
            chunk_num = 0

            while True:
                chunk_num += 1

                # Memory monitoring - pause if critical
                if self.memory_monitor.is_critical():
                    logger.warning(
                        "Critical memory pressure detected, pausing 10s for GC"
                    )
                    self.memory_monitor.log_status()
                    import time
                    time.sleep(10)

                # Fetch chunk of parent positions
                parents = self._fetch_chunk(depth, offset, self.chunk_size)

                if not parents:
                    break

                # Generate all children for this chunk
                chunk_new_positions = []
                for parent_pos in parents:
                    from ..core.game_state import unpack_state

                    parent_state = unpack_state(parent_pos.state, self.num_pits)

                    for move in generate_legal_moves(parent_state):
                        child_state = apply_move(parent_state, move)
                        child_hash = zobrist_hash(child_state)

                        # PostgreSQL handles dedup via ON CONFLICT DO NOTHING
                        child_pos = Position(
                            state_hash=child_hash,
                            state=pack_state(child_state),
                            depth=depth + 1,
                            seeds_in_pits=child_state.seeds_in_pits,
                        )
                        chunk_new_positions.append(child_pos)

                # Queue for async writing (non-blocking!)
                if chunk_new_positions:
                    async_writer.put(chunk_new_positions)
                    total_inserted += len(chunk_new_positions)

                # Update progress
                pbar.set_postfix({
                    "chunk": f"{chunk_num}/{num_chunks}",
                    "new": len(chunk_new_positions),
                    "total_new": total_inserted,
                })
                pbar.update(1)

                # Periodic logging for TUI monitoring
                if chunk_num % log_interval == 0 or chunk_num == num_chunks:
                    pct = (chunk_num / num_chunks * 100) if num_chunks > 0 else 0
                    logger.info(
                        f"  Depth {depth} progress: chunk {chunk_num}/{num_chunks} ({pct:.1f}%) - "
                        f"{total_inserted:,} new positions generated so far"
                    )

                offset += self.chunk_size

        # Wait for async writes to complete before counting
        logger.info(f"Waiting for async writes to complete...")
        async_writer.wait_until_empty()
        async_writer.stop()
        logger.info(f"All writes complete: {async_writer.total_written:,} positions written")

        # Final count from database
        final_count = self.storage.count_positions(depth=depth + 1)
        return final_count

    def _fetch_chunk(self, depth: int, offset: int, limit: int) -> List[Position]:
        """
        Fetch a chunk of positions at a given depth using efficient LIMIT/OFFSET.

        Args:
            depth: Depth to fetch from
            offset: Starting offset
            limit: Maximum positions to fetch

        Returns:
            List of positions
        """
        return self.storage.get_positions_at_depth_batch(depth, limit, offset)

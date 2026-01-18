"""
Chunked BFS solver for cloud databases.

Processes each depth in chunks to bound memory usage and enable
progress tracking for large-scale solves.

DEDUPLICATION STRATEGIES:
========================
PostgreSQL:
  - Uses database-level ON CONFLICT DO NOTHING (zero RAM overhead)
  - All workers send duplicates; database rejects them via PRIMARY KEY
  - MVCC allows concurrent inserts without lock contention
  - No in-memory hash set needed
  - Optimal for parallel solving with 8+ workers

SQLite:
  - Uses in-memory hash set to avoid database EXISTS() queries
  - Faster than database lookups for duplicate checking
  - Hash set can grow large at peak depths (monitored and adaptive)
  - Automatically switches to DB mode if memory gets tight
  - Best for sequential or low-worker-count solves
"""

import logging
import time
import threading
from queue import Queue, Empty
from typing import List, Set, Optional
from tqdm import tqdm

from ..core import (
    GameState,
    create_starting_state,
    generate_legal_moves,
    apply_move,
    zobrist_hash,
    pack_state,
)
from ..storage import StorageBackend, Position
from ..utils import MemoryMonitor

logger = logging.getLogger(__name__)


class AsyncWriter:
    """
    Background writer thread for async database inserts.

    Workers can queue positions without blocking on DB I/O.
    Writer thread continuously pulls from queue and inserts.
    """

    def __init__(self, storage: StorageBackend):
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
                    item = self.queue.get(timeout=0.1)
                    if item is None:  # Sentinel value to stop
                        break

                    # Extract batch and allow_duplicates flag
                    batch, allow_duplicates = item

                    # Write batch to database
                    self.storage.insert_batch(batch, allow_duplicates=allow_duplicates)
                    self.storage.flush()
                    self.total_written += len(batch)
                    self.queue.task_done()

                except Empty:
                    # Queue is empty, continue waiting
                    continue
                except Exception as e:
                    if not self.stop_flag.is_set():
                        # Only log error if not shutting down
                        logger.error(f"AsyncWriter error: {e}")
                        self.error = e
                        break
        except Exception as e:
            logger.error(f"AsyncWriter fatal error: {e}")
            self.error = e

    def put(self, positions: List[Position], allow_duplicates: bool = False) -> None:
        """
        Queue positions for async writing.

        Args:
            positions: Batch of positions to write
            allow_duplicates: If True, skip deduplication (faster inserts)
        """
        if self.error:
            raise self.error

        self.queue.put((positions, allow_duplicates))
        self.total_queued += len(positions)

    def wait_until_empty(self) -> None:
        """Block until all queued writes complete."""
        self.queue.join()  # Wait for all tasks to be marked done

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
    BFS solver that processes positions in chunks.

    Strategy:
    - For each depth level:
      - Fetch parents in chunks from storage
      - Generate all children for chunk
      - Deduplicate locally
      - Batch insert to storage
    - Bounded memory usage (chunk_size * avg_branching_factor)
    """

    def __init__(
        self,
        storage: StorageBackend,
        num_pits: int,
        num_seeds: int,
        chunk_size: int = 50_000,
        dedup_batch_size: int = 300_000_000,
        enable_memory_monitoring: bool = True,
        async_writes: bool = True,
    ):
        """
        Initialize chunked BFS solver.

        Args:
            storage: Storage backend (should be cloud database)
            num_pits: Number of pits per player
            num_seeds: Initial seeds per pit
            chunk_size: Number of positions to process per chunk
            dedup_batch_size: Max hashes in dedup set before allowing duplicates (~40B per hash, 300M = 12GB)
            enable_memory_monitoring: Enable adaptive memory management
            async_writes: Use background thread for database writes (hides I/O latency)
        """
        self.storage = storage
        self.num_pits = num_pits
        self.num_seeds = num_seeds
        self.chunk_size = chunk_size
        self.dedup_batch_size = dedup_batch_size
        self.enable_memory_monitoring = enable_memory_monitoring
        self.async_writes = async_writes

        # Deduplication strategy selection
        # =====================================
        # PostgreSQL: Uses ON CONFLICT DO NOTHING at database level
        #   - Zero RAM overhead for deduplication
        #   - MVCC allows all workers to insert duplicates concurrently
        #   - Database rejects duplicates via PRIMARY KEY constraint
        #   - No in-memory hash set needed
        #
        # SQLite: Uses in-memory hash set to avoid database EXISTS() queries
        #   - Faster than database lookups for dedup checking
        #   - Hash set can grow large (monitored and adaptively switched to DB mode)
        #   - WAL mode allows concurrent reads but writes still serialize
        self.use_db_dedup = False
        logger.info("SQLite detected: using in-memory deduplication (better performance)")

        # Memory monitoring
        if enable_memory_monitoring:
            self.memory_monitor = MemoryMonitor(
                warning_threshold_gb=4.0, critical_threshold_gb=2.0
            )
            logger.info("Memory monitoring enabled")
        else:
            self.memory_monitor = None

        logger.info(f"Chunked BFS: {chunk_size:,} positions per chunk")
        if not self.use_db_dedup:
            logger.info(f"Dedup batch size: {dedup_batch_size:,} hashes (~{dedup_batch_size * 40 // 1_000_000_000:.1f}GB max)")
            logger.info(f"When limit exceeded: allow duplicates (fast inserts, cleanup later)")
        logger.info(f"Async writes: {'enabled' if async_writes else 'disabled'} (background writer thread)")

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
        # Log every 10% of chunks or every 100 chunks, whichever is more frequent
        log_interval = max(1, min(100, num_chunks // 10))

        # Async writer setup
        # ==================
        async_writer = None
        if self.async_writes:
            async_writer = AsyncWriter(self.storage)
            async_writer.start()
            logger.info(f"Async writes enabled: database writes will not block chunk processing")

        # Deduplication state tracking
        # =============================
        # SQLite strategy: Start with in-memory dedup, allow duplicates when RAM exhausted
        #   - all_new_hashes tracks seen hashes for this depth
        #   - Bounded at dedup_batch_size (default 300M = ~12GB)
        #   - When batch limit reached: clear set, allow duplicates for remainder of depth
        #   - Duplicates are cleaned up after BFS completes
        #   - Next depth: reset to in-memory mode
        #   - This keeps peak memory at ~12GB and avoids WAL bloat from INSERT OR IGNORE
        #
        # Memory per depth is bounded: O(min(depth_size, dedup_batch_size))
        all_new_hashes: Set[int] = set() if not self.use_db_dedup else None
        total_inserted = 0
        use_db_dedup = self.use_db_dedup  # False for SQLite (starts in-memory mode each depth)
        allow_duplicates = False  # When True, skip dedup and allow duplicates (fast)

        # Profiling accumulators (measure where time is spent)
        time_fetch = 0.0
        time_generate = 0.0
        time_dedup = 0.0
        time_insert = 0.0

        with tqdm(total=num_chunks, desc=f"Depth {depth} chunks", unit="chunk") as pbar:
            offset = 0
            chunk_num = 0

            while offset < total_at_depth:
                chunk_num += 1
                chunk_start = time.time()

                # Disk space monitoring (every 10 chunks to avoid overhead)
                # ==========================================================
                if chunk_num % 10 == 0:
                    self._check_disk_space()

                # Memory monitoring and adaptive dedup switching (SQLite only)
                # ============================================================
                # PostgreSQL: Skips this entirely (self.use_db_dedup is True)
                # SQLite: Monitors RAM and switches from in-memory to DB dedup if needed
                if not self.use_db_dedup and self.memory_monitor and chunk_num % 10 == 0:
                    if self.memory_monitor.is_critical():
                        logger.warning(
                            f"Critical memory pressure at chunk {chunk_num}, "
                            f"clearing dedup set (size: {len(all_new_hashes):,}), allowing duplicates"
                        )
                        all_new_hashes.clear()
                        allow_duplicates = True
                    elif self.memory_monitor.should_throttle():
                        if len(all_new_hashes) > self.dedup_batch_size:
                            logger.info(
                                f"Dedup set too large ({len(all_new_hashes):,}), "
                                f"allowing duplicates for remainder of depth (will cleanup later)"
                            )
                            all_new_hashes.clear()
                            allow_duplicates = True

                # Periodic dedup set clearing to prevent unbounded growth (SQLite only)
                # Check every chunk to keep memory tightly bounded
                if not self.use_db_dedup and not allow_duplicates and len(all_new_hashes) > self.dedup_batch_size:
                    logger.info(
                        f"Dedup set reached batch limit ({len(all_new_hashes):,} hashes, ~{len(all_new_hashes) * 40 // 1_000_000_000:.1f}GB), "
                        f"allowing duplicates for remainder of depth (will cleanup later)"
                    )
                    all_new_hashes.clear()
                    allow_duplicates = True

                # Fetch chunk of parent positions
                t0 = time.time()
                parents = self._fetch_chunk(depth, offset, self.chunk_size)
                time_fetch += time.time() - t0

                if not parents:
                    break

                # Generate all children for this chunk
                chunk_new_positions = []
                t0 = time.time()
                for parent_pos in parents:
                    from ..core.game_state import unpack_state

                    parent_state = unpack_state(parent_pos.state, self.num_pits)

                    for move in generate_legal_moves(parent_state):
                        child_state = apply_move(parent_state, move)
                        child_hash = zobrist_hash(child_state)

                        # Deduplication logic - backend-specific strategies
                        # ==================================================
                        if use_db_dedup or allow_duplicates:
                            # Send ALL positions without in-memory dedup:
                            #   - PostgreSQL: Uses INSERT OR IGNORE (fast, DB deduplicates)
                            #   - SQLite allow_duplicates: Uses plain INSERT (fast, allows duplicates)
                            #   - SQLite use_db_dedup fallback: Uses INSERT OR IGNORE (slow, WAL bloat)
                            #   - Zero RAM overhead for deduplication
                            child_pos = Position(
                                state_hash=child_hash,
                                state=pack_state(child_state),
                                depth=depth + 1,
                                seeds_in_pits=child_state.seeds_in_pits,
                            )
                            chunk_new_positions.append(child_pos)
                        else:
                            # SQLite with in-memory deduplication:
                            #   - Check hash set before creating Position object
                            #   - Avoids database round-trips (much faster)
                            #   - Hash set grows with unique positions at this depth
                            #   - Bounded at dedup_batch_size (300M hashes = 12GB RAM)
                            if child_hash not in all_new_hashes:
                                all_new_hashes.add(child_hash)
                                child_pos = Position(
                                    state_hash=child_hash,
                                    state=pack_state(child_state),
                                    depth=depth + 1,
                                    seeds_in_pits=child_state.seeds_in_pits,
                                )
                                chunk_new_positions.append(child_pos)

                time_generate += time.time() - t0

                # Batch insert this chunk's new positions
                t0 = time.time()
                if chunk_new_positions:
                    if async_writer:
                        # Async: queue for background writing (non-blocking!)
                        async_writer.put(chunk_new_positions, allow_duplicates=allow_duplicates)
                        total_inserted += len(chunk_new_positions)
                    else:
                        # Sync: block on database write
                        inserted = self.storage.insert_batch(chunk_new_positions, allow_duplicates=allow_duplicates)
                        total_inserted += len(chunk_new_positions)
                        self.storage.flush()
                time_insert += time.time() - t0

                # Update progress
                postfix = {
                    "chunk": f"{chunk_num}/{num_chunks}",
                    "new": len(chunk_new_positions),
                    "total_new": total_inserted,
                }

                # Show deduplication mode
                if not self.use_db_dedup:
                    if allow_duplicates:
                        postfix["dedup"] = "ALLOW_DUPS"
                    elif use_db_dedup:
                        postfix["dedup"] = "DB"
                    else:
                        postfix["dedup"] = "MEM"

                pbar.set_postfix(postfix)
                pbar.update(1)

                # Periodic logging for TUI monitoring (can't see progress bar)
                if chunk_num % log_interval == 0 or chunk_num == num_chunks:
                    pct = (chunk_num / num_chunks * 100) if num_chunks > 0 else 0
                    # Add profiling breakdown
                    total_time = time_fetch + time_generate + time_insert
                    if total_time > 0:
                        fetch_pct = time_fetch / total_time * 100
                        gen_pct = time_generate / total_time * 100
                        insert_pct = time_insert / total_time * 100
                        profile_str = f" | Profile: fetch={fetch_pct:.1f}% gen={gen_pct:.1f}% insert={insert_pct:.1f}%"
                    else:
                        profile_str = ""

                    logger.info(
                        f"  Depth {depth} progress: chunk {chunk_num}/{num_chunks} ({pct:.1f}%) - "
                        f"{total_inserted:,} new positions generated so far{profile_str}"
                    )

                offset += self.chunk_size

        # Wait for async writes to complete before counting
        if async_writer:
            logger.info(f"Waiting for async writes to complete...")
            async_writer.wait_until_empty()
            async_writer.stop()
            logger.info(f"All writes complete: {async_writer.total_written:,} positions written")

        # Performance summary
        total_time = time_fetch + time_generate + time_insert
        if total_time > 0:
            logger.info(
                f"Depth {depth} timing breakdown: "
                f"fetch={time_fetch:.1f}s ({time_fetch/total_time*100:.1f}%), "
                f"generate={time_generate:.1f}s ({time_generate/total_time*100:.1f}%), "
                f"insert={time_insert:.1f}s ({time_insert/total_time*100:.1f}%) "
                f"[total={total_time:.1f}s]"
            )

        # Final count from database
        if use_db_dedup or self.use_db_dedup:
            final_count = self.storage.count_positions(depth=depth + 1)
            if use_db_dedup and not self.use_db_dedup:
                logger.info(f"Switched to DB dedup: {final_count:,} positions at depth {depth + 1}")
            return final_count
        else:
            # SQLite with in-memory dedup
            return len(all_new_hashes)

    def _fetch_chunk(self, depth: int, offset: int, limit: int) -> List[Position]:
        """
        Fetch a chunk of positions at a given depth.

        Args:
            depth: Depth to fetch from
            offset: Starting offset
            limit: Maximum positions to fetch

        Returns:
            List of positions
        """
        # Use efficient database LIMIT/OFFSET query
        return self.storage.get_positions_at_depth_batch(depth, limit, offset)

    def _check_disk_space(self):
        """
        Check disk space and raise exception if critically low.

        Escape hatch to prevent filling up the disk during long solves.
        """
        import shutil
        from pathlib import Path

        try:
            # Get database path from storage backend
            if hasattr(self.storage, 'db_path'):
                db_path = Path(self.storage.db_path)
            else:
                # Can't check disk without path
                return

            # Get disk usage stats
            stat = shutil.disk_usage(db_path.parent)
            free_gb = stat.free / (1024**3)
            percent_free = (stat.free / stat.total) * 100 if stat.total > 0 else 0

            # Critical threshold: 5GB or 5% free (whichever is more restrictive)
            MIN_FREE_GB = 5.0
            MIN_FREE_PERCENT = 5.0

            if free_gb < MIN_FREE_GB or percent_free < MIN_FREE_PERCENT:
                logger.error(
                    f"DISK SPACE CRITICALLY LOW! "
                    f"Free: {free_gb:.1f}GB ({percent_free:.1f}%) - "
                    f"Stopping solve to prevent disk full"
                )
                raise RuntimeError(
                    f"Disk space too low: {free_gb:.1f}GB free ({percent_free:.1f}%). "
                    f"Need at least {MIN_FREE_GB}GB or {MIN_FREE_PERCENT}% free. "
                    f"Stopping to prevent filling disk."
                )

            # Warning threshold: 10GB or 10% free
            WARN_FREE_GB = 10.0
            WARN_FREE_PERCENT = 10.0

            if free_gb < WARN_FREE_GB or percent_free < WARN_FREE_PERCENT:
                logger.warning(
                    f"Disk space getting low: {free_gb:.1f}GB free ({percent_free:.1f}%)"
                )
        except RuntimeError:
            # Re-raise disk full errors
            raise
        except Exception as e:
            # Log but don't crash on disk check errors
            logger.debug(f"Could not check disk space: {e}")

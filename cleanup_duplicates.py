#!/usr/bin/env python3
"""
Clean up duplicate positions in the database after BFS.

When BFS runs with allow_duplicates=True, the database can contain
duplicate state_hash entries. This script removes all but one copy
of each duplicate and vacuums the database to reclaim space.

Usage:
    python3 cleanup_duplicates.py data/databases/kalah_6_4.db
"""

import argparse
import sqlite3
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def cleanup_duplicates(db_path: str, dry_run: bool = False) -> None:
    """
    Remove duplicate positions from database.

    Args:
        db_path: Path to SQLite database
        dry_run: If True, only count duplicates without removing
    """
    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    logger.info(f"Opening database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get initial statistics
    logger.info("Analyzing database...")
    cursor.execute("SELECT COUNT(*) FROM positions")
    total_before = cursor.fetchone()[0]
    logger.info(f"Total positions before cleanup: {total_before:,}")

    cursor.execute("SELECT COUNT(DISTINCT state_hash) FROM positions")
    unique_count = cursor.fetchone()[0]
    logger.info(f"Unique positions: {unique_count:,}")

    duplicate_count = total_before - unique_count
    if duplicate_count == 0:
        logger.info("No duplicates found - database is clean!")
        conn.close()
        return

    logger.info(f"Duplicates to remove: {duplicate_count:,} ({duplicate_count / total_before * 100:.1f}%)")

    if dry_run:
        logger.info("Dry run mode - not removing duplicates")
        conn.close()
        return

    # Get database size before
    db_size_before = Path(db_path).stat().st_size / (1024**3)
    logger.info(f"Database size before cleanup: {db_size_before:.2f} GB")

    # Remove duplicates (WITHOUT ROWID compatible)
    logger.info("Removing duplicates (using temp table for WITHOUT ROWID compatibility)...")

    # Create temp table with unique positions
    cursor.execute("""
        CREATE TABLE positions_dedup AS
        SELECT
            state_hash,
            MIN(state) as state,
            MIN(depth) as depth,
            MIN(seeds_in_pits) as seeds_in_pits,
            MIN(minimax_value) as minimax_value,
            MIN(best_move) as best_move
        FROM positions
        GROUP BY state_hash
    """)

    # Drop old table and rename
    cursor.execute("DROP TABLE positions")
    cursor.execute("ALTER TABLE positions_dedup RENAME TO positions")

    # Recreate indexes
    cursor.execute("CREATE INDEX idx_depth ON positions(depth)")
    cursor.execute("CREATE INDEX idx_seeds_in_pits ON positions(seeds_in_pits)")

    logger.info(f"Removed {duplicate_count:,} duplicates")
    conn.commit()

    # Verify cleanup
    cursor.execute("SELECT COUNT(*) FROM positions")
    total_after = cursor.fetchone()[0]
    logger.info(f"Total positions after cleanup: {total_after:,}")

    if total_after != unique_count:
        logger.warning(f"WARNING: Expected {unique_count:,} positions but found {total_after:,}")

    # Vacuum to reclaim space
    logger.info("Vacuuming database to reclaim space (this may take a while)...")
    conn.execute("VACUUM")
    conn.close()

    # Get database size after
    db_size_after = Path(db_path).stat().st_size / (1024**3)
    space_saved = db_size_before - db_size_after
    logger.info(f"Database size after cleanup: {db_size_after:.2f} GB")
    logger.info(f"Space reclaimed: {space_saved:.2f} GB ({space_saved / db_size_before * 100:.1f}%)")

    logger.info("Cleanup complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up duplicate positions in Mancala solver database"
    )
    parser.add_argument(
        "db_path",
        help="Path to SQLite database file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count duplicates without removing them"
    )

    args = parser.parse_args()
    cleanup_duplicates(args.db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

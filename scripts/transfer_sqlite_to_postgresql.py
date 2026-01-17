#!/usr/bin/env python3
"""
Transfer positions from SQLite to PostgreSQL.

Usage:
    python3 scripts/transfer_sqlite_to_postgresql.py \
        --sqlite-db data/databases/kalah_4_3.db \
        --pg-host localhost \
        --pg-port 5433 \
        --pg-database mancala \
        --pg-user postgres \
        --pg-password mancala-first-solve
"""

import argparse
import sqlite3
import psycopg2
import psycopg2.extras
from tqdm import tqdm


def transfer(sqlite_path, pg_host, pg_port, pg_database, pg_user, pg_password, batch_size=10000):
    """Transfer all positions from SQLite to PostgreSQL."""

    # Connect to SQLite
    print(f"📂 Connecting to SQLite: {sqlite_path}")
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    # Count total positions
    sqlite_cursor.execute("SELECT COUNT(*) FROM positions")
    total_positions = sqlite_cursor.fetchone()[0]
    print(f"📊 Total positions to transfer: {total_positions:,}")

    # Connect to PostgreSQL
    print(f"🐘 Connecting to PostgreSQL: {pg_host}:{pg_port}/{pg_database}")
    pg_conn = psycopg2.connect(
        host=pg_host,
        port=pg_port,
        database=pg_database,
        user=pg_user,
        password=pg_password
    )
    pg_conn.autocommit = False

    # Clear existing data
    print("🗑️  Clearing existing PostgreSQL data...")
    with pg_conn.cursor() as cursor:
        cursor.execute("TRUNCATE TABLE positions")
    pg_conn.commit()

    # Transfer in batches
    print(f"📦 Transferring in batches of {batch_size:,}...")
    sqlite_cursor.execute("SELECT * FROM positions")

    batch = []
    transferred = 0

    with tqdm(total=total_positions, unit="pos") as pbar:
        for row in sqlite_cursor:
            # Convert SQLite row to tuple
            batch.append((
                int(row['state_hash']),  # Convert TEXT back to int
                row['state'],
                row['depth'],
                row['seeds_in_pits'],
                row['minimax_value'],
                row['best_move']
            ))

            if len(batch) >= batch_size:
                # Bulk insert
                with pg_conn.cursor() as cursor:
                    psycopg2.extras.execute_values(
                        cursor,
                        """
                        INSERT INTO positions
                        (state_hash, state, depth, seeds_in_pits, minimax_value, best_move)
                        VALUES %s
                        """,
                        batch,
                        page_size=1000
                    )
                pg_conn.commit()

                transferred += len(batch)
                pbar.update(len(batch))
                batch = []

    # Insert remaining
    if batch:
        with pg_conn.cursor() as cursor:
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO positions
                (state_hash, state, depth, seeds_in_pits, minimax_value, best_move)
                VALUES %s
                """,
                batch,
                page_size=1000
            )
        pg_conn.commit()
        transferred += len(batch)
        pbar.update(len(batch))

    # Verify
    print("✅ Verifying transfer...")
    with pg_conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM positions")
        pg_count = cursor.fetchone()[0]

    print(f"📊 SQLite:     {total_positions:,} positions")
    print(f"📊 PostgreSQL: {pg_count:,} positions")

    if pg_count == total_positions:
        print("✅ Transfer complete and verified!")
    else:
        print(f"⚠️  Warning: Counts don't match!")

    # Close connections
    sqlite_conn.close()
    pg_conn.close()


def main():
    parser = argparse.ArgumentParser(description="Transfer SQLite database to PostgreSQL")
    parser.add_argument("--sqlite-db", required=True, help="Path to SQLite database")
    parser.add_argument("--pg-host", default="localhost", help="PostgreSQL host")
    parser.add_argument("--pg-port", type=int, default=5432, help="PostgreSQL port")
    parser.add_argument("--pg-database", required=True, help="PostgreSQL database name")
    parser.add_argument("--pg-user", required=True, help="PostgreSQL user")
    parser.add_argument("--pg-password", required=True, help="PostgreSQL password")
    parser.add_argument("--batch-size", type=int, default=10000, help="Batch size for transfer")

    args = parser.parse_args()

    transfer(
        sqlite_path=args.sqlite_db,
        pg_host=args.pg_host,
        pg_port=args.pg_port,
        pg_database=args.pg_database,
        pg_user=args.pg_user,
        pg_password=args.pg_password,
        batch_size=args.batch_size
    )


if __name__ == "__main__":
    main()

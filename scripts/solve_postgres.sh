#!/bin/bash
#
# Quick wrapper for PostgreSQL-based solving
# Much faster than SQLite with multiple workers!
#
# Usage:
#   ./scripts/solve_postgres.sh 4 3 14    # Kalah(4,3) with 14 workers
#   ./scripts/solve_postgres.sh 6 3 14    # Kalah(6,3) with 14 workers
#

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <num_pits> <num_seeds> [workers] [database_name]"
    echo ""
    echo "Examples:"
    echo "  $0 4 3           # Kalah(4,3) with 1 worker"
    echo "  $0 4 3 14        # Kalah(4,3) with 14 workers (fast!)"
    echo "  $0 6 3 14        # Kalah(6,3) with 14 workers"
    echo "  $0 6 3 14 kalah_6_3  # Custom database name"
    exit 1
fi

PITS=$1
SEEDS=$2
WORKERS=${3:-1}
DB_NAME=${4:-mancala}

# Auto-detect PostgreSQL user
PG_USER=${USER}

echo "========================================="
echo "Mancala Solver - PostgreSQL Backend"
echo "========================================="
echo "Variant:   Kalah($PITS,$SEEDS)"
echo "Workers:   $WORKERS"
echo "Database:  $DB_NAME (PostgreSQL)"
echo "User:      $PG_USER"
echo "========================================="
echo ""

# Check if PostgreSQL is running
if ! pg_isready >/dev/null 2>&1; then
    echo "ERROR: PostgreSQL is not running!"
    echo ""
    echo "Start it with:"
    echo "  brew services start postgresql@15"
    exit 1
fi

# Check if database exists, create if needed
if ! psql -U "$PG_USER" -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    echo "Creating database '$DB_NAME'..."
    psql -U "$PG_USER" -d postgres -c "CREATE DATABASE $DB_NAME;" >/dev/null
    echo "✓ Database created"
    echo ""
fi

# Get script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Run the solver using PYTHONPATH
cd "$PROJECT_ROOT"
PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH" python3 -m mancala_solver.cli solve \
    --num-pits "$PITS" \
    --num-seeds "$SEEDS" \
    --backend postgresql \
    --pg-database "$DB_NAME" \
    --pg-user "$PG_USER" \
    --workers "$WORKERS"

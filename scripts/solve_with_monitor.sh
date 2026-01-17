#!/bin/bash
#
# Solve Kalah variant with automatic monitoring
#
# Usage:
#   ./scripts/solve_with_monitor.sh 6 3  # Solve Kalah(6,3)
#   ./scripts/solve_with_monitor.sh 6 4  # Solve Kalah(6,4)
#

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <num_pits> <num_seeds> [db_path]"
    echo ""
    echo "Examples:"
    echo "  $0 6 3                                    # Kalah(6,3) on external drive"
    echo "  $0 6 4 /Volumes/MancalaData/kalah_6_4.db  # Kalah(6,4) explicit path"
    exit 1
fi

NUM_PITS=$1
NUM_SEEDS=$2
DB_PATH=${3:-"data/databases/kalah_${NUM_PITS}_${NUM_SEEDS}.db"}

echo "🎮 Starting Kalah($NUM_PITS,$NUM_SEEDS) solve..."
echo "📁 Database: $DB_PATH"
echo ""

# Detect if on external drive (PostgreSQL) or local (SQLite)
if [[ "$DB_PATH" == /Volumes/* ]]; then
    echo "📦 Using PostgreSQL on external drive"
    BACKEND="postgresql"
    PG_DATABASE="mancala_${NUM_PITS}_${NUM_SEEDS}"

    # Ensure PostgreSQL is running
    if ! pg_ctl -D /Volumes/MancalaData/postgres_data status &>/dev/null; then
        echo "⚠️  PostgreSQL not running - starting it..."
        pg_ctl -D /Volumes/MancalaData/postgres_data start
        sleep 2
    fi

    # Create database if it doesn't exist
    createdb "$PG_DATABASE" 2>/dev/null || echo "  (database already exists)"

    SOLVE_CMD="python3 -u -m src.mancala_solver.cli.main solve \
        --num-pits $NUM_PITS \
        --num-seeds $NUM_SEEDS \
        --backend postgresql \
        --pg-database $PG_DATABASE \
        --workers 14"
else
    echo "📦 Using SQLite locally"
    BACKEND="sqlite"

    # Create directory if needed
    mkdir -p "$(dirname "$DB_PATH")"

    SOLVE_CMD="python3 -u -m src.mancala_solver.cli.main solve \
        --num-pits $NUM_PITS \
        --num-seeds $NUM_SEEDS \
        --backend sqlite \
        --db-path $DB_PATH \
        --workers 14"
fi

# Run solve in background with unbuffered output
LOG_FILE="/tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_$(date +%Y%m%d_%H%M%S).log"
echo "📝 Log file: $LOG_FILE"
echo ""

PYTHONUNBUFFERED=1 $SOLVE_CMD > "$LOG_FILE" 2>&1 &
SOLVE_PID=$!

echo "✅ Solve running in background (PID: $SOLVE_PID)"
echo ""
echo "To monitor progress:"
echo "  python3 scripts/monitor_solve.py $LOG_FILE $DB_PATH"
echo ""
echo "Or just run:"
echo "  ./scripts/monitor.sh"
echo ""
echo "To stop:"
echo "  kill $SOLVE_PID"
echo ""

# Wait a moment for log file to be created
sleep 2

# Auto-start monitor if available
if [ -t 0 ]; then
    echo "🔍 Starting monitor..."
    sleep 1
    python3 scripts/monitor_solve.py "$LOG_FILE" "$DB_PATH"
else
    echo "Run monitor manually: python3 scripts/monitor_solve.py $LOG_FILE $DB_PATH"
fi

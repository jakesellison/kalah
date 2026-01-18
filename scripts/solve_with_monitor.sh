#!/bin/bash
#
# Solve Kalah variant with automatic monitoring
#
# Usage:
#   ./scripts/solve_with_monitor.sh 6 3  # Solve Kalah(6,3) with TUI monitor
#   ./scripts/solve_with_monitor.sh 6 4 --no-monitor  # Log to console directly
#   ./scripts/solve_with_monitor.sh 6 4 --dual-output  # Both log file AND console
#

set -e

# Parse flags and positional arguments
NO_MONITOR=0
DUAL_OUTPUT=0
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-monitor)
            NO_MONITOR=1
            shift
            ;;
        --dual-output)
            DUAL_OUTPUT=1
            shift
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Restore positional parameters
set -- "${POSITIONAL_ARGS[@]}"

if [ $# -lt 2 ]; then
    echo "Usage: $0 [OPTIONS] <num_pits> <num_seeds> [db_path]"
    echo ""
    echo "Options:"
    echo "  --no-monitor     Run without TUI monitor (logs to console)"
    echo "  --dual-output    Output to both log file AND console"
    echo ""
    echo "Examples:"
    echo "  $0 6 3                                    # Kalah(6,3) with TUI monitor"
    echo "  $0 6 4 --no-monitor                       # Direct console output"
    echo "  $0 4 3 --dual-output                      # Both log file and console"
    exit 1
fi

NUM_PITS=$1
NUM_SEEDS=$2
DB_PATH=${3:-"data/databases/kalah_${NUM_PITS}_${NUM_SEEDS}.db"}

echo "🎮 Starting Kalah($NUM_PITS,$NUM_SEEDS) solve..."
echo "📁 Database: $DB_PATH"
echo ""
echo "📦 Using SQLite (fast mode - no WAL, no crash recovery)"

# Create directory if needed
mkdir -p "$(dirname "$DB_PATH")"

SOLVE_CMD="python3 -u -m src.mancala_solver.cli.main solve \
    --num-pits $NUM_PITS \
    --num-seeds $NUM_SEEDS \
    --db-path $DB_PATH \
    --workers 14 \
    --fast-mode"

# Handle different output modes
LOG_FILE="/tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_$(date +%Y%m%d_%H%M%S).log"

if [ $NO_MONITOR -eq 1 ]; then
    # Direct console output - no log file, no monitor
    echo "🎮 Running with direct console output (no monitor)"
    echo ""
    PYTHONUNBUFFERED=1 exec $SOLVE_CMD

elif [ $DUAL_OUTPUT -eq 1 ]; then
    # Dual output mode - both console and log file using tee
    echo "📝 Log file: $LOG_FILE"
    echo "📺 Dual output mode: console + log file"
    echo ""
    PYTHONUNBUFFERED=1 $SOLVE_CMD 2>&1 | tee "$LOG_FILE"

else
    # Default: background solve with TUI monitor
    echo "📝 Log file: $LOG_FILE"
    echo ""

    PYTHONUNBUFFERED=1 $SOLVE_CMD > "$LOG_FILE" 2>&1 &
    SOLVE_PID=$!

    echo "✅ Solve running in background (PID: $SOLVE_PID)"
    echo ""
    echo "To monitor progress manually:"
    echo "  python3 scripts/monitor_solve.py $LOG_FILE $DB_PATH"
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
fi

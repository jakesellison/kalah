#!/bin/bash
# Solve Kalah(6,4) overnight
# Custom rules: only capture if opposite pit has seeds

set -e

# Setup paths
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DB_PATH="data/databases/kalah_6_4_custom.db"
LOG_FILE="logs/kalah_6_4_${TIMESTAMP}.log"

# Create directories
mkdir -p data/databases
mkdir -p logs

# Remove old database if exists
if [ -f "$DB_PATH" ]; then
    echo "Removing existing database: $DB_PATH"
    rm "$DB_PATH"
fi

echo "================================================================"
echo "Kalah(6,4) Solver - Custom Rules (Capture only if opposite has seeds)"
echo "================================================================"
echo "Database: $DB_PATH"
echo "Log file: $LOG_FILE"
echo "Start time: $(date)"
echo "Workers: 14"
echo "Fast mode: ENABLED (no crash recovery, max speed)"
echo "================================================================"
echo ""

# Run solver
python3 -m src.mancala_solver.cli.main \
    --log-level INFO \
    solve \
    --num-pits 6 \
    --num-seeds 4 \
    --db-path "$DB_PATH" \
    --workers 14 \
    --fast-mode \
    2>&1 | tee "$LOG_FILE"

echo ""
echo "================================================================"
echo "SOLVE COMPLETE"
echo "================================================================"
echo "End time: $(date)"
echo "Database: $DB_PATH"
echo "Log file: $LOG_FILE"
echo ""

# Show final result
echo "Final result:"
python3 -m src.mancala_solver.cli.main \
    --log-level INFO \
    query \
    --num-pits 6 \
    --num-seeds 4 \
    --db-path "$DB_PATH" \
    2>&1 | grep -E "(Total positions|Starting position value|Best opening move)"

echo ""
echo "Done!"

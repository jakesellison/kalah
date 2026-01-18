#!/bin/bash
# Check progress of Kalah(6,4) solve

# Find the most recent log file
LOG_FILE=$(ls -t logs/kalah_6_4_*.log 2>/dev/null | head -1)

if [ -z "$LOG_FILE" ]; then
    echo "No log file found. Has the solve started?"
    exit 1
fi

echo "================================================================"
echo "Kalah(6,4) Solve Progress"
echo "================================================================"
echo "Log file: $LOG_FILE"
echo ""

# Show BFS progress
echo "BFS Progress (building game graph):"
echo "-----------------------------------"
tail -100 "$LOG_FILE" | grep -E "Depth [0-9]+:" | tail -5
echo ""

# Show Minimax progress
echo "Minimax Progress (computing values):"
echo "------------------------------------"
tail -100 "$LOG_FILE" | grep -E "Seeds-in-pits [0-9]+:" | tail -5
echo ""

# Show latest status
echo "Latest activity:"
echo "----------------"
tail -10 "$LOG_FILE"
echo ""

# Database size
DB_PATH="data/databases/kalah_6_4_custom.db"
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    echo "Database size: $DB_SIZE"
fi

echo ""
echo "To monitor in real-time: tail -f $LOG_FILE"

#!/bin/bash
# Quick launcher for the monitor - auto-detects latest solve

cd "$(dirname "$0")/.." || exit 1

# Find the most recent task output
TASK_OUTPUT=$(ls -t /private/tmp/claude/*/tasks/*.output 2>/dev/null | head -1)

# Find the most recent database
DB_FILE=$(ls -t data/databases/kalah_*.db 2>/dev/null | head -1)

if [ -z "$TASK_OUTPUT" ]; then
    echo "No task output found. Is a solve running?"
    exit 1
fi

echo "📊 Monitoring solve..."
echo "   Task: $TASK_OUTPUT"
echo "   DB:   $DB_FILE"
echo ""

python3 scripts/monitor_solve.py "$TASK_OUTPUT" "$DB_FILE"

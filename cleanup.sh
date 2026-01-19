#!/bin/bash
# Pre-run cleanup script

echo "=== Mancala Solver Cleanup ==="
echo ""

# 1. Kill any existing solver processes
echo "1. Killing existing solver processes..."
pkill -f "python.*mancala" 2>/dev/null && echo "   ✓ Killed existing processes" || echo "   ✓ No processes to kill"

# 2. Clean up old database files in root
echo ""
echo "2. Cleaning up old database files in root..."
rm -f *.db* *.log 2>/dev/null && echo "   ✓ Removed old DB files from root" || echo "   ✓ No old files to remove"

# 3. Clean up test databases
echo ""
echo "3. Cleaning up test databases..."
rm -f /tmp/kalah*.db* adaptive_test.* baseline_test.* 2>/dev/null && echo "   ✓ Cleaned test databases" || echo "   ✓ No test files"

# 4. Check system resources
echo ""
echo "4. Checking system resources..."
uptime
echo ""
df -h . | grep -v "Filesystem"
echo ""
vm_stat | head -5

# 5. Purge system caches (optional - requires sudo)
echo ""
echo "5. Purge system caches? (requires sudo, helps free memory)"
read -p "   Run 'sudo purge'? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo purge
    echo "   ✓ System caches purged"
else
    echo "   ✓ Skipped cache purge"
fi

echo ""
echo "=== Cleanup Complete ==="
echo ""
echo "Ready to start solver with:"
echo "  python3 -m src.mancala_solver.cli.main solve --num-pits 6 --num-seeds 4 --db-path databases/kalah_6_4.db --workers 14 2>&1 | tee databases/kalah_6_4.log"

#!/bin/bash
#
# Benchmark parallel vs chunked BFS solvers
#
# Usage: ./scripts/benchmark_bfs.sh 4 2
#

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <num_pits> <num_seeds>"
    echo "Example: $0 4 2"
    exit 1
fi

NUM_PITS=$1
NUM_SEEDS=$2

echo "🏁 Benchmarking Kalah($NUM_PITS,$NUM_SEEDS) solvers"
echo "=================================================="
echo ""

# Cleanup old databases
rm -f /tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_parallel.db
rm -f /tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_chunked.db

# Benchmark 1: Parallel BFS
echo "📊 Test 1: Parallel BFS (14 workers)"
echo "-----------------------------------"
time python3 -m src.mancala_solver.cli.main solve \
    --num-pits $NUM_PITS \
    --num-seeds $NUM_SEEDS \
    --db-path /tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_parallel.db \
    --workers 14 \
    --fast-mode \
    --solver parallel 2>&1 | grep -E "(BFS complete|Depth [0-9]+: Generated|SOLUTION)"

echo ""
echo ""

# Benchmark 2: Chunked BFS
echo "📊 Test 2: Chunked BFS (single-threaded)"
echo "----------------------------------------"
time python3 -m src.mancala_solver.cli.main solve \
    --num-pits $NUM_PITS \
    --num-seeds $NUM_SEEDS \
    --db-path /tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_chunked.db \
    --workers 14 \
    --fast-mode \
    --solver chunked 2>&1 | grep -E "(BFS complete|Depth [0-9]+: Generated|SOLUTION)"

echo ""
echo ""
echo "=================================================="
echo "✅ Benchmark complete!"
echo ""
echo "Check /tmp/kalah_${NUM_PITS}_${NUM_SEEDS}_*.db for results"

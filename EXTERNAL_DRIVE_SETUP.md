# External Drive Setup for Large Solves

## When Your Drive Arrives

### Step 1: Format the Drive

```bash
# List all disks
diskutil list

# Format as APFS (replace disk4 with your drive)
sudo diskutil eraseDisk APFS MancalaData disk4

# Verify mount
ls -la /Volumes/MancalaData
```

### Step 2: Install PostgreSQL on External Drive

PostgreSQL is faster than SQLite for large databases (better indexing, query optimization).

```bash
# Install PostgreSQL (if not already installed)
brew install postgresql@16

# Initialize database cluster on external drive
initdb -D /Volumes/MancalaData/postgres_data

# Create config to use external drive
cat > /Volumes/MancalaData/postgres_data/postgresql.conf.local << 'EOF'
# Memory settings for M3 Max
shared_buffers = 8GB
effective_cache_size = 24GB
work_mem = 256MB
maintenance_work_mem = 2GB

# Performance settings
random_page_cost = 1.1  # For SSD
effective_io_concurrency = 200
max_worker_processes = 14
max_parallel_workers = 14
max_parallel_workers_per_gather = 14
EOF

# Start PostgreSQL
pg_ctl -D /Volumes/MancalaData/postgres_data -l /Volumes/MancalaData/postgres.log start

# Create database
createdb mancala
```

### Step 3: Solve Kalah(6,3) First (Validation)

```bash
# This tests the approach on a smaller variant
python3 -m src.mancala_solver.cli.main solve \
  --num-pits 6 \
  --num-seeds 3 \
  --backend postgresql \
  --pg-database mancala \
  --db-path /Volumes/MancalaData/postgres_data \
  --workers 14

# Estimated time: 1-2 weeks
# Estimated size: 5-10 TB
```

Monitor progress:
```bash
./scripts/monitor.sh
```

### Step 4: Solve Kalah(6,4) (Main Goal)

If Kalah(6,3) succeeds and you have space:

```bash
# Create separate database for 6,4
createdb mancala_6_4

python3 -m src.mancala_solver.cli.main solve \
  --num-pits 6 \
  --num-seeds 4 \
  --backend postgresql \
  --pg-database mancala_6_4 \
  --workers 14

# Estimated time: 1-2 months
# Estimated size: 10-30 TB
```

## Alternative: Keep Using SQLite

SQLite works fine too, just slightly slower query performance:

```bash
# Solve directly to external drive
python3 -m src.mancala_solver.cli.main solve \
  --num-pits 6 \
  --num-seeds 3 \
  --backend sqlite \
  --db-path /Volumes/MancalaData/kalah_6_3.db \
  --workers 14
```

## Backup Strategy

Periodically backup to your Mac's internal SSD:

```bash
# Backup PostgreSQL
pg_dump mancala | gzip > ~/kalah_6_3_backup_$(date +%Y%m%d).sql.gz

# Or backup SQLite
cp /Volumes/MancalaData/kalah_6_3.db ~/kalah_6_3_backup_$(date +%Y%m%d).db
```

## Query Results

```bash
# Query solved database
python3 -m src.mancala_solver.cli.main query \
  --num-pits 6 \
  --num-seeds 3 \
  --backend postgresql \
  --pg-database mancala
```

## Cost Savings

**vs Cloud SQL:**
- 1× 20TB drive: $280
- 2× 20TB drives: $560 (if needed)

**vs Cloud SQL + Storage for same solve:**
- 10 TB: Save $5,500
- 30 TB: Save $15,340

## Performance

**Expected solve times:**
- Kalah(6,3): 1-2 weeks on M3 Max with 14 workers
- Kalah(6,4): 1-2 months on M3 Max with 14 workers

**Can run continuously:**
- Mac doesn't need to be active/unlocked
- Just disable sleep for display/disk
- Monitor via SSH if needed

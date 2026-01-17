# Cloud Architecture for Mancala Strong Solve

## Phase 1: Kalah(4,3) Prototype - LOCAL ONLY

### Specifications
- **Positions**: 4.6 million (from Irving's paper)
- **Storage**: 4.6M × 9 bytes = **41 MB** (fits in RAM!)
- **Strategy**: Build entirely on laptop, no cloud needed
- **Purpose**: Validate approach, test algorithms, verify against Irving's results

### Implementation
```python
# Simple SQLite database
positions.db (< 100 MB with indexes)

# All fits in RAM:
- Active frontier: ~100K positions max
- Full database: 41 MB
- Total RAM usage: < 500 MB
```

**Decision**: Start with pure local development for Kalah(4,3)

---

## Phase 2: Kalah(6,4) - GCP Cloud Infrastructure

### Storage Requirement Review
- **Positions**: ~1.3 trillion
- **Storage**: 11.7 TB
- **Challenge**: Massive write throughput + deduplication + eventual queries

### Cloud SQL: **NOT RECOMMENDED**

**Why Cloud SQL is wrong for this**:
- ❌ Optimized for OLTP (small transactions), not bulk inserts
- ❌ Expensive at scale ($200-500/month for needed specs)
- ❌ Limited to 64 TB (we might need more for indexes)
- ❌ Insert performance poor for billions of rows
- ❌ Not designed for massive batch workloads

### Recommended Architecture: **Hybrid Approach**

```
┌─────────────────────────────────────────┐
│   Local MacBook (M3 Max)                │
│   - BFS computation (14 cores)          │
│   - SQLite cache (10-50 GB)             │
│   - Batch staging area                  │
└────────────┬────────────────────────────┘
             │ Upload batches
             ▼
┌─────────────────────────────────────────┐
│   Cloud Storage (GCS)                   │
│   - Parquet/Avro batch files            │
│   - Partitioned by depth/seed count     │
│   - Compressed (Snappy/ZSTD)            │
│   - Cost: ~$23/TB/month                 │
└────────────┬────────────────────────────┘
             │ Load for processing
             ▼
┌─────────────────────────────────────────┐
│   Cloud Bigtable OR PostgreSQL on CE   │
│   - Deduplication layer                 │
│   - Minimax value storage               │
│   - Fast key-value lookups              │
└─────────────────────────────────────────┘
```

### Option A: Cloud Storage + Cloud Bigtable (RECOMMENDED)

**Phase 1 - BFS Generation**:
```python
For each depth level d:
    1. Load depth d-1 positions from local SQLite cache
    2. Generate all successor positions (parallel, 14 cores)
    3. Batch insert to local SQLite (deduplicate locally)
    4. When cache reaches 10-50 GB:
       - Export to Parquet file
       - Upload to GCS: gs://mancala-solve/depth-{d}/batch-{n}.parquet
       - Clear local cache
    5. After depth complete:
       - Deduplicate across batches (optional, can defer)
```

**Phase 2 - Minimax Analysis**:
```python
For each seed_count (descending from 24):
    1. Download positions from GCS
    2. Compute minimax values (streaming, never load all at once)
    3. Write results to Cloud Bigtable:
       - Key: state_hash (8 bytes)
       - Value: {depth, minimax_value, best_move}
    4. Query Bigtable for child positions to propagate values
```

**Cost Estimate**:
- Cloud Storage: 12 TB × $0.02/GB = $240/month (can delete after solving)
- Cloud Bigtable: 1 node = $0.65/hour = ~$470/month (provision during solve only)
- Egress: Minimal (local → GCS is free from same region)
- **Total**: ~$700-1000/month during active solving

**Pros**:
- ✅ Massive scalability (Bigtable handles billions of rows)
- ✅ Fast lookups for minimax phase
- ✅ Pay only while solving (shut down after)
- ✅ Cloud Storage is cheap archival

**Cons**:
- ❌ More complex setup
- ❌ Higher cost during active work

### Option B: PostgreSQL on Compute Engine (BUDGET OPTION)

**Setup**:
```
Compute Engine VM:
- Machine: n2-highmem-16 (16 vCPUs, 128 GB RAM)
- Disk: 15 TB SSD persistent disk
- PostgreSQL 15 with aggressive tuning
- Cost: ~$600-800/month
```

**PostgreSQL Tuning**:
```sql
-- Optimize for bulk inserts
shared_buffers = 32GB
work_mem = 512MB
maintenance_work_mem = 8GB
checkpoint_timeout = 30min
max_wal_size = 16GB
synchronous_commit = off  -- Acceptable for this use case
fsync = off  -- Only during bulk load, RE-ENABLE after

-- Partitioning
CREATE TABLE positions (
    state_hash BIGINT,
    state BYTEA,
    depth INT,
    seed_count INT,
    minimax_value INT,
    best_move SMALLINT
) PARTITION BY RANGE (depth);

-- Create partition for each depth
CREATE TABLE positions_d0 PARTITION OF positions FOR VALUES FROM (0) TO (1);
...
```

**Workflow**:
```python
# Local batch processing
batch_size = 1_000_000
for each depth:
    generate positions (local)
    insert in batches to PostgreSQL (COPY command)
    deduplicate within depth using ON CONFLICT
```

**Pros**:
- ✅ Simpler than Bigtable
- ✅ Full SQL queries available
- ✅ Single managed VM
- ✅ Can snapshot/backup easily

**Cons**:
- ❌ Need to manage PostgreSQL tuning
- ❌ Deduplication slower than Bigtable
- ❌ Scaling limited by single machine

### Option C: Hybrid Local + Cloud Storage Only (CHEAPEST)

**Strategy**: Never build giant database, just compute on the fly

```python
Phase 1 - BFS:
    For each depth:
        - Generate positions
        - Deduplicate locally
        - Save to Cloud Storage as Parquet
        - Discard from local memory

Phase 2 - Minimax:
    For each seed_count (descending):
        - Stream positions from Cloud Storage
        - Compute minimax using child lookups
        - Cache frequently accessed positions (LRU)
        - Write final results to new Parquet files

Final Result:
    - Small database (~100 GB) with just starting positions
      and their optimal values
    - Archive full tree to Cloud Storage
```

**Cost**:
- Cloud Storage: 12 TB × $0.02/GB = $240/month → $2880/year
- Compute: Just your laptop
- **Total**: ~$240/month, can delete most data after solve

**Pros**:
- ✅ Minimal cost
- ✅ No database management
- ✅ Works with laptop only

**Cons**:
- ❌ Slower minimax phase (must scan files)
- ❌ Can't query arbitrary positions easily
- ❌ More complex deduplication

---

## Recommended Approach

### For Kalah(4,3) Prototype:
**Local SQLite only** - no cloud needed (41 MB database)

### For Kalah(6,4) Production:
**Option A or B**, depending on budget:

- **If you want speed**: Option A (Bigtable) - ~$1000/month for 2-3 months
- **If you want simplicity**: Option B (PostgreSQL) - ~$700/month for 2-3 months
- **If you want minimal cost**: Option C (Cloud Storage only) - ~$240/month, slower

---

## Your Specific Questions Answered

### 1. "Is Cloud SQL the right choice?"
**No** - use Cloud Bigtable (for scale) or self-managed PostgreSQL on Compute Engine (for cost+simplicity)

### 2. "Cache entire layer locally?"
**Yes!** Perfect strategy:
```python
# Pseudocode
current_depth_cache = SQLiteDB(":memory:" or tempfile)  # 10-50 GB
for position in previous_depth:
    successors = generate_moves(position)
    current_depth_cache.insert_many(successors)  # Auto-deduplicates

    if current_depth_cache.size > 50_GB:
        # Flush to cloud
        export_to_parquet()
        upload_to_gcs(f"depth-{d}/batch-{n}.parquet")
        current_depth_cache.clear()
```

### 3. "Chunk and deduplicate in cloud?"
**Yes!** That's exactly Option A:
- Process in chunks locally (10-50 GB batches)
- Upload to Cloud Storage
- Deduplicate either:
  - Locally before upload (better), OR
  - In cloud database using UPSERT/ON CONFLICT (Bigtable/PostgreSQL)

---

## Next Steps

1. **Immediate**: Build Kalah(4,3) solver locally (no cloud needed)
2. **After validation**: Set up GCP project
3. **Prototype cloud pipeline**: Test batch upload to GCS with small dataset
4. **Choose**: Bigtable vs PostgreSQL based on budget/complexity preference
5. **Scale up**: Apply to Kalah(6,4)

---

## Sample GCP Setup Commands

```bash
# Create project
gcloud projects create mancala-solve

# Enable APIs
gcloud services enable bigtable.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable compute.googleapis.com

# Create Cloud Storage bucket
gsutil mb -l us-central1 gs://mancala-solve-data

# Create Bigtable instance (when ready)
gcloud bigtable instances create mancala-db \
    --cluster=mancala-cluster \
    --cluster-zone=us-central1-a \
    --cluster-num-nodes=1 \
    --display-name="Mancala Solver"
```

Would you like me to start implementing the Kalah(4,3) solver now?

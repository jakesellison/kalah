# Mancala Solver - Complete Project Design

## What We Haven't Considered Yet

### 1. Exact Kalah Rules Specification
### 2. Project Structure & Code Organization
### 3. Parallelization Architecture
### 4. Checkpointing & Resumability
### 5. Testing & Validation Strategy
### 6. Configuration Management
### 7. Logging & Monitoring
### 8. Error Handling
### 9. Performance Profiling

Let's address each:

---

## 1. Exact Kalah Rules Specification

We need to be crystal clear on the rules since different sources have variations.

### Standard Kalah(m,n) Rules - What We're Implementing

**Setup**:
- `m` pits per player (we'll use 4 or 6)
- `n` seeds per pit initially (we'll use 3 or 4)
- 1 store (mancala) per player
- Total seeds: `2 * m * n` (constant throughout game)

**Board Layout**:
```
        P2 Pits (counterclockwise: 12,11,10,9,8,7 for m=6)
     [12][11][10][9][8][7]
[13]                      [6]  <- Stores
     [0] [1] [2] [3][4][5]
        P1 Pits (clockwise: 0,1,2,3,4,5 for m=6)
```

**Turn Sequence**:
1. Player chooses a non-empty pit on their side
2. Pick up ALL seeds from that pit
3. Sow seeds counter-clockwise, one per pit
4. **Include own store** in sowing path
5. **Skip opponent's store** in sowing path
6. Special rules:
   - **Extra Turn**: If last seed lands in own store, take another turn
   - **Capture**: If last seed lands in own empty pit AND opposite pit has seeds:
     - Capture opponent's seeds from opposite pit
     - Capture the last seed placed
     - Place all captured seeds in own store

**Opposite Pits Mapping** (for captures):
```
For m=6:
P1 pit 0 ↔ P2 pit 12
P1 pit 1 ↔ P2 pit 11
P1 pit 2 ↔ P2 pit 10
P1 pit 3 ↔ P2 pit 9
P1 pit 4 ↔ P2 pit 8
P1 pit 5 ↔ P2 pit 7

Formula: opposite_of(pit_i) = (2*m + 1) - pit_i
```

**Game Ending**:
- Game ends when one player's side (all pits) is empty
- Remaining seeds on other side go to that player's store
- Winner: Player with most seeds in store
- **Tie**: Possible if both have equal seeds

**Minimax Value**:
- Value = (Player1 Store) - (Player2 Store) at end of game
- Positive = P1 wins
- Negative = P2 wins
- Zero = tie

### Edge Cases to Handle
1. Multiple extra turns in a row (keep going until no longer landing in store)
2. Capture on last available move
3. Empty all pits in one move (triggers end)
4. Long sowing sequences (seed count > board size, wraps around)

---

## 2. Project Structure

```
mancala/
├── README.md
├── PROJECT_DESIGN.md           # This file
├── MANCALA_SOLVING.md          # Research notes
├── GAME_STATE_DESIGN.md        # State representation
├── CLOUD_ARCHITECTURE.md       # Infrastructure
├── STORAGE_ABSTRACTION.md      # Backend interface
│
├── requirements.txt            # Python dependencies
├── setup.py                    # Package setup
├── .gitignore
├── pyproject.toml             # Modern Python config
│
├── src/
│   └── mancala_solver/
│       ├── __init__.py
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── game_state.py       # GameState class, bit-packing
│       │   ├── rules.py            # Move generation, validation
│       │   └── hash.py             # Zobrist hashing
│       │
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── base.py             # StorageBackend abstract class
│       │   ├── sqlite.py           # SQLite implementation
│       │   ├── postgresql.py       # PostgreSQL implementation
│       │   ├── bigtable.py         # Cloud Bigtable implementation
│       │   └── parquet.py          # Cloud Storage + Parquet
│       │
│       ├── solver/
│       │   ├── __init__.py
│       │   ├── bfs.py              # BFS game graph builder
│       │   ├── minimax.py          # Retrograde minimax analysis
│       │   └── parallel.py         # Parallel worker coordination
│       │
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── config.py           # Configuration management
│       │   ├── logging.py          # Logging setup
│       │   ├── checkpoint.py       # Save/resume functionality
│       │   └── stats.py            # Statistics tracking
│       │
│       └── cli/
│           ├── __init__.py
│           └── main.py             # Command-line interface
│
├── tests/
│   ├── __init__.py
│   ├── test_game_state.py
│   ├── test_rules.py
│   ├── test_storage.py
│   ├── test_bfs.py
│   ├── test_minimax.py
│   └── test_integration.py
│
├── configs/
│   ├── kalah_4_3.yaml            # Config for Kalah(4,3)
│   ├── kalah_6_4.yaml            # Config for Kalah(6,4)
│   └── local.yaml                # Local development settings
│
├── scripts/
│   ├── setup_gcp.sh              # GCP infrastructure setup
│   ├── benchmark.py              # Performance benchmarking
│   └── visualize.py              # Board state visualization
│
├── data/                          # .gitignore this
│   ├── checkpoints/              # Resume files
│   ├── databases/                # SQLite files
│   └── results/                  # Solved databases, statistics
│
└── notebooks/                     # Jupyter notebooks for exploration
    ├── state_analysis.ipynb
    └── performance_profiling.ipynb
```

---

## 3. Parallelization Architecture

### Design Choices

**Option A: Multiprocessing (RECOMMENDED)**
- Python's GIL makes threading useless for CPU work
- Use `multiprocessing.Pool` for BFS generation
- Share-nothing architecture: each worker generates independently

**Option B: Ray (for scaling to cluster)**
- If we want to use multiple machines
- Overkill for laptop, good for cloud VMs

### Parallel BFS Design

```python
from multiprocessing import Pool, Manager, Queue
import queue

class ParallelBFSSolver:
    def __init__(self, storage, num_workers=14):
        self.storage = storage
        self.num_workers = num_workers

    def build_game_graph(self):
        """
        Level-by-level parallel BFS
        """
        depth = 0

        while True:
            # Get all positions at current depth (main process)
            positions = list(self.storage.get_positions_at_depth(depth))
            if not positions:
                break

            print(f"Depth {depth}: {len(positions)} positions")

            # Divide work among workers
            chunk_size = len(positions) // self.num_workers + 1
            chunks = [positions[i:i+chunk_size]
                     for i in range(0, len(positions), chunk_size)]

            # Parallel generation
            with Pool(processes=self.num_workers) as pool:
                results = pool.map(self._generate_successors, chunks)

            # Merge and deduplicate (main process)
            all_successors = []
            for chunk_successors in results:
                all_successors.extend(chunk_successors)

            # Batch insert handles deduplication
            count = self.storage.insert_batch(all_successors)
            print(f"  Generated {len(all_successors)} -> {count} unique new positions")

            self.storage.flush()
            depth += 1

    def _generate_successors(self, positions):
        """
        Worker function: generate successors for a chunk of positions
        Runs in separate process, no shared state
        """
        successors = []
        for pos in positions:
            state = unpack_state(pos.state)
            for move in generate_legal_moves(state):
                next_state = apply_move(state, move)
                next_pos = Position(
                    state_hash=hash_state(next_state),
                    state=pack_state(next_state),
                    depth=pos.depth + 1,
                    seed_count=sum(next_state)
                )
                successors.append(next_pos)
        return successors
```

### Parallel Minimax Design

```python
class ParallelMinimaxSolver:
    def __init__(self, storage, num_workers=14):
        self.storage = storage
        self.num_workers = num_workers

    def compute_minimax(self, max_seeds):
        """
        Retrograde analysis: work backwards from endgame
        Within each seed_count, positions are independent (can parallelize)
        """
        for seed_count in range(0, max_seeds + 1):
            positions = list(self.storage.get_positions_by_seed_count(seed_count))
            print(f"Seed count {seed_count}: {len(positions)} positions")

            # Divide positions into chunks
            chunk_size = len(positions) // self.num_workers + 1
            chunks = [positions[i:i+chunk_size]
                     for i in range(0, len(positions), chunk_size)]

            # Parallel minimax computation
            with Pool(processes=self.num_workers) as pool:
                results = pool.map(self._compute_chunk_minimax, chunks)

            # Update database with results
            for chunk_results in results:
                for state_hash, value, best_move in chunk_results:
                    self.storage.update_solution(state_hash, value, best_move)

            self.storage.flush()

    def _compute_chunk_minimax(self, positions):
        """
        Worker: compute minimax for chunk of positions
        Needs read access to storage for child lookups
        """
        results = []
        for pos in positions:
            value, best_move = self._minimax_position(pos)
            results.append((pos.state_hash, value, best_move))
        return results

    def _minimax_position(self, pos):
        """
        Compute minimax value for a single position
        Assumes all child positions already have values
        """
        state = unpack_state(pos.state)

        # Terminal state?
        if is_terminal(state):
            return evaluate_terminal(state), None

        # Get all legal moves and their resulting values
        best_value = float('-inf') if is_player1_turn(state) else float('inf')
        best_move = None

        for move in generate_legal_moves(state):
            next_state = apply_move(state, move)
            next_hash = hash_state(next_state)

            # Lookup child value from storage
            child = self.storage.get(next_hash)
            child_value = child.minimax_value

            # Minimax logic
            if is_player1_turn(state):  # Maximizing
                if child_value > best_value:
                    best_value = child_value
                    best_move = move
            else:  # Minimizing
                if child_value < best_value:
                    best_value = child_value
                    best_move = move

        return best_value, best_move
```

### Synchronization Considerations

**BFS Phase**:
- ✅ Embarrassingly parallel within each depth
- ✅ No synchronization needed during generation
- ⚠️ Only synchronize at batch insert (handled by storage layer)

**Minimax Phase**:
- ✅ Embarrassingly parallel within each seed_count
- ⚠️ Workers need READ access to storage (for child lookups)
- ⚠️ Storage must be thread-safe for concurrent reads

---

## 4. Checkpointing & Resumability

**Why Needed**:
- Solve might take days/weeks
- Crashes happen (OOM, network, bugs)
- Want to pause and resume
- Cloud costs (shut down VM overnight)

**What to Checkpoint**:
1. Current depth (BFS) or seed_count (minimax)
2. Completed depths/seed_counts
3. Statistics (positions generated, time elapsed)
4. Configuration

**Checkpoint Strategy**:

```python
import json
from pathlib import Path
from datetime import datetime

class CheckpointManager:
    def __init__(self, checkpoint_dir: str = "data/checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, phase: str, **metadata):
        """
        Save checkpoint for resume capability
        """
        checkpoint = {
            "timestamp": datetime.utcnow().isoformat(),
            "phase": phase,  # "bfs" or "minimax"
            **metadata
        }

        filename = f"checkpoint_{phase}_{checkpoint['timestamp']}.json"
        path = self.checkpoint_dir / filename

        with open(path, 'w') as f:
            json.dump(checkpoint, f, indent=2)

        # Symlink to latest
        latest = self.checkpoint_dir / f"checkpoint_{phase}_latest.json"
        if latest.exists():
            latest.unlink()
        latest.symlink_to(filename)

        print(f"Checkpoint saved: {path}")

    def load_checkpoint(self, phase: str):
        """
        Load latest checkpoint for given phase
        """
        latest = self.checkpoint_dir / f"checkpoint_{phase}_latest.json"
        if not latest.exists():
            return None

        with open(latest) as f:
            return json.load(f)

# Usage in solver:
def build_game_graph(self):
    # Try to resume from checkpoint
    checkpoint = self.checkpoint_manager.load_checkpoint("bfs")
    start_depth = checkpoint["current_depth"] + 1 if checkpoint else 0

    for depth in range(start_depth, MAX_DEPTH):
        # ... generate positions ...

        # Save checkpoint every N depths
        if depth % 10 == 0:
            self.checkpoint_manager.save_checkpoint(
                "bfs",
                current_depth=depth,
                total_positions=self.storage.count_positions()
            )
```

**Storage is Our Main Checkpoint**:
- Database IS the checkpoint
- No need to save positions separately
- Just track: "what depth/seed_count are we on?"

---

## 5. Testing & Validation Strategy

### Unit Tests

```python
# tests/test_game_state.py
def test_bit_packing():
    """Ensure pack/unpack is lossless"""
    state = create_starting_state(num_pits=6, num_seeds=4)
    packed = pack_state(state)
    unpacked = unpack_state(packed)
    assert state == unpacked

def test_hash_consistency():
    """Same state should hash to same value"""
    state = create_starting_state(6, 4)
    h1 = hash_state(state)
    h2 = hash_state(state)
    assert h1 == h2

# tests/test_rules.py
def test_simple_move():
    """Basic move without capture or extra turn"""
    state = create_starting_state(4, 3)
    next_state = apply_move(state, move=0)
    # Assert expected distribution of seeds

def test_capture():
    """Test capture rule"""
    # Set up board state where capture is possible
    # Apply move, verify capture occurred

def test_extra_turn():
    """Test landing in own store gives extra turn"""
    # Set up state, apply move that lands in store
    # Verify player doesn't change

def test_game_end():
    """Test end-of-game logic"""
    # Create state where one side is empty
    # Verify remaining seeds go to other player's store

# tests/test_storage.py
def test_storage_insert_dedup():
    """Test deduplication works"""
    storage = SQLiteBackend(":memory:")
    pos = Position(...)
    assert storage.insert(pos) == True  # First insert
    assert storage.insert(pos) == False  # Duplicate

# tests/test_integration.py
def test_solve_tiny_game():
    """Solve trivial game, verify result"""
    # Solve Kalah(2,1) - tiny game
    # Verify outcome matches hand calculation
```

### Validation Against Known Results

```python
def test_kalah_4_3_starting_value():
    """
    Irving's result: Starting position should have specific value
    """
    solver = MancalaSolver(storage, num_pits=4, num_seeds=3)
    solver.solve()

    start_state = create_starting_state(4, 3)
    start_hash = hash_state(start_state)
    result = storage.get(start_hash)

    # Compare against Irving's published result
    assert result.minimax_value == IRVING_RESULT

def test_position_count_kalah_4_3():
    """
    Irving reported 4,604,996 positions for Kalah(4,3)
    """
    solver = MancalaSolver(storage, num_pits=4, num_seeds=3)
    solver.build_game_graph()

    total = storage.count_positions()
    assert total == 4_604_996  # Irving's count
```

### Invariants to Check

```python
def verify_invariants(state):
    """Check game invariants hold"""
    # Total seeds constant
    total_seeds = sum(state)
    assert total_seeds == INITIAL_SEEDS

    # No negative seeds
    assert all(s >= 0 for s in state)

    # Seeds are integers
    assert all(isinstance(s, int) for s in state)
```

---

## 6. Configuration Management

Use YAML for human-readable configs:

```yaml
# configs/kalah_4_3.yaml
game:
  variant: "kalah"
  num_pits: 4
  num_seeds: 3
  rules:
    capture: true
    extra_turn: true

storage:
  backend: "sqlite"
  path: "data/databases/kalah_4_3.db"

solver:
  num_workers: 14
  batch_size: 100000
  checkpoint_interval: 10  # Save every N depths

logging:
  level: "INFO"
  file: "data/logs/solve_4_3.log"
```

```python
# src/mancala_solver/utils/config.py
import yaml
from dataclasses import dataclass
from pathlib import Path

@dataclass
class GameConfig:
    variant: str
    num_pits: int
    num_seeds: int
    capture: bool
    extra_turn: bool

@dataclass
class StorageConfig:
    backend: str
    path: str

@dataclass
class SolverConfig:
    num_workers: int
    batch_size: int
    checkpoint_interval: int

@dataclass
class Config:
    game: GameConfig
    storage: StorageConfig
    solver: SolverConfig

    @classmethod
    def from_yaml(cls, path: str):
        with open(path) as f:
            data = yaml.safe_load(f)

        return cls(
            game=GameConfig(**data['game']['rules'],
                          variant=data['game']['variant'],
                          num_pits=data['game']['num_pits'],
                          num_seeds=data['game']['num_seeds']),
            storage=StorageConfig(**data['storage']),
            solver=SolverConfig(**data['solver'])
        )

# Usage:
config = Config.from_yaml("configs/kalah_4_3.yaml")
```

---

## 7. Logging & Monitoring

```python
# src/mancala_solver/utils/logging.py
import logging
from pathlib import Path

def setup_logging(level="INFO", log_file=None):
    """Configure logging for the solver"""

    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # File handler (optional)
    handlers = [console_handler]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, level),
        handlers=handlers
    )

# Usage in solver:
import logging
logger = logging.getLogger(__name__)

class BFSSolver:
    def build_game_graph(self):
        logger.info("Starting BFS game graph construction")

        for depth in range(MAX_DEPTH):
            positions = self.storage.get_positions_at_depth(depth)
            logger.info(f"Depth {depth}: {len(positions)} positions")

            # ...

            logger.debug(f"Generated {count} new positions")

        logger.info("BFS complete")
```

### Progress Monitoring

```python
from tqdm import tqdm  # Progress bars

def build_game_graph(self):
    depth = 0
    with tqdm(desc="BFS Progress", unit="depth") as pbar:
        while True:
            positions = list(self.storage.get_positions_at_depth(depth))
            if not positions:
                break

            pbar.set_description(f"Depth {depth} ({len(positions)} positions)")

            # ... process ...

            pbar.update(1)
            depth += 1
```

---

## 8. Error Handling

```python
class MancalaSolverError(Exception):
    """Base exception for solver errors"""
    pass

class InvalidMoveError(MancalaSolverError):
    """Raised when an invalid move is attempted"""
    pass

class StorageError(MancalaSolverError):
    """Raised when storage operation fails"""
    pass

class CheckpointError(MancalaSolverError):
    """Raised when checkpoint save/load fails"""
    pass

# In solver code:
def apply_move(state, move):
    if not is_legal_move(state, move):
        raise InvalidMoveError(f"Move {move} is illegal for state {state}")

    # ... apply move ...

# Graceful error handling in main loop:
def solve(self):
    try:
        self.build_game_graph()
        self.compute_minimax()
    except KeyboardInterrupt:
        logger.info("Interrupted by user, saving checkpoint...")
        self.checkpoint_manager.save_checkpoint(...)
        raise
    except Exception as e:
        logger.error(f"Solver failed: {e}", exc_info=True)
        self.checkpoint_manager.save_checkpoint(...)
        raise
```

---

## 9. Performance Profiling

```python
# scripts/benchmark.py
import cProfile
import pstats
from mancala_solver import MancalaSolver

def profile_bfs():
    profiler = cProfile.Profile()
    profiler.enable()

    solver = MancalaSolver(...)
    solver.build_game_graph()

    profiler.disable()

    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative')
    stats.print_stats(20)  # Top 20 functions

# Memory profiling
from memory_profiler import profile

@profile
def build_game_graph(self):
    # ... implementation ...
```

### Performance Metrics to Track

```python
# src/mancala_solver/utils/stats.py
from dataclasses import dataclass
from time import time

@dataclass
class SolverStats:
    positions_generated: int = 0
    positions_unique: int = 0
    duplicate_positions: int = 0
    depths_completed: int = 0
    start_time: float = None
    end_time: float = None

    def elapsed_time(self):
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    def positions_per_second(self):
        elapsed = self.elapsed_time()
        if elapsed:
            return self.positions_unique / elapsed
        return 0

    def duplication_rate(self):
        if self.positions_generated > 0:
            return self.duplicate_positions / self.positions_generated
        return 0

    def print_summary(self):
        print(f"""
Solver Statistics:
  Total positions generated: {self.positions_generated:,}
  Unique positions: {self.positions_unique:,}
  Duplicates: {self.duplicate_positions:,} ({self.duplication_rate():.1%})
  Depths completed: {self.depths_completed}
  Elapsed time: {self.elapsed_time():.2f}s
  Throughput: {self.positions_per_second():.0f} positions/sec
        """)
```

---

## 10. Dependencies

```
# requirements.txt
# Core
numpy>=1.24.0
pyyaml>=6.0

# Storage backends
# (SQLite is built-in to Python)
psycopg2-binary>=2.9.0  # PostgreSQL
google-cloud-bigtable>=2.0.0  # Cloud Bigtable (optional)
google-cloud-storage>=2.0.0   # Cloud Storage (optional)
pyarrow>=12.0.0  # Parquet support

# Testing
pytest>=7.0.0
pytest-cov>=4.0.0

# Development
tqdm>=4.65.0  # Progress bars
memory-profiler>=0.60.0  # Memory profiling
black>=23.0.0  # Code formatting
mypy>=1.0.0  # Type checking

# Optional: for cluster scaling
# ray[default]>=2.0.0
```

---

## Summary: Critical Design Decisions

Before we code, we need to decide:

### ✅ Already Decided
1. ✅ Storage abstraction layer (yes)
2. ✅ Start with Kalah(4,3) then scale to (6,4)
3. ✅ SQLite for local, PostgreSQL/Bigtable for cloud
4. ✅ Bit-packed state representation (9 bytes)

### 🤔 Need Your Input

1. **Python Version**: Python 3.10+? (need for type hints)

2. **Exact Kalah Rules**: The spec above is standard - confirm that's what you want?

3. **Extra Turn Handling**: Can a player have multiple extra turns in a row? (Yes in standard rules)

4. **Development Workflow**:
   - Use Git from the start?
   - Type hints (mypy)?
   - Auto-formatting (black)?

5. **Testing Level**:
   - Unit tests for everything?
   - Or just integration tests to verify final results?

6. **CLI vs Library**:
   - Just command-line tool?
   - Or also importable library for experimentation?

**My Recommendation**:
- Python 3.10+
- Standard Kalah rules (as specified above)
- Git + type hints + black (good practices)
- Comprehensive unit tests (catches bugs early)
- Both CLI and library (flexibility)

**Does this all make sense? Anything else to discuss before implementation?**

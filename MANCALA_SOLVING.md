# Strong Solving Mancala - Project Documentation

## System Specifications

### Hardware
- **Model**: MacBook Pro (Mac15,11)
- **Chip**: Apple M3 Max
- **CPU Cores**: 14 total (10 performance + 4 efficiency)
- **Memory**: 36 GB LPDDR5 (Micron)
- **Available Disk**: 399 GB

### Software
- **OS**: macOS 26.0.1 (25A362)
- **Kernel**: Darwin 25.0.0

### Implications for Mancala Solving
- **36 GB RAM**: Should be sufficient for large endgame databases (Rawlings used 32GB)
- **14 cores**: Excellent for parallel BFS workers
- **399 GB disk**: Plenty of space for database storage if needed

---

## Prior Work Review

### Geoffrey Irving, Jeroen Donkers, Jos Uiterwijk (2000)
**Paper**: "Solving Kalah" - Caltech

**Type of Solve**: **STRONG SOLVE** (complete game graph)

**Key Results**:
- Built complete game graphs for multiple Kalah variants
- Proved Kalah(6,4) is a win by 10 for first player with perfect play
- Proved Kalah(6,5) is a win by 12 for first player with perfect play
- Largest database built: Kalah(4,3) with **4,604,996 positions**

**Two-Phase Approach** (same as our plan!):
1. **Phase 1**: Build complete game graph starting from opening position
2. **Phase 2**: Compute game values of all positions backwards (retrograde analysis)

**Technical Details**:
- Algorithm: Iterative-deepening MTD(f) with zero-window alpha-beta search
- Major optimizations:
  - Move ordering
  - Transposition tables
  - Futility pruning
  - Enhanced transposition cut-offs
  - Endgame databases (24-piece database)

### Mark Rawlings (2015)
**Achievement**: Extended solving to Kalah(6,6)

**Key Results**:
- Proved Kalah(6,6) is a win by 2 for first player (surprising given larger margins in 4-seed and 5-seed variants)
- Fully quantified initial moves for Kalah(6,4), Kalah(6,5), and Kalah(6,6)

**Computational Scale**:
- Total search time: 106 days
- Nodes searched: 55+ trillion
- Endgame database size: 38,902,940,896 positions (all positions with ≤34 seeds)
- Database load time: 17 minutes into 32GB RAM

**Technical Approach**:
- Created massive endgame databases
- Used "empty capture" variation analysis
- Loaded entire endgame database into RAM for fast lookups

---

## Critical Findings: Strong Solve Status

**Has Kalah(6,4) been strongly solved?**

Based on research:
- Irving et al. (2000) used **alpha-beta search with transposition tables** to prove outcomes
- They built "complete game graphs" but only published database size for Kalah(4,3): 4.6M positions
- **No published complete database for Kalah(6,4)** found
- Rawlings (2015) built massive endgame databases (38.9 billion positions with ≤34 seeds)
- But endgame databases ≠ complete game graph from starting position

**Game Graph Size Estimates**:
- **Kalah(6,4) reachable positions**: ~1.3 trillion (10^12) - [Game Complexity Wikipedia](https://en.wikipedia.org/wiki/Game_complexity)
- **State-space complexity**: ~10^12
- **Game-tree complexity**: 6×10^18
- **Storage requirement**: 1.3 trillion × 9 bytes = **11.7 TB**

**Conclusion**: Irving PROVED the outcome via search, but likely didn't store the complete 1.3 trillion position database. **A true strong solve database for Kalah(6,4) may not exist publicly.**

---

## Our Solving Strategy

### Two-Phase Approach

#### Phase 1: Build Complete Game Tree (BFS)
- Use breadth-first search with parallel workers
- Build tree of all legal game states
- Divide work at each depth level across available cores
- Store states efficiently (design TBD)

#### Phase 2: Retrograde Minimax Analysis
- Start from endgame positions
- Step backward one seed-count at a time
- Evaluate minimax value for each position
- Advantage: Only evaluate reachable positions from our forward search

### Storage Considerations

**RAM vs Database Decision**:
- 36 GB RAM available
- Rawlings' 34-seed database: ~39 billion positions in 32GB
- Need to estimate our state space size
- May use hybrid: RAM for hot data, disk for complete storage

### Game State Representation
(To be designed - need to determine most efficient encoding)

---

## Implementation Plan

1. **System setup and storage design**
2. **Implement game state representation**
3. **Build parallel BFS engine**
4. **Develop storage layer (RAM/disk hybrid)**
5. **Implement retrograde minimax analysis**
6. **Verification and analysis**

---

## References

- [Solving Kalah - Geoffrey Irving (PDF)](https://naml.us/paper/irving2000_kalah.pdf)
- [Kalah research by Mark Rawlings - Mancala World](https://mancala.fandom.com/wiki/Kalah_research_by_Mark_Rawlings)
- [Kalah - Wikipedia](https://en.wikipedia.org/wiki/Kalah)
- [ResearchGate: Solving Kalah](https://www.researchgate.net/publication/2911672_Solving_Kalah)

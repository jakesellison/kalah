#!/usr/bin/env python3
"""
Real-time monitoring dashboard for Mancala solver.

Usage: python3 scripts/monitor_solve.py /path/to/output.log
"""

import sys
import time
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
    from rich.text import Text
except ImportError:
    print("Installing required package 'rich'...")
    subprocess.run([sys.executable, "-m", "pip", "install", "rich", "-q"])
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
    from rich.text import Text


class SolverMonitor:
    def __init__(self, log_file: str, db_path: str = None):
        self.log_file = Path(log_file)
        self.db_path = Path(db_path) if db_path else None
        self.console = Console()

        # Parsing state
        self.phase = "Unknown"
        self.current_depth = 0
        self.max_depth = 0
        self.total_positions = 0
        self.current_seeds_in_pits = 0
        self.max_seeds_in_pits = 0
        self.seeds_solved = 0
        self.start_time = None
        self.phase1_end_time = None

        # Intra-depth progress tracking
        self.depth_chunk_current = 0
        self.depth_chunk_total = 0
        self.depth_positions_generated = 0

        # History for graphs
        self.depth_history = []  # [(depth, positions, total)]
        self.seed_history = []   # [(seeds, positions, iterations)]

        # Resource tracking
        self.db_size = 0
        self.last_update = datetime.now()

        # Memory management state
        self.dedup_mode = "MEM"  # "MEM" or "DB"
        self.memory_warnings = 0
        self.memory_critical = 0
        self.adaptive_cache_mb = None
        self.last_memory_state = "Normal"  # "Normal", "Throttled", "Critical"

        # Database query caching (to avoid hitting DB every refresh)
        self._cached_max_depth = None
        self._last_depth_query = None

    def parse_log_line(self, line: str):
        """Parse a log line and update state."""
        # Phase detection
        if "PHASE 1" in line and ("Building game graph" in line or "Parallel BFS" in line):
            self.phase = "BFS"
            if not self.start_time:
                self.start_time = datetime.now()
        elif "PHASE 2" in line and ("Computing minimax" in line or "Parallel Minimax" in line):
            self.phase = "Minimax"
            if not self.phase1_end_time:
                self.phase1_end_time = datetime.now()
        elif "SOLUTION COMPLETE" in line or "VALIDATION PASSED" in line:
            self.phase = "Complete"

        # BFS progress - final depth completion
        match = re.search(r'Depth (\d+): Generated ([\d,]+) new positions.*total: ([\d,]+)', line)
        if match:
            self.current_depth = int(match.group(1))
            positions_at_depth = int(match.group(2).replace(',', ''))
            self.total_positions = int(match.group(3).replace(',', ''))
            self.max_depth = max(self.max_depth, self.current_depth)
            self.depth_history.append((self.current_depth, positions_at_depth, self.total_positions))
            self.last_update = datetime.now()
            # Reset intra-depth tracking
            self.depth_chunk_current = 0
            self.depth_chunk_total = 0
            self.depth_positions_generated = 0

        # BFS depth start (captures total positions to process)
        match = re.search(r'Depth (\d+): Processing ([\d,]+) positions in chunks', line)
        if match:
            self.current_depth = int(match.group(1))
            self.max_depth = max(self.max_depth, self.current_depth)
            # Reset intra-depth tracking for new depth
            self.depth_chunk_current = 0
            self.depth_chunk_total = 0
            self.depth_positions_generated = 0

        # BFS intra-depth progress (new!)
        match = re.search(r'Depth (\d+) progress: chunk (\d+)/(\d+) \(([\d.]+)%\) - ([\d,]+) new positions', line)
        if match:
            depth = int(match.group(1))
            if depth == self.current_depth:  # Only track current depth
                self.depth_chunk_current = int(match.group(2))
                self.depth_chunk_total = int(match.group(3))
                self.depth_positions_generated = int(match.group(5).replace(',', ''))
                self.last_update = datetime.now()

        # Minimax progress
        match = re.search(r'Max seeds in pits: (\d+)', line)
        if match:
            self.max_seeds_in_pits = int(match.group(1))

        match = re.search(r'Seeds-in-pits (\d+): solved ([\d,]+) positions in (\d+) iterations', line)
        if match:
            seeds = int(match.group(1))
            positions = int(match.group(2).replace(',', ''))
            iterations = int(match.group(3))
            self.current_seeds_in_pits = seeds
            self.seeds_solved = seeds + 1
            self.seed_history.append((seeds, positions, iterations))
            self.last_update = datetime.now()  # Track updates

        # Memory management events
        if "Using adaptive cache size:" in line:
            match = re.search(r'adaptive cache size: (\d+)MB', line)
            if match:
                self.adaptive_cache_mb = int(match.group(1))

        if "switching to DB-based dedup" in line or "DB-based dedup:" in line:
            self.dedup_mode = "DB"

        if "Critical memory pressure" in line or "CRITICAL memory pressure" in line:
            self.memory_critical += 1
            self.last_memory_state = "Critical"

        if "Memory pressure:" in line and "Critical" not in line:
            self.memory_warnings += 1
            self.last_memory_state = "Throttled"

        # Reset to normal if we see memory recovered
        if "Memory:" in line and "available" in line:
            # Check if pressure is back to normal
            match = re.search(r'(\d+\.\d+)GB available', line)
            if match:
                available_gb = float(match.group(1))
                if available_gb > 4.0:
                    self.last_memory_state = "Normal"

    def get_db_size(self) -> str:
        """Get database file size."""
        if self.db_path and self.db_path.exists():
            size_bytes = self.db_path.stat().st_size
            if size_bytes < 1024**2:
                return f"{size_bytes / 1024:.1f} KB"
            elif size_bytes < 1024**3:
                return f"{size_bytes / 1024**2:.1f} MB"
            else:
                return f"{size_bytes / 1024**3:.2f} GB"
        return "N/A"

    def get_db_max_depth(self) -> int:
        """Query database for maximum depth (cached, refreshes every 5 seconds)."""
        if not self.db_path or not self.db_path.exists():
            return None

        # Use cached value if recent (within 5 seconds)
        now = datetime.now()
        if self._last_depth_query and (now - self._last_depth_query).total_seconds() < 5:
            return self._cached_max_depth

        # Query database
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path), timeout=1.0)
            cursor = conn.execute("SELECT MAX(depth) FROM positions")
            result = cursor.fetchone()
            conn.close()
            self._cached_max_depth = result[0] if result and result[0] is not None else None
            self._last_depth_query = now
            return self._cached_max_depth
        except:
            return self._cached_max_depth  # Return last known value on error

    def get_disk_space(self) -> dict:
        """Get disk space info for database location."""
        try:
            import shutil
            if self.db_path and self.db_path.exists():
                stat = shutil.disk_usage(self.db_path.parent)
                total_gb = stat.total / (1024**3)
                used_gb = stat.used / (1024**3)
                free_gb = stat.free / (1024**3)
                percent_used = (stat.used / stat.total) * 100 if stat.total > 0 else 0
                return {
                    "total_gb": total_gb,
                    "used_gb": used_gb,
                    "free_gb": free_gb,
                    "percent_used": percent_used,
                }
        except:
            pass
        return {
            "total_gb": 0,
            "used_gb": 0,
            "free_gb": 0,
            "percent_used": 0,
        }

    def get_resource_usage(self) -> dict:
        """Get system resource usage."""
        try:
            # Process memory usage
            result = subprocess.run(
                ["ps", "-eo", "rss,comm"],
                capture_output=True,
                text=True
            )
            process_mem_kb = 0
            for line in result.stdout.split('\n'):
                if 'python' in line.lower():
                    mem_kb = int(line.strip().split()[0])
                    process_mem_kb += mem_kb

            process_mem_mb = process_mem_kb / 1024

            # Total system memory
            total_mem_result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True
            )
            total_mem_bytes = int(total_mem_result.stdout.strip())
            total_mem_gb = total_mem_bytes / (1024**3)

            # System memory pressure (active + wired)
            vm_stat = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True
            )

            pages_free = 0
            pages_active = 0
            pages_inactive = 0
            pages_wired = 0
            page_size = 4096  # Default page size

            for line in vm_stat.stdout.split('\n'):
                if 'page size of' in line:
                    page_size = int(line.split('of')[1].strip().split()[0])
                elif 'Pages free:' in line:
                    pages_free = int(line.split(':')[1].strip().rstrip('.'))
                elif 'Pages active:' in line:
                    pages_active = int(line.split(':')[1].strip().rstrip('.'))
                elif 'Pages inactive:' in line:
                    pages_inactive = int(line.split(':')[1].strip().rstrip('.'))
                elif 'Pages wired down:' in line:
                    pages_wired = int(line.split(':')[1].strip().rstrip('.'))

            # Calculate actual memory usage
            used_mem_bytes = (pages_active + pages_wired) * page_size
            # Available = Free + Inactive (inactive pages can be reclaimed)
            available_mem_bytes = (pages_free + pages_inactive) * page_size
            used_mem_gb = used_mem_bytes / (1024**3)
            free_mem_gb = available_mem_bytes / (1024**3)

            # Memory pressure percentage
            memory_pressure = (used_mem_bytes / total_mem_bytes) * 100 if total_mem_bytes > 0 else 0

            # CPU count
            cpu_count = subprocess.run(
                ["sysctl", "-n", "hw.ncpu"],
                capture_output=True,
                text=True
            ).stdout.strip()

            return {
                "process_mem_mb": process_mem_mb,
                "total_mem_gb": total_mem_gb,
                "used_mem_gb": used_mem_gb,
                "free_mem_gb": free_mem_gb,
                "memory_pressure": memory_pressure,
                "cpu_count": cpu_count,
            }
        except:
            return {
                "process_mem_mb": 0,
                "total_mem_gb": 0,
                "used_mem_gb": 0,
                "free_mem_gb": 0,
                "memory_pressure": 0,
                "cpu_count": "?"
            }

    def create_dashboard(self) -> Layout:
        """Create the dashboard layout."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=8)
        )

        # Header
        elapsed = ""
        if self.start_time:
            delta = datetime.now() - self.start_time
            elapsed = str(delta).split('.')[0]

        header_text = Text()
        header_text.append("🎮 Mancala Strong Solver Monitor", style="bold cyan")
        header_text.append(f"  |  Phase: ", style="white")
        if self.phase == "BFS":
            header_text.append(self.phase, style="bold yellow")
        elif self.phase == "Minimax":
            header_text.append(self.phase, style="bold green")
        elif self.phase == "Complete":
            header_text.append(self.phase, style="bold green")
        else:
            header_text.append(self.phase, style="white")
        header_text.append(f"  |  Elapsed: {elapsed}", style="white")

        # Add memory state indicator to header
        if self.last_memory_state != "Normal":
            header_text.append("  |  ", style="white")
            if self.last_memory_state == "Throttled":
                header_text.append("⚠ MEM THROTTLED", style="bold yellow")
            elif self.last_memory_state == "Critical":
                header_text.append("! MEM CRITICAL", style="bold red")

        layout["header"].update(Panel(header_text, border_style="cyan"))

        # Body - split into main stats and progress
        layout["body"].split_row(
            Layout(name="stats", ratio=1),
            Layout(name="progress", ratio=1)
        )

        # Stats table
        stats_table = Table(show_header=False, box=None, padding=(0, 2))
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="white")

        if self.phase == "BFS" or self.phase == "Minimax" or self.phase == "Complete":
            stats_table.add_row("Total Positions", f"{self.total_positions:,}")

            # Database and disk space
            db_size = self.get_db_size()
            stats_table.add_row("Database Size", db_size)

            disk = self.get_disk_space()
            if disk["total_gb"] > 0:
                disk_percent = disk["percent_used"]
                disk_color = "green" if disk_percent < 70 else "yellow" if disk_percent < 90 else "red"
                stats_table.add_row(
                    "Disk Space",
                    Text(
                        f"{disk['free_gb']:.1f}GB free / {disk['total_gb']:.1f}GB ({disk_percent:.0f}% used)",
                        style=disk_color
                    )
                )

        if self.phase == "BFS":
            stats_table.add_row("Current Depth", str(self.current_depth))
            # Query database for actual max depth if available
            db_max_depth = self.get_db_max_depth()
            if db_max_depth is not None and db_max_depth > self.max_depth:
                # BFS complete in DB, show progress
                pct = (self.current_depth / db_max_depth * 100) if db_max_depth > 0 else 0
                stats_table.add_row("Max Depth", f"{db_max_depth} ({pct:.1f}% complete)")
            else:
                # BFS still discovering, show max so far
                stats_table.add_row("Max Depth Seen", f"{self.max_depth} (discovering...)")
            if self.depth_history:
                last_depth = self.depth_history[-1]
                stats_table.add_row("Positions at Depth", f"{last_depth[1]:,}")

            # Intra-depth progress (new!)
            if self.depth_chunk_total > 0:
                chunk_pct = (self.depth_chunk_current / self.depth_chunk_total * 100) if self.depth_chunk_total > 0 else 0
                stats_table.add_row(
                    "Depth Progress",
                    f"{self.depth_chunk_current}/{self.depth_chunk_total} chunks ({chunk_pct:.1f}%)"
                )
                if self.depth_positions_generated > 0:
                    stats_table.add_row("Positions Generated", f"{self.depth_positions_generated:,}")

            # ETA calculation for BFS (rough estimate)
            if self.start_time and len(self.depth_history) > 5:
                elapsed = (datetime.now() - self.start_time).total_seconds()
                depths_per_sec = self.current_depth / elapsed if elapsed > 0 else 0
                # Estimate max depth as current + some buffer (BFS slows at end)
                estimated_max = max(self.max_depth, self.current_depth + 10)
                remaining_depths = estimated_max - self.current_depth
                if depths_per_sec > 0 and remaining_depths > 0:
                    eta_seconds = remaining_depths / depths_per_sec
                    eta = timedelta(seconds=int(eta_seconds))
                    stats_table.add_row("Est. Time Left", str(eta).split('.')[0])

        elif self.phase == "Minimax":
            stats_table.add_row("Seeds Processed", f"{self.seeds_solved} / {self.max_seeds_in_pits + 1}")
            pct = (self.seeds_solved / (self.max_seeds_in_pits + 1)) * 100 if self.max_seeds_in_pits > 0 else 0
            stats_table.add_row("Progress", f"{pct:.1f}%")
            if self.seed_history:
                last_seed = self.seed_history[-1]
                stats_table.add_row("Current Layer", f"{last_seed[1]:,} positions")
                stats_table.add_row("Iterations", str(last_seed[2]))

            # ETA calculation for Minimax
            if self.phase1_end_time and self.seeds_solved > 0 and self.max_seeds_in_pits > 0:
                elapsed = (datetime.now() - self.phase1_end_time).total_seconds()
                seeds_per_sec = self.seeds_solved / elapsed if elapsed > 0 else 0
                remaining_seeds = self.max_seeds_in_pits + 1 - self.seeds_solved
                if seeds_per_sec > 0 and remaining_seeds > 0:
                    eta_seconds = remaining_seeds / seeds_per_sec
                    eta = timedelta(seconds=int(eta_seconds))
                    stats_table.add_row("Est. Time Left", str(eta).split('.')[0])

        # Resources
        resources = self.get_resource_usage()
        stats_table.add_row("", "")  # Spacer

        # Process memory (all Python processes including workers)
        stats_table.add_row("Process Memory", f"{resources['process_mem_mb']:.0f} MB (all workers)")

        # System memory with pressure indicator
        if resources['total_mem_gb'] > 0:
            pressure = resources['memory_pressure']
            pressure_color = "green" if pressure < 60 else "yellow" if pressure < 80 else "red"
            stats_table.add_row(
                "System Memory",
                Text(
                    f"{resources['used_mem_gb']:.1f}GB / {resources['total_mem_gb']:.1f}GB ({pressure:.0f}%)",
                    style=pressure_color
                )
            )
            stats_table.add_row("Memory Headroom", f"{resources['free_mem_gb']:.1f} GB")

        stats_table.add_row("CPU Cores", resources['cpu_count'])

        # Memory management status
        stats_table.add_row("", "")  # Spacer

        # Memory state with color coding
        state_color = "green"
        state_icon = "✓"
        if self.last_memory_state == "Throttled":
            state_color = "yellow"
            state_icon = "⚠"
        elif self.last_memory_state == "Critical":
            state_color = "red"
            state_icon = "!"

        stats_table.add_row(
            "Memory State",
            Text(f"{state_icon} {self.last_memory_state}", style=state_color)
        )

        # Dedup mode (only show during BFS)
        if self.phase == "BFS":
            dedup_color = "green" if self.dedup_mode == "MEM" else "yellow"
            dedup_icon = "⚡" if self.dedup_mode == "MEM" else "💾"
            stats_table.add_row(
                "Dedup Mode",
                Text(f"{dedup_icon} {self.dedup_mode}", style=dedup_color)
            )

        # Adaptive cache size
        if self.adaptive_cache_mb:
            stats_table.add_row("SQLite Cache", f"{self.adaptive_cache_mb} MB")

        # Memory events
        if self.memory_warnings > 0 or self.memory_critical > 0:
            warning_text = ""
            if self.memory_warnings > 0:
                warning_text += f"{self.memory_warnings} warnings"
            if self.memory_critical > 0:
                if warning_text:
                    warning_text += ", "
                warning_text += f"{self.memory_critical} critical"

            event_color = "red" if self.memory_critical > 0 else "yellow"
            stats_table.add_row(
                "Memory Events",
                Text(warning_text, style=event_color)
            )

        layout["stats"].update(Panel(stats_table, title="📊 Statistics", border_style="blue"))

        # Progress visualization
        if self.phase == "BFS":
            # Show last 10 depths
            progress_table = Table(title="Recent Depth Progress", show_header=True)
            progress_table.add_column("Depth", style="cyan", justify="right")
            progress_table.add_column("Positions", style="yellow", justify="right")
            progress_table.add_column("Total", style="green", justify="right")

            for depth, positions, total in self.depth_history[-10:]:
                progress_table.add_row(
                    str(depth),
                    f"{positions:,}",
                    f"{total:,}"
                )

            layout["progress"].update(Panel(progress_table, border_style="yellow"))

        elif self.phase == "Minimax":
            # Show last 10 seed layers
            progress_table = Table(title="Recent Seed Layers", show_header=True)
            progress_table.add_column("Seeds", style="cyan", justify="right")
            progress_table.add_column("Positions", style="yellow", justify="right")
            progress_table.add_column("Iterations", style="magenta", justify="right")

            for seeds, positions, iterations in self.seed_history[-10:]:
                progress_table.add_row(
                    str(seeds),
                    f"{positions:,}",
                    str(iterations)
                )

            layout["progress"].update(Panel(progress_table, border_style="green"))
        else:
            layout["progress"].update(Panel("Waiting for data...", border_style="white"))

        # Footer - recent log lines
        footer_lines = []
        try:
            with open(self.log_file, 'r') as f:
                lines = f.readlines()
                # Get last 5 log lines (filter out progress bar noise)
                log_lines = [l.strip() for l in lines if 'INFO' in l or 'ERROR' in l]
                footer_lines = log_lines[-5:]
        except:
            footer_lines = ["Waiting for log file..."]

        footer_text = "\n".join(footer_lines[-5:])
        layout["footer"].update(Panel(footer_text, title="📝 Recent Logs", border_style="white"))

        return layout

    def monitor(self):
        """Main monitoring loop."""
        self.console.print("[bold green]Starting Mancala Solver Monitor...[/bold green]")
        self.console.print(f"Monitoring: {self.log_file}")
        if self.db_path:
            self.console.print(f"Database: {self.db_path}")
        self.console.print("\nPress Ctrl+C to exit\n")

        time.sleep(1)

        with Live(self.create_dashboard(), refresh_per_second=2, console=self.console) as live:
            file_pos = 0
            try:
                while True:
                    # Read new lines from log
                    try:
                        with open(self.log_file, 'r') as f:
                            f.seek(file_pos)
                            new_lines = f.readlines()
                            file_pos = f.tell()

                            for line in new_lines:
                                self.parse_log_line(line)
                    except FileNotFoundError:
                        pass

                    # Update display
                    live.update(self.create_dashboard())

                    # Check if complete
                    if self.phase == "Complete":
                        time.sleep(5)
                        break

                    time.sleep(0.5)

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Monitoring stopped by user[/yellow]")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 monitor_solve.py <log_file> [db_file]")
        print("\nExample:")
        print("  python3 scripts/monitor_solve.py /private/tmp/claude/.../bfd0cc2.output data/databases/kalah_4_3.db")
        sys.exit(1)

    log_file = sys.argv[1]
    db_file = sys.argv[2] if len(sys.argv) > 2 else None

    monitor = SolverMonitor(log_file, db_file)
    monitor.monitor()


if __name__ == "__main__":
    main()

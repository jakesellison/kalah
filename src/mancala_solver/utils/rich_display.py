"""
Rich-based TUI for solver progress display.

Provides clean, formatted output with:
- Progress bars
- Resource monitoring
- DB size tracking
- No duplicate/noisy log lines
"""

import logging
from typing import Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

console = Console()
logger = logging.getLogger(__name__)


class SolverDisplay:
    """
    Rich-based display for solver progress.

    Shows:
    - Current phase (BFS/Minimax)
    - Progress bar
    - Resource stats (disk, RAM, DB size)
    - Performance metrics
    """

    def __init__(self, resource_monitor=None):
        """
        Initialize solver display.

        Args:
            resource_monitor: Optional ResourceMonitor instance
        """
        self.resource_monitor = resource_monitor
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        self.current_task = None
        self.phase = "Initializing"
        self.stats = {}

    def log(self, message: str, style: str = ""):
        """Log a message using rich console."""
        console.print(message, style=style)

    def log_info(self, message: str):
        """Log info message."""
        console.print(f"[blue]ℹ[/blue] {message}")

    def log_success(self, message: str):
        """Log success message."""
        console.print(f"[green]✓[/green] {message}")

    def log_warning(self, message: str):
        """Log warning message."""
        console.print(f"[yellow]⚠[/yellow]  {message}")

    def log_error(self, message: str):
        """Log error message."""
        console.print(f"[red]✗[/red] {message}")

    def show_header(self, title: str, num_pits: int, num_seeds: int, workers: int):
        """Show solver header."""
        console.rule(f"[bold blue]{title}[/bold blue]")
        console.print(f"Problem: Kalah({num_pits},{num_seeds})")
        console.print(f"Workers: {workers}")
        console.print()

    def show_resource_table(self) -> Table:
        """Create resource status table."""
        if not self.resource_monitor:
            return None

        stats = self.resource_monitor.get_status_summary()

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")

        # Disk
        disk_color = "green" if stats["disk_safe"] else "red"
        table.add_row(
            "Disk Free",
            f"[{disk_color}]{stats['disk_free_gb']:.1f}GB[/{disk_color}] / {stats['disk_total_gb']:.1f}GB"
        )

        # RAM
        ram_color = "green" if stats["ram_safe"] else "red"
        table.add_row(
            "RAM Used",
            f"[{ram_color}]{stats['ram_percent']:.1f}%[/{ram_color}] ({stats['ram_used_gb']:.1f}GB)"
        )

        # DB sizes
        table.add_row(
            "DB Main",
            f"{stats['db_main_gb']:.2f}GB"
        )
        table.add_row(
            "DB WAL",
            f"{stats['db_wal_gb']:.2f}GB"
        )
        table.add_row(
            "DB Total",
            f"[bold]{stats['db_total_gb']:.2f}GB[/bold]"
        )

        return table

    def update_depth_info(self, depth: int, positions: int, mode: str = "single", chunks: int = 0, total_db: int = 0, prev_depth_positions: int = 0):
        """Update depth processing information."""
        mode_str = f"[yellow]Parallel ({chunks} chunks)[/yellow]" if mode == "parallel" else "[green]Single-thread[/green]"

        info_parts = [
            f"[bold]Depth {depth}[/bold]",
            f"{positions:,} positions",
            mode_str,
        ]

        # Show growth factor if we have previous depth
        if prev_depth_positions > 0 and positions > 0:
            growth = positions / prev_depth_positions
            info_parts.append(f"[dim]({growth:.1f}x growth)[/dim]")

        if total_db > 0:
            info_parts.append(f"Total: {total_db:,}")

        self.log_info(" | ".join(info_parts))

    def update_chunk_progress(self, completed: int, total: int, eta_seconds: float = None):
        """Update progress for parallel chunk processing."""
        percent = (completed / total * 100) if total > 0 else 0
        bar_width = 20
        filled = int(bar_width * completed / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)

        eta_str = ""
        if eta_seconds is not None and eta_seconds > 0:
            mins = int(eta_seconds / 60)
            secs = int(eta_seconds % 60)
            eta_str = f" [dim](ETA: {mins}m {secs}s)[/dim]"

        console.print(f"  [cyan]Chunks:[/cyan] {bar} {completed}/{total} ({percent:.0f}%){eta_str}", end="\r")

    def update_seed_info(self, seeds: int, positions: int, iterations: int):
        """Update minimax seed layer information."""
        self.log_info(
            f"[bold]Seeds-in-pits {seeds}[/bold] | "
            f"{positions:,} positions | "
            f"{iterations} iterations"
        )

    def show_resources_inline(self):
        """Show resource stats as a single inline message."""
        if not self.resource_monitor:
            return

        stats = self.resource_monitor.get_status_summary()

        disk_icon = "✓" if stats["disk_safe"] else "⚠"
        ram_icon = "✓" if stats["ram_safe"] else "⚠"

        console.print(
            f"[dim]{disk_icon} Disk: {stats['disk_free_gb']:.0f}GB free | "
            f"{ram_icon} RAM: {stats['ram_percent']:.0f}% | "
            f"DB: {stats['db_total_gb']:.1f}GB (Main: {stats['db_main_gb']:.1f}GB, WAL: {stats['db_wal_gb']:.1f}GB)[/dim]"
        )


def setup_rich_logging():
    """Configure logging to work nicely with rich console."""
    from rich.logging import RichHandler

    # Remove existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add rich handler
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
    )
    rich_handler.setFormatter(logging.Formatter("%(message)s"))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[rich_handler],
        format="%(message)s",
    )

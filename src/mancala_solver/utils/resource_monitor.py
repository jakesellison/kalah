"""
Resource monitoring utilities for safe long-running solver operations.

Monitors disk space and RAM usage to prevent system crashes.
Uses native Python/system calls (no psutil dependency).
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class ResourceMonitor:
    """
    Monitors system resources and aborts if limits are exceeded.

    Default safety limits:
    - Disk space: Stop if < 50GB free
    - RAM: Stop if > 90% used
    """

    def __init__(
        self,
        db_path: str,
        min_disk_gb: float = 50.0,
        max_ram_percent: float = 90.0,
    ):
        """
        Initialize resource monitor.

        Args:
            db_path: Path to database (used to check disk space on correct volume)
            min_disk_gb: Minimum free disk space in GB before aborting
            max_ram_percent: Maximum RAM usage percentage before aborting
        """
        self.db_path = Path(db_path)
        self.min_disk_bytes = min_disk_gb * 1024 * 1024 * 1024
        self.max_ram_percent = max_ram_percent

    def check_disk_space(self) -> Tuple[bool, float, float]:
        """
        Check if sufficient disk space is available.

        Returns:
            (is_safe, free_gb, total_gb)
        """
        stat = os.statvfs(self.db_path.parent if self.db_path.exists() else self.db_path.parent)
        free_bytes = stat.f_bavail * stat.f_frsize
        total_bytes = stat.f_blocks * stat.f_frsize

        free_gb = free_bytes / (1024 ** 3)
        total_gb = total_bytes / (1024 ** 3)
        is_safe = free_bytes > self.min_disk_bytes

        return is_safe, free_gb, total_gb

    def check_ram(self) -> Tuple[bool, float, float]:
        """
        Check if RAM usage is within safe limits (macOS).

        Returns:
            (is_safe, used_percent, used_gb)
        """
        try:
            # Use vm_stat on macOS to get memory info
            result = subprocess.run(['vm_stat'], capture_output=True, text=True)
            if result.returncode != 0:
                # Fallback: assume safe if we can't check
                return True, 0.0, 0.0

            lines = result.stdout.split('\n')
            page_size = 4096  # Default page size on macOS

            # Parse vm_stat output
            stats = {}
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    # Extract number from value (remove dots and text)
                    num = ''.join(c for c in value if c.isdigit())
                    if num:
                        stats[key.strip()] = int(num)

            # Calculate memory usage
            # Pages free + Pages inactive are considered available
            pages_free = stats.get('Pages free', 0)
            pages_active = stats.get('Pages active', 0)
            pages_inactive = stats.get('Pages inactive', 0)
            pages_speculative = stats.get('Pages speculative', 0)
            pages_wired = stats.get('Pages wired down', 0)

            total_pages = pages_free + pages_active + pages_inactive + pages_speculative + pages_wired
            used_pages = pages_active + pages_wired

            if total_pages == 0:
                return True, 0.0, 0.0

            used_percent = (used_pages / total_pages) * 100
            used_gb = (used_pages * page_size) / (1024 ** 3)
            is_safe = used_percent < self.max_ram_percent

            return is_safe, used_percent, used_gb
        except Exception as e:
            logger.warning(f"Could not check RAM usage: {e}")
            # Assume safe if we can't check
            return True, 0.0, 0.0

    def get_db_sizes(self) -> Tuple[float, float, float]:
        """
        Get database file sizes in GB.

        Returns:
            (main_db_gb, wal_gb, total_gb)
        """
        main_db = self.db_path
        wal_file = Path(str(self.db_path) + "-wal")

        main_size = main_db.stat().st_size if main_db.exists() else 0
        wal_size = wal_file.stat().st_size if wal_file.exists() else 0

        main_gb = main_size / (1024 ** 3)
        wal_gb = wal_size / (1024 ** 3)
        total_gb = main_gb + wal_gb

        return main_gb, wal_gb, total_gb

    def check_all(self) -> Tuple[bool, str]:
        """
        Check all resource limits.

        Returns:
            (is_safe, message)
        """
        # Check disk space
        disk_safe, free_gb, total_gb = self.check_disk_space()
        if not disk_safe:
            return False, f"ABORT: Low disk space! Only {free_gb:.1f}GB free (need {self.min_disk_bytes/(1024**3):.0f}GB minimum)"

        # Check RAM
        ram_safe, used_percent, used_gb = self.check_ram()
        if not ram_safe:
            return False, f"ABORT: High RAM usage! {used_percent:.1f}% used (limit: {self.max_ram_percent}%)"

        return True, "Resources OK"

    def get_status_summary(self) -> dict:
        """
        Get current resource status for display.

        Returns:
            Dictionary with resource stats
        """
        disk_safe, free_gb, total_gb = self.check_disk_space()
        ram_safe, ram_percent, ram_used_gb = self.check_ram()
        main_gb, wal_gb, db_total_gb = self.get_db_sizes()

        return {
            "disk_free_gb": free_gb,
            "disk_total_gb": total_gb,
            "disk_safe": disk_safe,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "ram_safe": ram_safe,
            "db_main_gb": main_gb,
            "db_wal_gb": wal_gb,
            "db_total_gb": db_total_gb,
        }


class ResourceCheckError(Exception):
    """Raised when resource limits are exceeded."""
    pass

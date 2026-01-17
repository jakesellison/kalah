"""
Memory monitoring and management utilities.

Provides cross-platform memory tracking and adaptive behavior for
memory-constrained solves (e.g., Kalah(6,3)).
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MemoryStats:
    """Memory usage statistics."""

    process_rss_mb: float  # Resident Set Size (actual RAM used by process)
    process_vms_mb: float  # Virtual Memory Size
    system_total_gb: float  # Total system RAM
    system_available_gb: float  # Available RAM for new allocations
    system_percent: float  # Percentage of RAM in use
    swap_used_gb: float  # Swap usage (if available)


def get_memory_stats() -> Optional[MemoryStats]:
    """
    Get current memory usage statistics.

    Returns:
        MemoryStats if successful, None if memory info unavailable
    """
    try:
        import psutil

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        sys_mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        return MemoryStats(
            process_rss_mb=mem_info.rss / (1024**2),
            process_vms_mb=mem_info.vms / (1024**2),
            system_total_gb=sys_mem.total / (1024**3),
            system_available_gb=sys_mem.available / (1024**3),
            system_percent=sys_mem.percent,
            swap_used_gb=swap.used / (1024**3),
        )
    except ImportError:
        # psutil not installed - try platform-specific fallbacks
        return _get_memory_stats_fallback()
    except Exception as e:
        logger.warning(f"Failed to get memory stats: {e}")
        return None


def _get_memory_stats_fallback() -> Optional[MemoryStats]:
    """Fallback memory stats using platform-specific commands."""
    try:
        if sys.platform == "darwin":
            return _get_memory_stats_macos()
        elif sys.platform.startswith("linux"):
            return _get_memory_stats_linux()
        else:
            return None
    except Exception as e:
        logger.warning(f"Fallback memory stats failed: {e}")
        return None


def _get_memory_stats_macos() -> Optional[MemoryStats]:
    """Get memory stats on macOS using system commands."""
    import subprocess

    # Get process RSS
    pid = os.getpid()
    ps_result = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True
    )
    process_rss_mb = int(ps_result.stdout.strip()) / 1024 if ps_result.stdout.strip() else 0

    # Get total memory
    total_result = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
    )
    total_bytes = int(total_result.stdout.strip())
    total_gb = total_bytes / (1024**3)

    # Get memory pressure via vm_stat
    vm_result = subprocess.run(["vm_stat"], capture_output=True, text=True)

    pages_free = 0
    pages_active = 0
    pages_inactive = 0
    pages_wired = 0
    page_size = 4096

    for line in vm_result.stdout.split("\n"):
        if "page size of" in line:
            page_size = int(line.split("of")[1].strip().split()[0])
        elif "Pages free:" in line:
            pages_free = int(line.split(":")[1].strip().rstrip("."))
        elif "Pages active:" in line:
            pages_active = int(line.split(":")[1].strip().rstrip("."))
        elif "Pages inactive:" in line:
            pages_inactive = int(line.split(":")[1].strip().rstrip("."))
        elif "Pages wired down:" in line:
            pages_wired = int(line.split(":")[1].strip().rstrip("."))

    used_bytes = (pages_active + pages_wired) * page_size
    # Available = Free + Inactive (inactive pages can be reclaimed)
    available_bytes = (pages_free + pages_inactive) * page_size
    available_gb = available_bytes / (1024**3)
    percent = (used_bytes / total_bytes) * 100 if total_bytes > 0 else 0

    return MemoryStats(
        process_rss_mb=process_rss_mb,
        process_vms_mb=0,  # Not easily available without psutil
        system_total_gb=total_gb,
        system_available_gb=available_gb,
        system_percent=percent,
        swap_used_gb=0,  # Not easily available
    )


def _get_memory_stats_linux() -> Optional[MemoryStats]:
    """Get memory stats on Linux using /proc."""
    # Get process stats
    pid = os.getpid()
    with open(f"/proc/{pid}/status") as f:
        proc_status = f.read()

    rss_kb = 0
    vms_kb = 0
    for line in proc_status.split("\n"):
        if line.startswith("VmRSS:"):
            rss_kb = int(line.split()[1])
        elif line.startswith("VmSize:"):
            vms_kb = int(line.split()[1])

    # Get system memory
    with open("/proc/meminfo") as f:
        meminfo = f.read()

    total_kb = 0
    available_kb = 0
    swap_total_kb = 0
    swap_free_kb = 0

    for line in meminfo.split("\n"):
        if line.startswith("MemTotal:"):
            total_kb = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            available_kb = int(line.split()[1])
        elif line.startswith("SwapTotal:"):
            swap_total_kb = int(line.split()[1])
        elif line.startswith("SwapFree:"):
            swap_free_kb = int(line.split()[1])

    total_gb = total_kb / (1024**2)
    available_gb = available_kb / (1024**2)
    swap_used_gb = (swap_total_kb - swap_free_kb) / (1024**2)
    percent = ((total_kb - available_kb) / total_kb) * 100 if total_kb > 0 else 0

    return MemoryStats(
        process_rss_mb=rss_kb / 1024,
        process_vms_mb=vms_kb / 1024,
        system_total_gb=total_gb,
        system_available_gb=available_gb,
        system_percent=percent,
        swap_used_gb=swap_used_gb,
    )


class MemoryMonitor:
    """
    Adaptive memory monitor with configurable thresholds.

    Usage:
        monitor = MemoryMonitor(warning_threshold_gb=4.0, critical_threshold_gb=2.0)

        # Periodically check
        if monitor.should_throttle():
            # Reduce workers, clear caches, etc.
            pass

        if monitor.is_critical():
            # Emergency: flush to disk, pause operations
            pass
    """

    def __init__(
        self,
        warning_threshold_gb: float = 4.0,
        critical_threshold_gb: float = 2.0,
        enable_logging: bool = True,
    ):
        """
        Initialize memory monitor.

        Args:
            warning_threshold_gb: Available RAM below this triggers warning state
            critical_threshold_gb: Available RAM below this triggers critical state
            enable_logging: Whether to log memory warnings
        """
        self.warning_threshold_gb = warning_threshold_gb
        self.critical_threshold_gb = critical_threshold_gb
        self.enable_logging = enable_logging
        self._last_warning = 0
        self._warning_interval = 60  # Log warnings at most once per 60 checks

    def get_stats(self) -> Optional[MemoryStats]:
        """Get current memory statistics."""
        return get_memory_stats()

    def should_throttle(self) -> bool:
        """
        Check if operations should be throttled due to memory pressure.

        Returns:
            True if available RAM is below warning threshold
        """
        stats = self.get_stats()
        if stats is None:
            return False

        if stats.system_available_gb < self.warning_threshold_gb:
            if self.enable_logging and self._last_warning % self._warning_interval == 0:
                logger.warning(
                    f"Memory pressure: {stats.system_available_gb:.1f}GB available "
                    f"(threshold: {self.warning_threshold_gb:.1f}GB)"
                )
            self._last_warning += 1
            return True

        return False

    def is_critical(self) -> bool:
        """
        Check if memory is critically low.

        Returns:
            True if available RAM is below critical threshold
        """
        stats = self.get_stats()
        if stats is None:
            return False

        if stats.system_available_gb < self.critical_threshold_gb:
            if self.enable_logging:
                logger.error(
                    f"CRITICAL memory pressure: {stats.system_available_gb:.1f}GB available "
                    f"(threshold: {self.critical_threshold_gb:.1f}GB)"
                )
            return True

        return False

    def get_adaptive_cache_size_mb(self, max_cache_mb: int = 256) -> int:
        """
        Calculate adaptive cache size based on available memory.

        Args:
            max_cache_mb: Maximum cache size in MB

        Returns:
            Recommended cache size in MB
        """
        stats = self.get_stats()
        if stats is None:
            return max_cache_mb

        available_mb = stats.system_available_gb * 1024

        # Use up to 5% of available RAM, capped at max_cache_mb
        adaptive_size = min(int(available_mb * 0.05), max_cache_mb)

        # Always keep at least 16MB
        return max(adaptive_size, 16)

    def log_status(self):
        """Log current memory status."""
        stats = self.get_stats()
        if stats is None:
            logger.info("Memory stats unavailable")
            return

        logger.info(
            f"Memory: Process={stats.process_rss_mb:.0f}MB, "
            f"System={stats.system_available_gb:.1f}GB available "
            f"({stats.system_percent:.0f}% used)"
        )


def install_psutil():
    """
    Attempt to install psutil for better memory monitoring.

    Returns:
        True if installed successfully, False otherwise
    """
    try:
        import subprocess

        print("Installing psutil for enhanced memory monitoring...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "psutil", "-q"],
            check=True,
        )
        print("✓ psutil installed successfully")
        return True
    except Exception as e:
        print(f"✗ Failed to install psutil: {e}")
        print("  Falling back to platform-specific memory monitoring")
        return False

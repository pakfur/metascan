"""
Startup profiler utility for measuring application startup time.

Provides a simple context manager and logging utilities to profile
startup phases and identify bottlenecks.
"""

import time
import logging
from contextlib import contextmanager
from typing import Optional

# Module-level startup time reference
_startup_time: Optional[float] = None
_logger: Optional[logging.Logger] = None


def init_startup_profiler() -> float:
    """
    Initialize the startup profiler. Call this at the very beginning of main().

    Returns:
        The initial startup timestamp.
    """
    global _startup_time, _logger
    _startup_time = time.perf_counter()

    # Configure logging to show startup timing
    _logger = logging.getLogger("startup_profiler")
    _logger.setLevel(logging.DEBUG)

    # Add console handler if not already present
    if not _logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter("[STARTUP %(elapsed).3fs] %(message)s")
        handler.setFormatter(_StartupFormatter())
        _logger.addHandler(handler)

    _logger.info("Startup profiling initialized")
    return _startup_time


class _StartupFormatter(logging.Formatter):
    """Custom formatter that includes elapsed time since startup."""

    def format(self, record: logging.LogRecord) -> str:
        elapsed = time.perf_counter() - (_startup_time or time.perf_counter())
        return f"[STARTUP {elapsed:7.3f}s] {record.getMessage()}"


def log_startup(message: str) -> None:
    """
    Log a startup message with timestamp.

    Args:
        message: The message to log.
    """
    global _logger, _startup_time

    if _logger is None or _startup_time is None:
        # Fallback if profiler not initialized
        print(f"[STARTUP] {message}")
        return

    _logger.info(message)


@contextmanager
def profile_phase(phase_name: str):
    """
    Context manager to profile a startup phase.

    Usage:
        with profile_phase("Loading config"):
            config = load_config()

    Args:
        phase_name: Name of the phase being profiled.
    """
    start = time.perf_counter()
    log_startup(f"BEGIN: {phase_name}")

    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        log_startup(f"END:   {phase_name} ({elapsed:.3f}s)")


def get_elapsed() -> float:
    """
    Get elapsed time since startup profiler was initialized.

    Returns:
        Elapsed time in seconds, or 0 if profiler not initialized.
    """
    if _startup_time is None:
        return 0.0
    return time.perf_counter() - _startup_time


def log_startup_complete() -> None:
    """Log that startup is complete with total time."""
    elapsed = get_elapsed()
    log_startup(f"=== STARTUP COMPLETE === Total time: {elapsed:.3f}s")

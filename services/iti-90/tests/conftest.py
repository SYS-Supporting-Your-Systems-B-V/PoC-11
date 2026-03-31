import logging
import os
import sys
from pathlib import Path

# Use a test-specific env profile before importing the app module.
os.environ.setdefault("MCSD_ENV_FILE", ".env.pytest")

# Ensure project root (containing main.py) is importable.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _cleanup_pytest_logs() -> None:
    log_dir = ROOT / ".pytest_tmp"
    if not log_dir.exists():
        return

    # On Windows, active FileHandlers keep files locked. Close matching handlers first.
    root_logger = logging.getLogger()
    for handler in list(getattr(root_logger, "handlers", []) or []):
        base_filename = getattr(handler, "baseFilename", None)
        if not isinstance(base_filename, str):
            continue
        try:
            base_path = Path(base_filename).resolve()
        except OSError:
            continue
        if base_path.parent != log_dir.resolve():
            continue
        if not (base_path.name.startswith("mcsd_") and base_path.suffix == ".log"):
            continue
        try:
            root_logger.removeHandler(handler)
            handler.close()
        except OSError:
            pass

    for path in log_dir.glob("mcsd_*.log"):
        try:
            path.unlink()
        except OSError:
            # Best effort cleanup; do not fail test runs for filesystem issues.
            pass


def pytest_sessionstart(session):
    _cleanup_pytest_logs()


def pytest_sessionfinish(session, exitstatus):
    """Clean up per-run file logs so .pytest_tmp does not keep growing."""
    _cleanup_pytest_logs()

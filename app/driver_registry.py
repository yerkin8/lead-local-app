"""Track Selenium processes so desktop shutdown can close their browsers."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

_lock = threading.Lock()
_drivers = set()


def _profile_in_use(folder: Path) -> bool:
    lock = folder / "lockfile"
    if not lock.exists():
        return False
    try:
        with lock.open("a"):
            return False
    except OSError:
        return True


def profile_argument() -> str | None:
    """One kept browser profile (DATA_DIR/browser-profile) for all parsing browsers, so a check
    passed once is remembered (K2: a fresh profile is a new visitor and gets a captcha each run).
    When another of our browsers holds it, this browser starts with a temporary profile."""
    from .config import DATA_DIR
    folder = Path(DATA_DIR) / "browser-profile"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if _profile_in_use(folder):
        logging.getLogger(__name__).info("Browser profile busy: starting with a temporary one")
        return None
    return f"--user-data-dir={folder}"


def register(driver):
    with _lock:
        _drivers.add(driver)
    return driver


def quit_driver(driver) -> None:
    with _lock:
        if driver not in _drivers:
            return
        _drivers.discard(driver)
    driver.quit()


def close_all() -> int:
    with _lock:
        drivers = list(_drivers)
    closed = 0
    for driver in drivers:
        try:
            quit_driver(driver)
            closed += 1
        except Exception:
            logging.getLogger(__name__).exception("Could not close Selenium browser during shutdown")
    return closed

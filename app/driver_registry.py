"""Track Selenium processes so desktop shutdown can close their browsers."""

from __future__ import annotations

import logging
import threading

_lock = threading.Lock()
_drivers = set()


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

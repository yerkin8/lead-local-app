"""One cancellation mechanism for long desktop jobs (from branch 183745, adapted).

A job runs with a token bound to its thread (``bind``). Parsers call ``check()``
between steps and inside pauses; it raises ``Cancelled`` once ``request(key)`` was
called. ``request`` before ``bind`` is remembered, so a job claimed at the same moment
the owner pressed «Остановить» stops right at its start. Outside a bound job
``check()`` does nothing (the server worker never binds one).
"""
from __future__ import annotations

import threading
import time

_local = threading.local()
_lock = threading.Lock()
_tokens: dict[str, threading.Event] = {}


class Cancelled(BaseException):
    """BaseException on purpose: the parsers' ``except Exception`` blocks must not swallow it."""

    def __init__(self, message: str = "Остановлено пользователем"):
        super().__init__(message)


def bind(key: str) -> threading.Event:
    with _lock:
        event = _tokens.setdefault(key, threading.Event())
    _local.key = key
    return event


def unbind() -> None:
    key = getattr(_local, "key", None)
    _local.key = None
    if key:
        with _lock:
            _tokens.pop(key, None)


def request(key: str) -> None:
    """Ask the job ``key`` to stop (now, or as soon as it starts)."""
    with _lock:
        event = _tokens.setdefault(key, threading.Event())
    event.set()


def forget(key: str) -> None:
    """Drop a stop request for a job that will never start (it was stopped in the queue)."""
    with _lock:
        event = _tokens.get(key)
        if event is not None and getattr(_local, "key", None) != key:
            _tokens.pop(key, None)


def requested(key: str | None = None) -> bool:
    key = key or getattr(_local, "key", None)
    with _lock:
        event = _tokens.get(key) if key else None
    return bool(event and event.is_set())


def check() -> None:
    if requested():
        raise Cancelled()


def sleep(seconds: float) -> None:
    """A pause that ends early when the job is cancelled."""
    end = time.monotonic() + max(0.0, seconds)
    while True:
        check()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.2, left))


def wait(future, step: float = 0.2):
    """``future.result()`` that stays responsive to «Остановить»."""
    from concurrent.futures import TimeoutError as FutureTimeout
    while True:
        check()
        try:
            return future.result(timeout=step)
        except FutureTimeout:
            continue


def request_all() -> list[str]:
    with _lock:
        keys = list(_tokens)
        for event in _tokens.values():
            event.set()
    return keys

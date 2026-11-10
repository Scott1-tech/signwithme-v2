"""Failure throttling (login, share-link tokens).

An in-process fixed-window counter. That is enough for a single-instance
deployment, which is what this app is sized for; it is deliberately not a
distributed limiter, and the docstring says so rather than implying protection
it cannot give across replicas.

`KeyedThrottle` is the general form: `limit` failures of one key inside a
window of `window_seconds` (counted from the key's first failure) lock that key
until the window ends. Both numbers are read at call time, so a setting can be
changed (or patched in a test) without rebuilding the throttle.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.config import settings


@dataclass
class _Window:
    count: int
    expires_at: float


class KeyedThrottle:
    def __init__(self, limit: Callable[[], int], window_seconds: Callable[[], int]):
        self._limit = limit
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}

    def _prune(self, now: float) -> None:
        expired = [key for key, window in self._windows.items() if window.expires_at <= now]
        for key in expired:
            del self._windows[key]

    def is_locked(self, key: str) -> int:
        """Return the seconds remaining on a lockout, or 0 when not locked."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            window = self._windows.get(key)
            if window and window.count >= self._limit():
                return max(int(window.expires_at - now), 1)
        return 0

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            window = self._windows.get(key)
            if window is None:
                self._windows[key] = _Window(count=1, expires_at=now + self._window_seconds())
            else:
                window.count += 1

    def reset(self, key: str) -> None:
        with self._lock:
            self._windows.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


class LoginThrottle(KeyedThrottle):
    def __init__(self):
        super().__init__(lambda: settings.login_max_attempts, lambda: settings.login_lockout_seconds)


login_throttle = LoginThrottle()
# Failures against one e-mail from ANY address. Looser than the per-address
# limit (an outsider can trip it on purpose, locking the real owner out for
# the window), but without it a caller who rotates addresses is never slowed.
login_email_throttle = KeyedThrottle(
    lambda: settings.login_email_max_attempts, lambda: settings.login_lockout_seconds
)
# Share-link token failures per source address (EDITOR-V2-PLAN §8).
share_throttle = KeyedThrottle(lambda: settings.share_fail_limit, lambda: settings.share_fail_window_seconds)

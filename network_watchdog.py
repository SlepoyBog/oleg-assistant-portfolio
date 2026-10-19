import time
from collections import deque


class NetworkWatchdog:
    """Detects a burst of polling network errors without owning restart logic."""

    def __init__(self, limit=8, window_seconds=300, clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._errors = deque()

    def record_error(self):
        now = self.clock()
        cutoff = now - self.window_seconds
        while self._errors and self._errors[0] < cutoff:
            self._errors.popleft()
        self._errors.append(now)
        return len(self._errors) >= self.limit

    def reset(self):
        self._errors.clear()


class PendingUpdateWatchdog:
    """Triggers when Telegram reports an unchanged non-empty queue repeatedly."""

    def __init__(self, limit=2):
        self.limit = limit
        self._consecutive_pending = 0

    def observe(self, pending_count):
        if pending_count > 0:
            self._consecutive_pending += 1
        else:
            self._consecutive_pending = 0
        return self._consecutive_pending >= self.limit


class ConnectivityWatchdog:
    """Triggers after consecutive health-probe failures."""

    def __init__(self, limit=3):
        self.limit = limit
        self._consecutive_failures = 0

    def failed(self):
        self._consecutive_failures += 1
        return self._consecutive_failures >= self.limit

    def succeeded(self):
        self._consecutive_failures = 0

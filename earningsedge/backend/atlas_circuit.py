"""Atlas circuit breaker — short-circuit MongoDB calls when Atlas is down.

Atlas free tier intermittently blocks SSL handshakes from cloud egress
IPs (Cloud Run, Heroku). Every doomed MongoDB call costs ~5 seconds
of dyno time waiting for the handshake to fail. During live audio,
many requests fire in parallel (persona pulse, pattern matches,
watchlist refresh) and the backlog of pending Atlas-blocked requests
eats all dyno memory until Heroku SIGTERMs it.

The fix: after N consecutive SSL failures, mark Atlas as down for
COOLDOWN_S seconds. Callers check ``is_open()`` before attempting
Mongo; when the circuit is open, they short-circuit to in-memory
fallbacks instead of paying the 5-second wait.
"""
from __future__ import annotations

import threading
import time

# Trip after ONE failure. On Heroku the Atlas SSL is consistently broken
# and waiting for two failures means every caller pays the 5s SSL timeout
# wait before the circuit opens — N callers × 5s = serious dyno hit.
TRIP_THRESHOLD = 1
# How long to stay open before retrying. Atlas SSL failures from Heroku
# don't resolve themselves on a minute timescale, so we extend the
# cooldown to 10 minutes (was 60s) to massively reduce retry frequency.
COOLDOWN_S = 600.0


class _CircuitState:
    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return False
            if time.monotonic() - self.opened_at > COOLDOWN_S:
                # Cooldown expired; allow one probe.
                self.opened_at = None
                self.consecutive_failures = 0
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.consecutive_failures += 1
            if self.consecutive_failures >= TRIP_THRESHOLD:
                self.opened_at = time.monotonic()


_circuit = _CircuitState()


def is_open() -> bool:
    """Return True when Atlas should NOT be called (circuit is open)."""
    return _circuit.is_open()


def record_success() -> None:
    _circuit.record_success()


def record_failure() -> None:
    _circuit.record_failure()

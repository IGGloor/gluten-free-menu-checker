"""In-memory limits on /analyze: a per-IP rate limit and a daily cap for the whole app.

Each menu check calls Jina Reader and Gemini, which have their own quotas, so these limits stop one
visitor (or a burst of visitors) from using them up. The counts live in this process's memory, so they
reset when the app restarts. On Vercel several instances can run at once, each with its own counts, so
there the limits are approximate rather than exact.
"""

import math
import time
from collections import deque
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Callable


class SlidingWindowLimiter:
    """Allows at most `limit` hits per key within any `window` seconds. A limit of 0 turns it off."""

    # Once this many keys are tracked, forget the ones with no recent hits.
    _SWEEP_AT = 10_000

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Records a hit and returns None, or returns the seconds to wait if `key` is over the limit."""
        if self.limit <= 0:
            return None
        now = self.clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window - now
        hits.append(now)
        if len(self._hits) > self._SWEEP_AT:
            self._sweep(now)
        return None

    def _sweep(self, now: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DailyCap:
    """Allows at most `limit` uses per UTC day, across all visitors. A limit of 0 turns it off."""

    def __init__(self, limit: int, now: Callable[[], datetime] = _utc_now):
        self.limit = limit
        self.now = now
        self._day: date | None = None
        self._used = 0

    def take(self) -> bool:
        """Uses one of today's slots. Returns False when there are none left."""
        if self.limit <= 0:
            return True
        today = self.now().date()
        if today != self._day:
            self._day, self._used = today, 0
        if self._used >= self.limit:
            return False
        self._used += 1
        return True

    def seconds_until_reset(self) -> int:
        now = self.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), dt_time.min, tzinfo=timezone.utc)
        return math.ceil((midnight - now).total_seconds())

    def reset(self) -> None:
        self._day, self._used = None, 0

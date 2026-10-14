"""Small in-memory sliding-window rate limiter.

Sufficient for a single-node home deployment. For multi-instance setups put a
reverse proxy with rate limiting (Caddy/Traefik) in front.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.max_requests:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory under abuse
                for k in [k for k, v in self._hits.items() if not v][:5_000]:
                    del self._hits[k]
            return True

    def blocked(self, key: str) -> bool:
        """True if ``key`` reached the limit (does not record a hit)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return False
            while q and q[0] <= now - self.window:
                q.popleft()
            return len(q) >= self.max_requests

    def hit(self, key: str) -> None:
        """Record one event (e.g. a failed login) without checking the limit."""
        with self._lock:
            self._hits[key].append(time.monotonic())

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            return max(1, int(self.window - (time.monotonic() - q[0])) + 1)

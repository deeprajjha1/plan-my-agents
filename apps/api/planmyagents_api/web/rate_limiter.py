"""In-process token-bucket rate limiter for the public demand APIs.

Sprint 6 / C — keeps a curious scraper from hammering ``/demand/*`` on
every page load while we run a single-replica dev deployment. Designed
to be:

* **Single-replica accurate.** Production-grade multi-replica would
  need Redis or a sticky session. We deliberately defer that until
  there are real demand-signal customers; an in-process limiter is
  enough to stop one accidental fork-bomb-loop scrape.
* **Free under normal use.** The homepage and the public ``/demand``
  page each fetch the two endpoints once per render; with the 60-rpm
  default a single client can refresh every second forever and never
  trip the limiter.
* **Honest when tripped.** Returns 429 with a ``Retry-After`` header
  (rounded up to whole seconds) so the caller can self-throttle.
* **Bounded memory under IP rotation (2026-05-19).** The per-key
  bucket dict no longer grows unbounded. Two complementary mechanisms
  keep it small:

    1. *Lazy eviction*: every ``_SWEEP_INTERVAL`` acquires we walk
       the dict, refill every bucket against the current clock, and
       drop any whose tokens have refilled all the way back to
       ``capacity``. Such a bucket is semantically indistinguishable
       from a never-seen key — the next ``acquire(key)`` would
       re-create it with the same starting state — so dropping it
       is a pure no-op for behaviour and a win for memory.
    2. *Hard cap*: ``max_buckets`` (default 100k) bounds the dict
       even in adversarial cases where the lazy sweep can't keep up
       (e.g., every key arrives right before its bucket fully
       refills). When the cap is breached we evict the buckets
       whose ``last_refill_at`` is oldest first — these are the
       keys most likely to be cleanly dropped, since the longest
       since-seen is the most likely to be fully refilled by now.

  This pattern is intentionally dependency-free: no LRU library, no
  Redis, no background thread. The cost is one extra dict walk per
  ``_SWEEP_INTERVAL`` acquires — at 60 rpm per key that's once every
  ~17 minutes per key, well under the budget of a public endpoint.

The bucket key is the source IP. We do NOT hash or rotate it — these
endpoints are public-by-design and the IP is already on the wire; an
attacker is going to be more bandwidth-limited than identity-limited.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_DEFAULT_MAX_BUCKETS = 100_000
"""Soft cap on the per-key bucket dict.

100k unique IPs is roughly the upper end of what we'd see in a day
of legitimate public-API use (the public-demand endpoints are
homepage-only). Adversarial IP rotation would have to deliberately
churn more than this to start tripping the hard-cap eviction. We
could tune lower at the cost of dropping legitimate buckets earlier
under bursty load, but the lazy-sweep covers normal-traffic memory
without needing a tight cap.
"""

_SWEEP_INTERVAL = 1024
"""How many ``acquire`` calls between full-dict sweeps.

Tuned for amortised O(1) cost per acquire: a sweep touches every
bucket once, and we run it every 1024 acquires, so each bucket pays
~1/1024 of one extra dict access per acquire. With the 60-rpm
default the sweep fires roughly once every 17 minutes per key under
homepage-shaped traffic.
"""


class RateLimitExceeded(RuntimeError):
    """Raised by :meth:`TokenBucketRateLimiter.acquire` when the bucket
    has no tokens left. ``retry_after_seconds`` is the float seconds
    until at least one token becomes available."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))
        super().__init__(
            f"rate limit exceeded; retry after {self.retry_after_seconds:.2f}s"
        )


@dataclass
class _Bucket:
    tokens: float
    last_refill_at: float


class TokenBucketRateLimiter:
    """Per-key (default per-IP) token bucket.

    The bucket holds ``capacity`` tokens and refills at
    ``refill_rate_per_second`` tokens/second. Each ``acquire(key)``
    consumes one token. ``acquire`` raises :class:`RateLimitExceeded`
    when the bucket is empty.

    Two design choices worth noting:

    * ``capacity == burst``. Bursting up to the full bucket is
      allowed, so a page that does two parallel fetches will never
      trip the limiter on a healthy refill rate.
    * ``last_refill_at`` is a monotonic wall-clock; we use
      :func:`time.monotonic` so DST / NTP jumps don't grant or
      revoke tokens incorrectly.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_rate_per_second: float,
        clock: Callable[[], float] | None = None,
        max_buckets: int = _DEFAULT_MAX_BUCKETS,
        sweep_interval: int = _SWEEP_INTERVAL,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_rate_per_second <= 0:
            raise ValueError("refill_rate_per_second must be > 0")
        if max_buckets <= 0:
            raise ValueError("max_buckets must be > 0")
        if sweep_interval <= 0:
            raise ValueError("sweep_interval must be > 0")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate_per_second)
        self.max_buckets = int(max_buckets)
        self.sweep_interval = int(sweep_interval)
        self._clock = clock or time.monotonic
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._acquires_since_sweep = 0

    def acquire(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, last_refill_at=now)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.last_refill_at)
            bucket.tokens = min(
                self.capacity, bucket.tokens + elapsed * self.refill_rate
            )
            bucket.last_refill_at = now
            if bucket.tokens < 1.0:
                missing = 1.0 - bucket.tokens
                retry_after = missing / self.refill_rate
                raise RateLimitExceeded(retry_after_seconds=retry_after)
            bucket.tokens -= 1.0

            self._acquires_since_sweep += 1
            if self._acquires_since_sweep >= self.sweep_interval:
                self._sweep_locked(now)
            # Hard cap: in adversarial cases (every key arrives right
            # before refilling), the sweep alone may not free enough
            # entries. Trim oldest-last-refilled buckets until we're
            # back under the soft cap. Done after the sweep so we
            # don't redundantly evict buckets the sweep would catch.
            if len(self._buckets) > self.max_buckets:
                self._trim_oldest_locked()

    def reset(self, key: str | None = None) -> None:
        """Drop all bucket state (or one key's state). Useful in tests."""
        with self._lock:
            if key is None:
                self._buckets.clear()
                self._acquires_since_sweep = 0
            else:
                self._buckets.pop(key, None)

    def bucket_count(self) -> int:
        """Return the current number of tracked buckets (test/observability hook)."""

        with self._lock:
            return len(self._buckets)

    def _sweep_locked(self, now: float) -> None:
        """Drop any bucket that has fully refilled (== never-seen-key).

        Caller must hold ``self._lock``. Refills every bucket against
        ``now`` first, so a key that's been idle long enough to
        replenish is evicted in the same pass. Resets the sweep
        counter on exit.
        """

        for key in list(self._buckets.keys()):
            bucket = self._buckets[key]
            elapsed = max(0.0, now - bucket.last_refill_at)
            bucket.tokens = min(
                self.capacity, bucket.tokens + elapsed * self.refill_rate
            )
            bucket.last_refill_at = now
            if bucket.tokens >= self.capacity:
                # Fully refilled — indistinguishable from never-seen.
                # Dropping is a pure memory win, zero behaviour change.
                del self._buckets[key]
        self._acquires_since_sweep = 0

    def _trim_oldest_locked(self) -> None:
        """Evict oldest-last-refilled buckets until we're under the cap.

        Caller must hold ``self._lock``. This is the defense-in-depth
        path: it only runs when the lazy sweep couldn't free enough
        entries. Oldest-last-refilled is the right eviction order
        because those buckets have either been seen-recently-but-
        haven't-refilled (unusual) or are stale enough that we'd
        evict them in the next sweep anyway.
        """

        excess = len(self._buckets) - self.max_buckets
        if excess <= 0:
            return
        # Partial sort: pick the `excess` oldest entries by
        # last_refill_at. heapq.nsmallest is O(n log k); we expect
        # excess ≪ len(self._buckets) in the common case.
        from heapq import nsmallest

        oldest = nsmallest(
            excess,
            self._buckets.items(),
            key=lambda item: item[1].last_refill_at,
        )
        for key, _ in oldest:
            self._buckets.pop(key, None)


def public_demand_rate_limiter() -> TokenBucketRateLimiter:
    """Construct the limiter for the public demand APIs from env vars.

    * ``PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN`` (default 60)
      controls both capacity (burst) and refill rate (per-minute).
      Setting it to ``0`` disables the limiter — useful in tests that
      drive many sequential fetches.
    * ``PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS`` (default 100000)
      bounds in-process memory under IP rotation. Operators
      shouldn't need to touch this unless they're seeing genuine
      sustained traffic from > 100k distinct IPs/day on the public
      demand endpoints.
    """

    raw = os.getenv("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN", "60")
    try:
        per_minute = int(raw)
    except ValueError:
        per_minute = 60
    raw_max = os.getenv("PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS", "")
    try:
        max_buckets = int(raw_max) if raw_max else _DEFAULT_MAX_BUCKETS
    except ValueError:
        max_buckets = _DEFAULT_MAX_BUCKETS
    if max_buckets <= 0:
        # Mirror the per_minute<=0 guard below: a non-positive value would
        # crash TokenBucketRateLimiter.__init__ with `max_buckets must be > 0`,
        # taking down every request after startup. Treat as "operator typoed
        # the env var" and fall back to the documented default instead of
        # hard-failing the whole API.
        max_buckets = _DEFAULT_MAX_BUCKETS
    if per_minute <= 0:
        # Effectively unlimited: refill at 1e6/s, capacity 1e6.
        return TokenBucketRateLimiter(
            capacity=1_000_000,
            refill_rate_per_second=1_000_000.0,
            max_buckets=max_buckets,
        )
    return TokenBucketRateLimiter(
        capacity=per_minute,
        refill_rate_per_second=per_minute / 60.0,
        max_buckets=max_buckets,
    )

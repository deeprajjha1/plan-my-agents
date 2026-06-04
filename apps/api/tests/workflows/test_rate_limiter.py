"""Unit tests for the public-demand token-bucket rate limiter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.web.rate_limiter import (  # noqa: E402
    RateLimitExceeded,
    TokenBucketRateLimiter,
    public_demand_rate_limiter,
)


class _FakeClock:
    """Hand-driven clock so tests don't depend on wall time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class TokenBucketTests(unittest.TestCase):
    def test_first_n_calls_within_capacity_pass(self) -> None:
        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=3, refill_rate_per_second=1.0, clock=clock
        )
        for _ in range(3):
            limiter.acquire("client-a")

    def test_capacity_plus_one_raises(self) -> None:
        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=2, refill_rate_per_second=1.0, clock=clock
        )
        limiter.acquire("k")
        limiter.acquire("k")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.acquire("k")
        self.assertGreater(ctx.exception.retry_after_seconds, 0.0)

    def test_refill_restores_tokens_over_time(self) -> None:
        clock = _FakeClock()
        # 1 token / second
        limiter = TokenBucketRateLimiter(
            capacity=2, refill_rate_per_second=1.0, clock=clock
        )
        limiter.acquire("k")
        limiter.acquire("k")
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire("k")
        clock.advance(1.0)
        limiter.acquire("k")
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire("k")

    def test_separate_keys_have_separate_buckets(self) -> None:
        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=1, refill_rate_per_second=1.0, clock=clock
        )
        limiter.acquire("alice")
        limiter.acquire("bob")  # different key — own bucket
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire("alice")
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire("bob")

    def test_retry_after_decreases_as_clock_advances(self) -> None:
        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=1, refill_rate_per_second=1.0, clock=clock
        )
        limiter.acquire("k")
        with self.assertRaises(RateLimitExceeded) as first:
            limiter.acquire("k")
        first_wait = first.exception.retry_after_seconds
        clock.advance(0.5)
        with self.assertRaises(RateLimitExceeded) as second:
            limiter.acquire("k")
        self.assertLess(second.exception.retry_after_seconds, first_wait)

    def test_zero_or_negative_capacity_refuses_construction(self) -> None:
        with self.assertRaises(ValueError):
            TokenBucketRateLimiter(capacity=0, refill_rate_per_second=1.0)
        with self.assertRaises(ValueError):
            TokenBucketRateLimiter(capacity=1, refill_rate_per_second=0.0)


class PublicDemandLimiterFactoryTests(unittest.TestCase):
    def test_default_capacity_is_60_per_minute(self) -> None:
        limiter = public_demand_rate_limiter()
        self.assertEqual(limiter.capacity, 60.0)
        self.assertAlmostEqual(limiter.refill_rate, 1.0)

    def test_zero_per_min_disables_limiter(self) -> None:
        import os

        old = os.environ.get("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN")
        os.environ["PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN"] = "0"
        try:
            limiter = public_demand_rate_limiter()
            # Capacity is effectively unbounded.
            for _ in range(10_000):
                limiter.acquire("k")
        finally:
            if old is None:
                os.environ.pop("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN", None)
            else:
                os.environ["PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN"] = old


class TokenBucketEvictionTests(unittest.TestCase):
    """Pin the 2026-05-19 fix for unbounded per-IP bucket growth.

    Before this fix the ``_buckets`` dict grew without bound — every
    unique key added an entry that was never freed. An attacker
    rotating IPs (cheap on a botnet) could drive the process toward
    OOM at ~100 bytes per bucket. These tests assert that:

    * a fully-refilled bucket is evicted in the periodic sweep
      (zero behaviour change for the key — its next acquire is
      identical to first-seen), and
    * the hard ``max_buckets`` cap kicks in when the sweep can't
      keep up, bounding memory under sustained rotation.
    """

    def test_sweep_drops_fully_refilled_buckets(self) -> None:
        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=4,
            refill_rate_per_second=4.0,
            clock=clock,
            sweep_interval=8,
        )
        for key in ("a", "b", "c", "d"):
            limiter.acquire(key)
        self.assertEqual(limiter.bucket_count(), 4)

        # Wait long enough that every bucket has refilled past full
        # capacity. Drive eight DISTINCT keys to fire the sweep
        # without exhausting any of them: each key gets one token
        # of its own capacity-4 bucket, then the sweep runs and
        # finds 'a'/'b'/'c'/'d' fully replenished and evicts them.
        # The eight trigger keys all have tokens=3 (one short of
        # capacity) so the sweep keeps them.
        clock.advance(10.0)
        for i in range(8):
            limiter.acquire(f"trigger-{i}")
        # Only the trigger keys survived. The four original keys
        # had idled long enough to refill past capacity → evicted.
        for original in ("a", "b", "c", "d"):
            self.assertNotIn(original, limiter._buckets)
        for i in range(8):
            self.assertIn(f"trigger-{i}", limiter._buckets)

    def test_evicted_key_behaves_as_first_seen_on_return(self) -> None:
        """Eviction must not silently grant a partially-used bucket extra tokens."""

        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=2,
            refill_rate_per_second=1.0,
            clock=clock,
            sweep_interval=4,
        )
        limiter.acquire("k")
        limiter.acquire("k")
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire("k")
        # Wait long enough for the bucket to fully refill.
        clock.advance(10.0)
        # Drive four DISTINCT keys to fire the sweep without
        # exhausting any of them (sweep_interval=4, capacity=2).
        for i in range(4):
            limiter.acquire(f"other-{i}")
        self.assertNotIn("k", limiter._buckets)
        # First post-eviction acquire on 'k' starts a fresh bucket
        # at full capacity — same as first-seen behaviour.
        limiter.acquire("k")
        limiter.acquire("k")
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire("k")

    def test_hard_cap_bounds_dict_under_aggressive_rotation(self) -> None:
        """Even when no bucket has refilled past capacity, max_buckets caps memory."""

        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=2,
            refill_rate_per_second=0.001,  # extremely slow refill
            clock=clock,
            max_buckets=50,
            sweep_interval=10,
        )
        # 1_000 distinct keys — far above the 50-bucket cap. The
        # refill rate is too slow for any bucket to fully replenish
        # within the test, so the sweep alone can't free them.
        # The hard cap should kick in.
        for i in range(1_000):
            limiter.acquire(f"client-{i}")
        self.assertLessEqual(limiter.bucket_count(), 50)

    def test_one_hundred_thousand_keys_stays_bounded(self) -> None:
        """End-to-end memory stress: rotate 100k IPs, dict stays under cap."""

        clock = _FakeClock()
        limiter = TokenBucketRateLimiter(
            capacity=2,
            refill_rate_per_second=0.001,
            clock=clock,
            max_buckets=1_000,
            sweep_interval=1024,
        )
        for i in range(100_000):
            limiter.acquire(f"ip-{i}")
        self.assertLessEqual(limiter.bucket_count(), 1_000)

    def test_factory_honours_public_demand_max_buckets_env(self) -> None:
        import os

        saved_max = os.environ.get("PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS")
        saved_rate = os.environ.get("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN")
        os.environ["PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS"] = "7"
        os.environ.pop("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN", None)
        try:
            limiter = public_demand_rate_limiter()
            self.assertEqual(limiter.max_buckets, 7)
        finally:
            os.environ.pop("PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS", None)
            if saved_max is not None:
                os.environ["PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS"] = saved_max
            if saved_rate is not None:
                os.environ["PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN"] = saved_rate

    def test_factory_recovers_from_non_positive_max_buckets_env(self) -> None:
        # Pre-fix (2026-05-20 sweep): an operator typoing "0" or any
        # negative value into PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS
        # would parse cleanly but crash TokenBucketRateLimiter.__init__
        # with `max_buckets must be > 0`, taking down the entire FastAPI
        # app at first request creation. Mirror the per_minute<=0 guard
        # already below it.
        import os

        saved_max = os.environ.get("PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS")
        saved_rate = os.environ.get("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN")
        try:
            for bad_value in ("0", "-1", "-100"):
                with self.subTest(bad_value=bad_value):
                    os.environ["PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS"] = bad_value
                    os.environ.pop("PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN", None)
                    limiter = public_demand_rate_limiter()
                    # Falls back to the documented default rather than crashing.
                    self.assertGreater(limiter.max_buckets, 0)
        finally:
            os.environ.pop("PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS", None)
            if saved_max is not None:
                os.environ["PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS"] = saved_max
            if saved_rate is not None:
                os.environ["PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN"] = saved_rate


if __name__ == "__main__":
    unittest.main()

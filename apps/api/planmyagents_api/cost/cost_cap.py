"""Per-goal and per-day spend caps for paid provider execution.

Why this exists
---------------
Sprint 3a wires real provider keys (Stripe, Hunter, OpenAI, etc).
Without an enforced spend cap, a single buggy plan can drain
the daily budget. This module is the safety gate that has to
be in place before we promote any paid wrapper to production
routing.

Two limits are enforced:

* **per-goal**: maximum projected spend across all sub-tasks of
  one goal. Tracked in-memory by a :class:`CostCapPolicy` instance
  (one per goal request).
* **per-day**: maximum total spend across every paid call from
  every goal. Tracked durably in the spend ledger; the policy
  reads the day's running total at construction time and tracks
  in-memory deltas for the goal it's attached to.

Both limits are enforced **before** a call by projecting the
adapter's ``estimate_cost(request)`` onto the running total. If
the projection would exceed either cap, the call is refused with
a structured payload (see :class:`CostCapExceeded`). After a
successful call the policy records the actual cost via the
ledger so the daily budget reflects reality even when actual
cost differs from estimate.

Refusal payload
---------------
``CostCapExceeded.to_payload()`` returns::

    {
        "kind": "cost_cap_exceeded",
        "scope": "per_goal" | "daily",
        "limit_usd": 0.50,
        "spent_usd": 0.48,
        "projected_usd": 0.55,
        "allowance_usd": 0.02,
        "attempted_capability": "...",
        "attempted_provider": "...",
    }

The workflow executor surfaces this verbatim in the
``WorkflowSubTaskResult.refusal_reason``-side payload, so the UI
can show "we stopped at sub-task 3 because the per-goal cap
($0.50) would have been exceeded by $0.05 — set
``PLANMYAGENTS_COST_CAP_PER_GOAL_USD`` higher to allow this kind
of plan."
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse
    from planmyagents_api.cost.spend_ledger import SpendLedger

LOGGER = logging.getLogger(__name__)


# Defaults are deliberately conservative. They're set so an
# accidentally-loose plan can't silently spend $$$. Operators
# are expected to override via env vars once they understand
# their workload's economics.
DEFAULT_PER_GOAL_USD = 0.50
DEFAULT_DAILY_USD = 20.00

# Float-comparison tolerance for the cap math (1 micro-cent in USD).
# Dollar amounts are stored as IEEE-754 doubles and 50 × 0.01 sums to
# 0.5000000000000003 — without a tolerance, exactly hitting the cap
# would refuse the on-the-line call. The tolerance is well below any
# real billable amount we'd ever charge (smallest known stripe charge
# is $0.50, smallest hunter cost we have is $0.005); it sits five
# orders of magnitude under that, so it can't paper over a real
# overage.
_USD_FLOAT_EPSILON = 1e-6


class CostCapDisabledError(RuntimeError):
    """Raised when ``CostCapPolicy.charge`` is called on a
    disabled policy. The disabled policy should be a no-op
    fast-path; if you see this, your code path is not the
    no-op fast-path you thought it was."""


@dataclass(frozen=True)
class CostCapExceeded:
    """Structured refusal returned when a projected call would
    breach a cap. Not raised — the policy returns it so the
    executor can attach it to the sub-task result without
    unwinding control flow."""

    scope: str
    limit_usd: float
    spent_usd: float
    projected_usd: float
    attempted_capability: str
    attempted_provider: str

    @property
    def allowance_usd(self) -> float:
        return max(self.limit_usd - self.spent_usd, 0.0)

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": "cost_cap_exceeded",
            "scope": self.scope,
            "limit_usd": round(self.limit_usd, 6),
            "spent_usd": round(self.spent_usd, 6),
            "projected_usd": round(self.projected_usd, 6),
            "allowance_usd": round(self.allowance_usd, 6),
            "attempted_capability": self.attempted_capability,
            "attempted_provider": self.attempted_provider,
        }

    def to_refusal_string(self) -> str:
        """Compact one-liner for ``WorkflowSubTaskResult.refusal_reason``.

        Keeps the structured payload accessible via
        ``to_payload()`` for callers that want it verbatim, while
        making the refusal_reason field human-readable on its
        own. The format starts with ``cost_cap_exceeded:`` so
        downstream code can prefix-match without parsing JSON.
        """

        return (
            f"cost_cap_exceeded:{self.scope} "
            f"limit=${self.limit_usd:.4f} "
            f"spent=${self.spent_usd:.4f} "
            f"projected=${self.projected_usd:.4f} "
            f"allowance=${self.allowance_usd:.4f}"
        )


class CostCapPolicy:
    """Track per-goal and per-day spend, refuse when caps breach.

    One instance per goal execution. Construct via
    :func:`cost_cap_policy_from_env` so all callers share the
    same defaults and env var names.

    The class is intentionally synchronous — the caller (the
    workflow executor) is async, but the spend math is in-memory
    + a small ledger write. Making this async would force the
    ledger write inside the hot path's event loop without any
    real concurrency benefit at our volume.
    """

    def __init__(
        self,
        *,
        ledger: SpendLedger | None = None,
        per_goal_usd: float = DEFAULT_PER_GOAL_USD,
        daily_usd: float = DEFAULT_DAILY_USD,
        goal_hash: str = "",
        enabled: bool = True,
    ) -> None:
        if per_goal_usd < 0:
            raise ValueError("per_goal_usd must be non-negative")
        if daily_usd < 0:
            raise ValueError("daily_usd must be non-negative")
        self._ledger = ledger
        self._per_goal_usd = float(per_goal_usd)
        self._daily_usd = float(daily_usd)
        self._goal_hash = goal_hash
        self._enabled = bool(enabled)
        self._goal_spent_usd: float = 0.0
        self._day_spent_usd_baseline: float = 0.0
        if self._enabled and self._ledger is not None:
            try:
                self._day_spent_usd_baseline = float(
                    self._ledger.spend_today_usd()
                )
            except Exception as exc:  # noqa: BLE001 — defensive
                LOGGER.warning(
                    "CostCapPolicy: ledger.spend_today_usd() failed (%s); "
                    "daily cap will start from $0 baseline this request",
                    exc,
                )
                self._day_spent_usd_baseline = 0.0
        self._goal_spend_in_request: float = 0.0

    # --- public surface ------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def per_goal_limit_usd(self) -> float:
        return self._per_goal_usd

    @property
    def daily_limit_usd(self) -> float:
        return self._daily_usd

    @property
    def goal_spent_usd(self) -> float:
        """Spend recorded against this goal so far (in-memory)."""
        return self._goal_spent_usd

    @property
    def day_spent_usd(self) -> float:
        """Best-known daily spend: ledger baseline at construction
        time + any spend this policy recorded since."""
        return self._day_spent_usd_baseline + self._goal_spend_in_request

    def check(
        self,
        *,
        capability: str,
        provider_id: str,
        projected_cost_usd: float,
    ) -> CostCapExceeded | None:
        """Return a refusal if executing the call would breach
        either cap; otherwise return ``None``. Does not record
        anything — call :meth:`charge` after the call completes
        to update the running totals.

        ``projected_cost_usd`` is typically the adapter's
        ``estimate_cost(request)`` value. If the adapter returns a
        bogus negative number, treat it as zero (we still record
        the actual cost after the call).
        """

        if not self._enabled:
            return None
        projection = max(float(projected_cost_usd or 0.0), 0.0)

        per_goal_total = self._goal_spent_usd + projection
        if per_goal_total > self._per_goal_usd + _USD_FLOAT_EPSILON:
            return CostCapExceeded(
                scope="per_goal",
                limit_usd=self._per_goal_usd,
                spent_usd=self._goal_spent_usd,
                projected_usd=per_goal_total,
                attempted_capability=capability,
                attempted_provider=provider_id,
            )

        day_total = self.day_spent_usd + projection
        if day_total > self._daily_usd + _USD_FLOAT_EPSILON:
            return CostCapExceeded(
                scope="daily",
                limit_usd=self._daily_usd,
                spent_usd=self.day_spent_usd,
                projected_usd=day_total,
                attempted_capability=capability,
                attempted_provider=provider_id,
            )

        return None

    def charge(
        self,
        *,
        capability: str,
        provider_id: str,
        cost_usd: float,
        idempotency_key: str = "",
    ) -> None:
        """Record the actual cost of a completed call.

        Updates the in-memory goal/day totals and appends a
        :class:`SpendEvent` to the configured ledger. If the
        policy is disabled (``enabled=False``) this is a no-op
        — disabled policies don't track spend at all, including
        for audit, because the whole point of "disabled" is
        that we trust the caller.
        """

        if not self._enabled:
            return
        amount = max(float(cost_usd or 0.0), 0.0)
        self._goal_spent_usd += amount
        self._goal_spend_in_request += amount
        if self._ledger is None:
            return
        try:
            from planmyagents_api.cost.spend_ledger import SpendEvent

            event = SpendEvent(
                goal_hash=self._goal_hash,
                capability=capability,
                provider_id=provider_id,
                cost_usd=amount,
                idempotency_key=idempotency_key,
            )
            self._ledger.append([event])
        except Exception as exc:  # noqa: BLE001 — must never break /goal
            LOGGER.warning(
                "CostCapPolicy: ledger append failed (capability=%s "
                "provider=%s cost=%.6f): %s; in-memory total is still "
                "current for THIS goal but daily cap will under-count "
                "after this point",
                capability,
                provider_id,
                amount,
                exc,
            )

    async def gated_execute(
        self,
        adapter: Any,
        request: ProviderRequest,
    ) -> tuple[ProviderResponse | None, CostCapExceeded | None]:
        """Convenience wrapper: estimate, check, execute, charge.

        Returns ``(response, None)`` on success, ``(None, refusal)``
        when the cap blocks the call. Either side of the tuple
        is None — never both populated. Callers can pattern-match
        on the second element.

        Note: this awaits ``adapter.estimate_cost`` and
        ``adapter.execute`` exactly once each. If estimate_cost
        raises, we treat the projection as the per-call cost
        baked into the adapter at construction time (or zero) so
        a buggy estimator cannot disable the cap.
        """

        capability = str(getattr(request, "capability", "") or "")
        provider_id = str(getattr(adapter, "provider_id", "") or "")

        projected = 0.0
        try:
            projected_value = await adapter.estimate_cost(request)
            projected = float(projected_value or 0.0)
        except Exception as exc:  # noqa: BLE001 — defensive
            LOGGER.warning(
                "CostCapPolicy.gated_execute: estimate_cost failed for "
                "%s (%s); falling back to provider unit_cost_usd=%s",
                provider_id,
                exc,
                getattr(adapter, "unit_cost_usd", 0.0),
            )
            projected = float(getattr(adapter, "unit_cost_usd", 0.0) or 0.0)

        refusal = self.check(
            capability=capability,
            provider_id=provider_id,
            projected_cost_usd=projected,
        )
        if refusal is not None:
            LOGGER.warning(
                "cost_cap: refused call (scope=%s limit=$%.4f "
                "spent=$%.4f projected=$%.4f provider=%s capability=%s)",
                refusal.scope,
                refusal.limit_usd,
                refusal.spent_usd,
                refusal.projected_usd,
                provider_id,
                capability,
            )
            return None, refusal

        response = await adapter.execute(request)
        actual = float(getattr(response, "cost_usd", 0.0) or 0.0)
        self.charge(
            capability=capability,
            provider_id=provider_id,
            cost_usd=actual,
            idempotency_key=str(getattr(request, "idempotency_key", "") or ""),
        )
        return response, None


# ---------------------------------------------------------------------------
# Env-driven factory
# ---------------------------------------------------------------------------


def _env_bool(name: str, *, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning(
            "cost_cap: %s=%r is not a number; using default $%.4f",
            name,
            raw,
            default,
        )
        return default


def cost_cap_policy_from_env(*, goal_hash: str = "") -> CostCapPolicy:
    """Build a policy from the standard env vars.

    Recognised env vars:

    * ``PLANMYAGENTS_COST_CAP_ENABLED`` (default ``true``) — set
      to ``false`` to bypass all caps and skip ledger writes.
    * ``PLANMYAGENTS_COST_CAP_PER_GOAL_USD`` (default ``0.50``) —
      maximum projected spend across all sub-tasks of one goal.
    * ``PLANMYAGENTS_COST_CAP_DAILY_USD`` (default ``20.00``) —
      maximum total spend across every paid call from every goal
      in one UTC day.
    * ``PLANMYAGENTS_SPEND_LEDGER_PATH`` (resolved by
      :func:`spend_ledger_from_env`) — file path or Postgres DSN
      for the durable ledger backing the daily cap.
    """

    enabled = _env_bool("PLANMYAGENTS_COST_CAP_ENABLED", default=True)
    per_goal = _env_float(
        "PLANMYAGENTS_COST_CAP_PER_GOAL_USD", default=DEFAULT_PER_GOAL_USD
    )
    daily = _env_float(
        "PLANMYAGENTS_COST_CAP_DAILY_USD", default=DEFAULT_DAILY_USD
    )

    ledger = None
    if enabled:
        try:
            from planmyagents_api.cost.spend_ledger import spend_ledger_from_env

            ledger = spend_ledger_from_env()
        except Exception as exc:  # noqa: BLE001 — defensive
            LOGGER.warning(
                "cost_cap: failed to initialise spend ledger (%s); "
                "policy will track per-goal spend only — daily cap "
                "will reset every request",
                exc,
            )
            ledger = None

    return CostCapPolicy(
        ledger=ledger,
        per_goal_usd=per_goal,
        daily_usd=daily,
        goal_hash=goal_hash,
        enabled=enabled,
    )

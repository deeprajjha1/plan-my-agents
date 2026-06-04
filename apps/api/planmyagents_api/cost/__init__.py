"""Cost-cap and spend-ledger primitives for paid provider execution.

Sprint 3 (S3a-7):
    Sprint 3a builds executable wrappers for paid third-party APIs
    (Stripe, Hunter, OpenAI, etc). Without an enforced spend cap,
    a single buggy plan or an adversarial input could drain the
    budget for the day. This package is the safety gate that has
    to be in place *before* we wire up real provider keys in
    production.

Public surface:

* :class:`SpendEvent` and :class:`SpendLedger` — durable record of
  every paid provider call (capability, provider, cost_usd, day).
* :class:`CostCapPolicy` — pre-call check + post-call record around
  any :class:`ProviderAdapter`. Enforces both per-goal and per-day
  caps, returning a structured refusal when either is exceeded.
* :func:`cost_cap_policy_from_env` — convenience constructor that
  reads the standard env vars (``PLANMYAGENTS_COST_CAP_*``) and
  returns a fully wired policy backed by the configured ledger.

Why this lives outside ``agents/`` and ``workflows/``:

The spend ledger is consumed by both the workflow executor (the
caller) and operational tools (auditing, daily-budget alerts,
post-incident reviews). Putting it in its own package keeps it
free of imports from either side and makes it cheap to surface
in operational scripts without dragging in the executor stack.
"""

from planmyagents_api.cost.cost_cap import (
    CostCapDisabledError,
    CostCapExceeded,
    CostCapPolicy,
    cost_cap_policy_from_env,
)
from planmyagents_api.cost.spend_ledger import (
    SpendEvent,
    SpendLedger,
    spend_ledger_from_env,
)

__all__ = [
    "CostCapDisabledError",
    "CostCapExceeded",
    "CostCapPolicy",
    "SpendEvent",
    "SpendLedger",
    "cost_cap_policy_from_env",
    "spend_ledger_from_env",
]

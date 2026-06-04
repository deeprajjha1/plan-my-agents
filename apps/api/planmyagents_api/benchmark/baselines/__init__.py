"""Hand-written benchmark baseline adapters.

CRITICAL — read before adding anything to this package
======================================================

Under the post-16-May-2026 product spec
(see ``BUSINESS_PLAN.md`` §7 and ``docs/ARCHITECTURE.md`` §14) the
ONLY adapter family that may execute on behalf of a user is the
:class:`planmyagents_api.agents.protocol` generic-protocol family
(MCP / A2A / OpenAPI) running against credentials supplied by the
user in their own session (BYO-creds sandbox).

Hand-written vendor wrappers in this package exist for one purpose
only: to give the benchmark runner a known-good baseline against
which discovered protocol providers can be scored. They MUST NOT be
imported from any user-facing routing path. CI lints (T5) enforce
this; the firewall audit cron (T5) audits violations at runtime.

If you find yourself wanting to add a new file here, ask first:
*"Is this baseline strictly necessary to score a NEW benchmark cell?"*
If yes, add it here and ship a paired benchmark cell. If no, write a
provider-side spec or contribute upstream to the MCP / OpenAPI
provider, do not add another wrapper here.
"""

from __future__ import annotations

from typing import Protocol

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse


class BenchmarkAdapter(Protocol):
    """Hand-written baseline contract for benchmark cells only.

    Structurally identical to
    :class:`planmyagents_api.agents.base.ProviderAdapter` so existing
    benchmark code (``benchmark.runner.BenchmarkRunner``) keeps
    working without duck-type changes. The reason this is a separate
    symbol is to give CI lints and code reviewers an unambiguous
    signal: ``BenchmarkAdapter`` implementations live under
    ``benchmark/baselines/`` and may never leak into the customer
    execution path.
    """

    provider_id: str
    capabilities: list[str]

    async def estimate_cost(self, request: ProviderRequest) -> float:
        """Estimate request cost in USD."""

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        """Execute a benchmark request against the vendor API."""

    async def health_check(self) -> bool:
        """Return baseline health."""

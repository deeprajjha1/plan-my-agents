"""Provider adapter interface."""

from __future__ import annotations

from typing import Protocol

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse


class ProviderAdapter(Protocol):
    """Common contract for all callable providers."""

    provider_id: str
    capabilities: list[str]

    async def estimate_cost(self, request: ProviderRequest) -> float:
        """Estimate request cost in USD."""

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        """Execute a provider request."""

    async def health_check(self) -> bool:
        """Return provider health."""

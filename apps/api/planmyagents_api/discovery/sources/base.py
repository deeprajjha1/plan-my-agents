"""Discovery source protocol."""

from __future__ import annotations

from typing import Protocol

from planmyagents_api.discovery.models import DiscoveryCandidate


class DiscoverySource(Protocol):
    """A source that can return normalized discovery candidates."""

    source_id: str

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        """Return candidates that may satisfy the requested capabilities."""

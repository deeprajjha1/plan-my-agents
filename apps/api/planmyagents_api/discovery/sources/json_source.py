"""JSON file/URL discovery source."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate


class DiscoverySourceLoadError(RuntimeError):
    """Raised when a discovery source cannot be loaded."""


@dataclass(frozen=True)
class JsonDiscoverySource:
    """Load normalized candidates from JSON files or URLs.

    Supported payload shapes:
    - `[candidate, ...]`
    - `{ "candidates": [candidate, ...] }`
    - `{ "agents": [candidate, ...] }`
    - `{ "items": [candidate, ...] }`
    """

    locations: list[str]
    source_id: str = "json_source"
    timeout_seconds: float = 10.0
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []
        for location in self.locations:
            for raw in _records_from_payload(
                _load_json(location, timeout_seconds=self.timeout_seconds)
            ):
                try:
                    candidate = normalize_candidate(
                        raw,
                        source=self.source_id,
                        requested_capabilities=sorted(capabilities),
                        goal_hash=self.goal_hash,
                    )
                except (CandidateNormalizationError, TypeError, ValueError):
                    continue
                if capabilities and not candidate.supports_any(capabilities):
                    continue
                candidates.append(candidate)
        return candidates


def _load_json(location: str, *, timeout_seconds: float) -> Any:
    if location.startswith(("http://", "https://")):
        req = request.Request(location, headers={"Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, error.URLError, json.JSONDecodeError) as exc:
            raise DiscoverySourceLoadError(f"failed to load discovery source: {location}") from exc

    path = Path(location).expanduser()
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoverySourceLoadError(f"failed to load discovery source: {location}") from exc


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "agents", "items", "discovered_agents"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []

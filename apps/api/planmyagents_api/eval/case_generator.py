"""Capability-agnostic benchmark case generation.

When a capability has no hand-authored YAML, the Case_Generator synthesizes
candidate ``TestCase``s from a normalized operation input schema (see
``descriptor.py``). Generated sets are marked ``curated=False`` and are
EXCLUDED from any published ``scored_benchmark`` result — they may feed only
``functional_smoke`` until a human curates them.

The generator is intentionally deterministic and dependency-light by default:
given a JSON-schema-ish ``tool_schema`` it produces structurally valid input
dicts. An optional LLM client can enrich ``expected`` values for read-only
capabilities with a known correct answer, but the default path is offline so
the test suite needs no network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import TestCase


@dataclass(frozen=True)
class GeneratedCaseSet:
    capability: str
    version: str
    cases: list[TestCase]
    curated: bool = False


def _placeholder_for(prop_name: str, prop_schema: dict[str, Any]) -> Any:
    """Produce a structurally valid placeholder value for one schema property."""

    schema_type = str(prop_schema.get("type") or "string")
    if "enum" in prop_schema and isinstance(prop_schema["enum"], list) and prop_schema["enum"]:
        return prop_schema["enum"][0]
    if schema_type == "integer":
        return int(prop_schema.get("default", 1))
    if schema_type == "number":
        return float(prop_schema.get("default", 1.0))
    if schema_type == "boolean":
        return bool(prop_schema.get("default", False))
    if schema_type == "array":
        return list(prop_schema.get("default", []))
    if schema_type == "object":
        return dict(prop_schema.get("default", {}))
    # string-ish
    if "default" in prop_schema:
        return str(prop_schema["default"])
    # Heuristic example values for common field names — keeps generated cases
    # plausible without an LLM. RFC-2606 reserved domains are used so a
    # generated read-only case never touches a real third party.
    lname = prop_name.lower()
    if "url" in lname:
        return "https://example.com/"
    if "email" in lname:
        return "user@example.com"
    if "domain" in lname:
        return "example.com"
    if "query" in lname or "q" == lname or "search" in lname:
        return "example query"
    return f"example-{prop_name}"


def _version(capability: str, cases: list[TestCase]) -> str:
    fingerprint = sorted(
        [{"id": c.id, "inputs": c.inputs, "expected": c.expected} for c in cases],
        key=lambda item: item["id"],
    )
    blob = json.dumps(fingerprint, sort_keys=True, default=str).encode("utf-8")
    return f"gen:{capability}:{hashlib.sha256(blob).hexdigest()[:8]}"


@dataclass
class CaseGenerator:
    """Generates uncurated benchmark cases from a tool input schema."""

    chat_client: Any | None = None  # optional EscalatingChatClient
    cases_per_capability: int = 5

    def generate(
        self, *, capability: str, tool_schema: dict[str, Any] | None
    ) -> GeneratedCaseSet:
        schema = tool_schema or {}
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}

        base_inputs: dict[str, Any] = {
            name: _placeholder_for(name, prop if isinstance(prop, dict) else {})
            for name, prop in properties.items()
        }

        cases: list[TestCase] = []
        difficulties = ["easy", "medium", "hard"]
        for index in range(max(self.cases_per_capability, 1)):
            difficulty = difficulties[index % len(difficulties)]
            cases.append(
                TestCase(
                    id=f"gen-{capability}-{index + 1}",
                    capability=capability,
                    difficulty=difficulty,
                    inputs=dict(base_inputs),
                    # Generated cases assert only that the provider returns a
                    # non-empty, non-error result. They are smoke-grade by
                    # design; a human must add real expected values to curate.
                    expected={"status": {"accept": ["ok", "success", "queued", "created"]}},
                )
            )

        return GeneratedCaseSet(
            capability=capability,
            version=_version(capability, cases),
            cases=cases,
            curated=False,
        )


def filter_curated_for_scored(
    case_set: GeneratedCaseSet,
) -> list[TestCase]:
    """Return cases eligible for a ``scored_benchmark`` result.

    Uncurated generated sets contribute ZERO cases to a scored result
    (Property 10). They may still be used for ``functional_smoke`` by the
    caller, which reads ``case_set.cases`` directly.
    """

    return list(case_set.cases) if case_set.curated else []

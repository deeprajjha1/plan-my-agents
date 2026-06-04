"""Open MCP Opportunities — capabilities where APIs exist but no MCP does.

This is the "gap as signal" surface the user explicitly asked for. The
positioning was: Path A (only runnable agents/MCPs/A2A qualify), and the
api_provider rows from APIs.guru aren't dropped — they get reframed as
"APIs without agents", a list of vendors that *could* be wrapped as MCPs
but haven't been by anyone yet.

This module turns the local discovery store + the demand log into one
ranked list. Ranking signal:

    score = demand_request_count + api_supply_count

    where:
      - demand_request_count = how many distinct /goal requests asked
        for this capability (from the demand log)
      - api_supply_count = how many api_provider rows in the index
        match this capability (so a capability with 18 OpenAPI specs
        and 0 MCPs ranks higher than one with 1 OpenAPI spec and 0
        MCPs)

A capability is **only** an "opportunity" if it has at least one
api_provider in the index AND zero qualified MCP/A2A/AI-agent.
Capabilities already covered by an agent are filtered out: the user
doesn't need MCP volunteers for problems that are already solved.

The output is intentionally compact and public-safe — no raw goal
strings beyond a short, sanitized excerpt; no requester identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.discovery.apis_without_agents_store import ApiWithoutAgentRecord
from planmyagents_api.discovery.constants import AGENTIC_PROVIDER_TYPES
from planmyagents_api.discovery.demand_store import DemandSummary
from planmyagents_api.discovery.models import DiscoveryCandidate


@dataclass
class ApiWithoutAgent:
    """One row in the Open MCP Opportunities table."""

    provider_id: str
    display_name: str
    vendor: str
    openapi_url: str
    capabilities: list[str]
    source: str

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "vendor": self.vendor,
            "openapi_url": self.openapi_url,
            "capabilities": list(self.capabilities),
            "source": self.source,
        }


@dataclass
class OpportunityCapability:
    """One capability where APIs exist but no agent does."""

    capability_id: str
    api_supply_count: int
    demand_request_count: int
    distinct_requester_count: int
    score: float
    sample_demand_goals: list[str] = field(default_factory=list)
    apis: list[ApiWithoutAgent] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "api_supply_count": self.api_supply_count,
            "demand_request_count": self.demand_request_count,
            "distinct_requester_count": self.distinct_requester_count,
            "score": round(self.score, 4),
            "sample_demand_goals": list(self.sample_demand_goals),
            "apis": [api.to_json() for api in self.apis],
        }


@dataclass
class OpenMcpOpportunitiesReport:
    capabilities: list[OpportunityCapability] = field(default_factory=list)
    total_opportunities: int = 0
    total_apis_without_agents: int = 0
    methodology: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "total_opportunities": self.total_opportunities,
            "total_apis_without_agents": self.total_apis_without_agents,
            "methodology": self.methodology,
            "capabilities": [cap.to_json() for cap in self.capabilities],
        }


METHODOLOGY = (
    "An 'open MCP opportunity' is a capability where zero MCP servers, "
    "A2A agents, or AI agents in our index implement it, AND at least "
    "one of the following is true: (a) at least one vendor exposes an "
    "OpenAPI spec for it (a wrappable existing API; visible to AI agents "
    "but not directly callable by them), OR (b) at least one /goal "
    "request asked for it (real demand without supply — the deepest "
    "gaps; these rows show api_supply_count=0). Ranked by "
    "demand_request_count + api_supply_count, so capabilities with both "
    "lots of asks and lots of available APIs float to the top; demand-"
    "only rows still surface because the absence of any wrapper is "
    "itself the signal."
)


def _candidate_capability_ids(candidate: DiscoveryCandidate) -> set[str]:
    return {capability.id for capability in candidate.capabilities if capability.id}


def _demand_index(
    summaries: list[DemandSummary],
) -> dict[str, DemandSummary]:
    return {summary.capability_id: summary for summary in summaries}


def compute_open_mcp_opportunities(
    *,
    agentic_candidates: list[DiscoveryCandidate],
    api_records: list[ApiWithoutAgentRecord],
    demand_summaries: list[DemandSummary],
    max_apis_per_capability: int = 12,
    sample_goals_per_capability: int = 3,
) -> OpenMcpOpportunitiesReport:
    """Compute the Open MCP Opportunities report.

    Args:
        agentic_candidates: Loaded from `discovery_candidates`. Used only
            to determine which capabilities are already covered by an
            agent (those are filtered out — no opportunity).
        api_records: Loaded from `apis_without_agents`. The supply side
            of the gap.
        demand_summaries: Aggregated capability demand from the request
            log. Capabilities with no API supply but real demand are
            still surfaced (deepest gaps).

    Rules:
        * A capability counts as "agent-covered" if at least one
          agentic candidate declares it, regardless of
          verification_status — the goal here is to identify *gaps in
          the universe of public agent code*, not gaps in our own
          benchmark coverage. A row that's been superseded
          (`superseded_by_provider_id != ""`) is filtered out of supply
          because somebody already wrapped it.
        * Capabilities with zero APIs and zero demand are dropped.
        * A capability with demand but zero APIs is included with
          api_supply_count=0 — those are the deepest gaps.
    """

    agent_covered: set[str] = set()
    for candidate in agentic_candidates:
        if candidate.provider_type in AGENTIC_PROVIDER_TYPES:
            agent_covered.update(_candidate_capability_ids(candidate))

    apis_by_capability: dict[str, list[ApiWithoutAgentRecord]] = {}
    seen_api_ids: set[str] = set()
    for record in api_records:
        if record.superseded_by_provider_id:
            # Already wrapped by an agent — not an opportunity anymore.
            continue
        if not record.capabilities:
            continue
        for capability_id in record.capabilities:
            apis_by_capability.setdefault(capability_id, []).append(record)
            seen_api_ids.add(record.provider_id)

    demand_lookup = _demand_index(demand_summaries)
    capabilities_to_consider: set[str] = set(apis_by_capability.keys()) | set(
        demand_lookup.keys()
    )

    opportunities: list[OpportunityCapability] = []
    for capability_id in capabilities_to_consider:
        if capability_id in agent_covered:
            continue
        api_records_for_capability = apis_by_capability.get(capability_id, [])
        demand = demand_lookup.get(capability_id)
        api_supply_count = len(api_records_for_capability)
        demand_request_count = demand.request_count if demand else 0
        distinct_requester_count = demand.distinct_requester_count if demand else 0
        sample_goals = list(demand.sample_goals[:sample_goals_per_capability]) if demand else []
        if api_supply_count == 0 and demand_request_count == 0:
            continue
        opportunities.append(
            OpportunityCapability(
                capability_id=capability_id,
                api_supply_count=api_supply_count,
                demand_request_count=demand_request_count,
                distinct_requester_count=distinct_requester_count,
                # Linear score: 1 demand event ~= 1 supply API. Tunable
                # later once we have data; for v1 it's deliberately
                # transparent rather than a magical weighted formula.
                score=float(demand_request_count + api_supply_count),
                sample_demand_goals=sample_goals,
                apis=[
                    _api_row(record)
                    for record in api_records_for_capability[:max_apis_per_capability]
                ],
            )
        )

    opportunities.sort(
        key=lambda opp: (
            -opp.score,
            -opp.demand_request_count,
            -opp.api_supply_count,
            opp.capability_id,
        )
    )

    return OpenMcpOpportunitiesReport(
        capabilities=opportunities,
        total_opportunities=len(opportunities),
        total_apis_without_agents=len(seen_api_ids),
        methodology=METHODOLOGY,
    )


def _api_row(record: ApiWithoutAgentRecord) -> ApiWithoutAgent:
    return ApiWithoutAgent(
        provider_id=record.provider_id,
        display_name=record.display_name,
        vendor=record.vendor or "",
        openapi_url=record.openapi_url or "",
        capabilities=sorted(set(record.capabilities)),
        source=record.source_id,
    )

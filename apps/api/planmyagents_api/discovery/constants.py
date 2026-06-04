"""Shared discovery constants."""

PROVIDER_TYPE_PRIORITY = {
    "mcp_server": 1,
    "a2a_agent": 2,
    "ai_agent": 3,
    "api_provider": 4,
    "payment_provider": 5,
}

VALID_PROVIDER_TYPES = set(PROVIDER_TYPE_PRIORITY)
AGENTIC_PROVIDER_TYPES = {"mcp_server", "a2a_agent", "ai_agent"}

INTERNAL_MISSING_CAPABILITIES = {
    "dependency_resolution",
    "task_classification",
    "provider_routing",
}

API_CREDENTIAL_BLOCKER = "API key is required before execution."
PROTOCOL_CONFIG_BLOCKER = "Remote agent endpoint or protocol client is not configured yet."
API_ADAPTER_BLOCKER = "Executable adapter is not implemented yet."
PROTOCOL_ADAPTER_BLOCKER = "Executable adapter or protocol client is not implemented yet."
BENCHMARK_BLOCKER = "PlanMyAgents has not benchmarked this provider for this capability yet."
API_FALLBACK_BLOCKER = (
    "No promoted MCP/A2A/AI-agent provider is routable for this capability yet."
)
LEGACY_API_FALLBACK_BLOCKER = (
    "No higher-priority MCP/A2A/AI-agent provider is registered for this capability yet."
)


def default_will_fail_reasons(
    provider_type: str, required_env_vars: list[str] | None = None
) -> list[str]:
    """Return blockers that match the candidate type."""

    required_env_vars = required_env_vars or []
    if provider_type in AGENTIC_PROVIDER_TYPES:
        credential_blocker = (
            "Credentials are required before execution."
            if required_env_vars
            else PROTOCOL_CONFIG_BLOCKER
        )
        return [credential_blocker, BENCHMARK_BLOCKER, PROTOCOL_ADAPTER_BLOCKER]

    return [
        API_CREDENTIAL_BLOCKER,
        BENCHMARK_BLOCKER,
        API_ADAPTER_BLOCKER,
        API_FALLBACK_BLOCKER,
    ]


def normalize_will_fail_reasons(
    *,
    provider_type: str,
    required_env_vars: list[str] | None,
    reasons: list[str] | None,
) -> list[str]:
    """Normalize legacy blockers so UI copy stays truthful for each provider type."""

    required_env_vars = required_env_vars or []
    defaults = default_will_fail_reasons(provider_type, required_env_vars)
    if not reasons:
        return defaults

    normalized: list[str] = []
    for reason in reasons:
        if reason == LEGACY_API_FALLBACK_BLOCKER:
            if provider_type in AGENTIC_PROVIDER_TYPES:
                continue
            reason = API_FALLBACK_BLOCKER
        if reason == API_CREDENTIAL_BLOCKER and provider_type in AGENTIC_PROVIDER_TYPES:
            reason = "Credentials are required before execution." if required_env_vars else PROTOCOL_CONFIG_BLOCKER
        if reason == API_ADAPTER_BLOCKER and provider_type in AGENTIC_PROVIDER_TYPES:
            reason = PROTOCOL_ADAPTER_BLOCKER
        if reason not in normalized:
            normalized.append(reason)

    for reason in defaults:
        if reason not in normalized:
            normalized.append(reason)
    return normalized

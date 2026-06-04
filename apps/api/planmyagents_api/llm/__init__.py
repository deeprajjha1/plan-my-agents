"""LLM client abstractions used across planning, intent mapping, query
expansion, agent classification, and the candidate judge.

The headline export is :class:`EscalatingChatClient`, which wraps a
primary local model with a hosted fallback so callers do not need to
think about which tier produced their answer. See
``escalating_client.py`` for the full rationale.
"""

from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    EscalationMetadata,
    EscalationResult,
    NoLlmTierAvailableError,
    QualityVerdict,
    TierAttempt,
    build_default_escalating_client,
)

__all__ = [
    "EscalatingChatClient",
    "EscalationMetadata",
    "EscalationResult",
    "NoLlmTierAvailableError",
    "QualityVerdict",
    "TierAttempt",
    "build_default_escalating_client",
]

"""Natural-language goal planning for the current prototype.

This is deliberately conservative and rule-based. The product vision is to use
LLM-assisted planning against the provider registry, but the first demo needs a
deterministic, inspectable planner that proves the most important behavior:
execute only known capabilities and refuse everything else with clear reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from planmyagents_api.planner.capability_catalog import (
    CapabilityCatalog,
    infer_capabilities_from_catalog,
)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass(frozen=True)
class PlannedSubTask:
    capability: str
    description: str
    inputs: dict

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("planned sub-task capability is required")
        if not self.description.strip():
            raise ValueError("planned sub-task description is required")
        if not isinstance(self.inputs, dict):
            raise TypeError("planned sub-task inputs must be a dict")


@dataclass(frozen=True)
class GoalPlan:
    status: str  # executable | unsupported
    summary: str
    sub_tasks: list[PlannedSubTask] = field(default_factory=list)
    refusal_reasons: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in {"executable", "unsupported"}:
            raise ValueError(f"unsupported goal plan status: {self.status}")
        if not self.summary.strip():
            raise ValueError("goal plan summary is required")
        if self.status == "executable" and not self.sub_tasks:
            raise ValueError("executable goal plans must include sub-tasks")

    @property
    def executable(self) -> bool:
        return self.status == "executable"

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "summary": self.summary,
            "sub_tasks": [
                {
                    "capability": item.capability,
                    "description": item.description,
                    "inputs": item.inputs,
                }
                for item in self.sub_tasks
            ],
            "refusal_reasons": self.refusal_reasons,
            "missing_capabilities": self.missing_capabilities,
        }


def plan_goal(
    goal: str,
    supported_capabilities: set[str] | None = None,
    capability_catalog: CapabilityCatalog | None = None,
) -> GoalPlan:
    """Plan a user goal against currently supported capabilities.

    The planner identifies required capabilities from a user goal. It returns
    executable plans when all required sub-tasks have concrete inputs and are in
    the supported capability set. Otherwise it returns an unsupported plan with
    explicit missing capabilities.
    """

    supported = supported_capabilities or {"email_verification"}
    normalized = goal.strip()
    lowered = normalized.lower()
    emails = sorted(set(EMAIL_RE.findall(normalized)))

    if not normalized:
        return _unsupported(
            summary="No goal was provided.",
            reasons=["Please describe the job you want PlanMyAgents to complete."],
            missing=[],
        )

    if emails and _looks_like_email_verification(lowered):
        if "email_verification" not in supported:
            return _unsupported(
                summary="The goal requires email verification, but that capability is not enabled.",
                reasons=["No active provider is available for `email_verification`."],
                missing=["email_verification"],
            )
        return GoalPlan(
            status="executable",
            summary=(
                f"Can verify {len(emails)} email address(es) using the "
                "email-verification capability."
            ),
            sub_tasks=[
                PlannedSubTask(
                    capability="email_verification",
                    description=f"Verify deliverability for {email}",
                    inputs={"email": email},
                )
                for email in emails
            ],
        )

    if emails and not _looks_like_email_verification(lowered):
        return _unsupported(
            summary=(
                "Email addresses were found, but the requested job is not a supported "
                "email-verification workflow."
            ),
            reasons=[
                (
                    "PlanMyAgents can only execute email deliverability checks for concrete "
                    "email inputs in the current registry."
                ),
                "Please ask something like: 'verify jane@acme.com and bob@example.com'.",
            ],
            missing=[],
        )

    missing = (
        infer_capabilities_from_catalog(lowered, capability_catalog) if capability_catalog else []
    )
    if missing:
        return _unsupported(
            summary=(
                "This goal requires capabilities that are not available in the current "
                "provider registry."
            ),
            reasons=[
                (
                    "PlanMyAgents identified the required task domain from the source-derived "
                    "capability catalog, but no configured provider universe exists for these "
                    "capabilities yet."
                ),
                "Execution is blocked until matching providers are registered, benchmarked, and configured.",
            ],
            missing=missing,
        )

    return _unsupported(
        summary="No currently executable capability matched this goal.",
        reasons=[
            "PlanMyAgents could not confidently classify this goal into executable or source-derived capabilities.",
            "Use the LLM planner or expand discovery sources before attempting execution for this task category.",
        ],
        missing=["task_classification", "provider_routing"],
    )


def _looks_like_email_verification(lowered: str) -> bool:
    keywords = {
        "verify",
        "verification",
        "valid",
        "validate",
        "deliverable",
        "deliverability",
        "bounce",
        "check",
    }
    return any(word in lowered for word in keywords)


def _unsupported(summary: str, reasons: list[str], missing: list[str]) -> GoalPlan:
    return GoalPlan(
        status="unsupported",
        summary=summary,
        refusal_reasons=reasons,
        missing_capabilities=sorted(set(missing)),
    )

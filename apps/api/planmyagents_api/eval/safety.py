"""Side-effect safety classification for eval capabilities.

Runs BEFORE any live/sandbox call. A capability that mutates external state
(sends email, moves money, creates/deletes records) must be classified and,
if side-effecting, must have an explicit approval + fixture before a live run
is permitted. Unknown capabilities default to ``unclassified``, which permits
only ``dry_run`` — the safe default.

The classification table is curated and version-controlled. An optional LLM
heuristic may *propose* a class for an unknown capability, but a proposal is
NEVER auto-promoted to ``read_only``/``side_effecting`` — it is recorded as a
proposal and the effective class stays ``unclassified`` until a human curates
it. This keeps "we might send email" from ever silently becoming "safe to run
live".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from planmyagents_api.eval.models import EvalRunMode


class SafetyClass(StrEnum):
    READ_ONLY = "read_only"
    SIDE_EFFECTING = "side_effecting"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class SafetyApproval:
    """An explicit human approval to evaluate a side-effecting capability.

    ``fixture_spec`` names the synthetic/test destination the eval MUST use
    (e.g. ``{"recipient_domain": "resend.dev"}``). ``allow_live`` is required
    in addition for a real ``live`` run; sandbox is permitted with just a
    fixture spec.
    """

    capability: str
    fixture_spec: dict[str, str]
    approved_by: str
    approved_at: str
    allow_live: bool = False


# Curated seed table. Read-only capabilities return information without
# mutating external state; side-effecting capabilities mutate it. Anything
# not listed is `unclassified` (dry_run only).
_SEED_CLASSES: dict[str, SafetyClass] = {
    # read-only
    "web_scraping": SafetyClass.READ_ONLY,
    "price_comparison": SafetyClass.READ_ONLY,
    "semantic_search": SafetyClass.READ_ONLY,
    "contact_enrichment": SafetyClass.READ_ONLY,
    "email_verification": SafetyClass.READ_ONLY,
    "company_data_lookup": SafetyClass.READ_ONLY,
    "shipping_quote": SafetyClass.READ_ONLY,
    "travel_search": SafetyClass.READ_ONLY,
    "fare_comparison": SafetyClass.READ_ONLY,
    "return_policy_analysis": SafetyClass.READ_ONLY,
    # side-effecting
    "payment_authorization": SafetyClass.SIDE_EFFECTING,
    "email_send": SafetyClass.SIDE_EFFECTING,
    "booking_execution": SafetyClass.SIDE_EFFECTING,
}


@dataclass
class SafetyClassifier:
    """Assigns a safety class and the permitted run modes for a capability."""

    classes: dict[str, SafetyClass] = field(default_factory=lambda: dict(_SEED_CLASSES))
    approvals: dict[str, SafetyApproval] = field(default_factory=dict)
    # Operator overrides win over the seed table (e.g. to mark a freshly
    # curated capability). Proposals from an LLM are NOT placed here.
    overrides: dict[str, SafetyClass] = field(default_factory=dict)

    def classify(self, capability: str) -> SafetyClass:
        if capability in self.overrides:
            return self.overrides[capability]
        return self.classes.get(capability, SafetyClass.UNCLASSIFIED)

    def fixture_for(self, capability: str) -> dict[str, str] | None:
        approval = self.approvals.get(capability)
        return dict(approval.fixture_spec) if approval else None

    def permitted_run_modes(self, capability: str) -> set[EvalRunMode]:
        """Return the run modes allowed for ``capability``.

        * ``read_only``       → dry_run, sandbox, live (no external mutation).
        * ``unclassified``    → dry_run only (safe default).
        * ``side_effecting``  → dry_run always; sandbox iff an approval with a
          fixture exists; live iff the approval also sets ``allow_live``.
        """

        cls = self.classify(capability)
        if cls == SafetyClass.READ_ONLY:
            return {EvalRunMode.DRY_RUN, EvalRunMode.SANDBOX, EvalRunMode.LIVE}
        if cls == SafetyClass.UNCLASSIFIED:
            return {EvalRunMode.DRY_RUN}
        # side_effecting
        modes = {EvalRunMode.DRY_RUN}
        approval = self.approvals.get(capability)
        if approval and approval.fixture_spec:
            modes.add(EvalRunMode.SANDBOX)
            if approval.allow_live:
                modes.add(EvalRunMode.LIVE)
        return modes

    def is_permitted(self, capability: str, run_mode: EvalRunMode) -> bool:
        return run_mode in self.permitted_run_modes(capability)

    def record_proposal(self, capability: str, proposed: SafetyClass) -> SafetyClass:
        """Record an LLM/heuristic proposal WITHOUT applying it.

        The effective class for an unknown capability stays ``unclassified``;
        the proposal is informational only and never auto-promotes. Returns
        the effective class (always ``unclassified`` for an unknown
        capability, regardless of the proposal).
        """

        # Intentionally a no-op on `self.classes` — proposals never mutate the
        # curated table. A human promotes by setting `overrides[...]`.
        _ = proposed
        return self.classify(capability)

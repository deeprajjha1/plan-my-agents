"""Capability brainstorm pass — propose new capability ids the catalog lacks.

When the primary planner returns ``unsupported``, this pass asks a chat model
to think *outside* the current capability catalog and suggest new snake_case
capability ids the goal would actually need. The new ids are:

1. Returned to the planner so the user-facing response is more honest about
   what's missing (richer ``missing_capabilities``).
2. Appended to a JSONL demand-signal file at
   ``.planmyagents_runs/capability-demand.jsonl`` so we can later prioritise which
   capabilities need real provider acquisition.

The brainstorm pass is cheap (one LLM call, JSON-only) and skipped when the
plan is already executable. It defaults to off; enable with
``PLANMYAGENTS_BRAINSTORM=1`` (or pass a chat client explicitly).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)

DEFAULT_DEMAND_LOG = Path(".planmyagents_runs/capability-demand.jsonl")
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class BrainstormChatClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant message content for ``messages``."""


@dataclass(frozen=True)
class BrainstormResult:
    """Outcome of one brainstorm call."""

    proposed_capabilities: list[str]
    raw_response: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "proposed_capabilities": list(self.proposed_capabilities),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


def brainstorm_capabilities(
    *,
    goal: str,
    catalog_capability_ids: Iterable[str],
    existing_missing: Iterable[str],
    chat_client: BrainstormChatClient,
    max_proposed: int = 12,
    persist_path: Path | None = None,
) -> BrainstormResult:
    """Ask the chat client for additional snake_case capability ids."""

    goal_text = (goal or "").strip()
    if not goal_text:
        return BrainstormResult(
            proposed_capabilities=[], skipped=True, skip_reason="empty_goal"
        )

    catalog = sorted({str(item).strip() for item in catalog_capability_ids if str(item).strip()})
    existing = sorted({str(item).strip() for item in existing_missing if str(item).strip()})

    messages = _build_messages(
        goal=goal_text,
        catalog_capability_ids=catalog,
        existing_missing=existing,
        max_proposed=max_proposed,
    )

    try:
        raw = chat_client.complete(messages)
    except Exception as exc:  # noqa: BLE001 - intentionally broad
        LOGGER.info("brainstorm chat call failed: %s", exc)
        return BrainstormResult(
            proposed_capabilities=[], skipped=True, skip_reason=f"chat_error:{exc}"
        )

    proposed = _parse_capabilities(raw, max_proposed=max_proposed)
    proposed = [
        capability
        for capability in proposed
        if capability not in catalog and capability not in existing
    ]

    if proposed:
        _persist_demand(
            persist_path or DEFAULT_DEMAND_LOG,
            goal=goal_text,
            proposed=proposed,
        )

    return BrainstormResult(proposed_capabilities=proposed, raw_response=raw)


def is_brainstorm_enabled() -> bool:
    """Honour ``PLANMYAGENTS_BRAINSTORM`` (truthy values enable the pass)."""

    raw = os.getenv("PLANMYAGENTS_BRAINSTORM", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _build_messages(
    *,
    goal: str,
    catalog_capability_ids: list[str],
    existing_missing: list[str],
    max_proposed: int,
) -> list[dict[str, str]]:
    system = (
        "You expand a user's goal into additional snake_case capability ids "
        "that an autonomous agent platform would need. You are NOT planning "
        "what's already there; you are surfacing capability gaps the catalog "
        "doesn't yet know about.\n\n"
        "Hard rules:\n"
        f"- Return strictly JSON: {{\"new_capabilities\": [\"capability_id\", ...]}}\n"
        f"- Propose at most {max_proposed} ids.\n"
        "- snake_case, lowercase, alphanumeric + underscore, 3-64 chars.\n"
        "- Each id must describe ONE atomic operation (good: kyc_aml_check;\n"
        "  bad: payment_and_compliance).\n"
        "- Do NOT repeat ids that already appear in catalog or existing_missing.\n"
        "- Cover the goal end-to-end: auth, identity, due diligence, financial,\n"
        "  legal, regulatory, registration, settlement, communication, and\n"
        "  reporting where relevant.\n"
        "- Prefer concrete domain operations over generic verbs (good:\n"
        "  property_title_lookup; bad: search).\n"
    )
    user = json.dumps(
        {
            "goal": goal,
            "catalog_capability_ids": catalog_capability_ids,
            "existing_missing": existing_missing,
        }
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_capabilities(raw: str, *, max_proposed: int) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    payload = _safe_load_json(text)
    if not isinstance(payload, dict):
        return []
    candidates = payload.get("new_capabilities")
    if not isinstance(candidates, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str):
            continue
        capability_id = item.strip().replace(" ", "_").replace("-", "_")
        if not _SNAKE_CASE.match(capability_id):
            continue
        if capability_id in seen:
            continue
        seen.add(capability_id)
        cleaned.append(capability_id)
        if len(cleaned) >= max_proposed:
            break
    return cleaned


def _safe_load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None


def _persist_demand(path: Path, *, goal: str, proposed: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "proposed_at": datetime.now(UTC).isoformat(),
            "goal": goal,
            "capabilities": list(proposed),
            "source": "planner_brainstorm",
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:  # pragma: no cover - logging only
        LOGGER.info("failed to persist brainstorm demand signal: %s", exc)

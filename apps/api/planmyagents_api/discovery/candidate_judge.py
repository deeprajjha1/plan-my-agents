"""LLM-driven retrieval-time relevance judge.

Why this module exists
----------------------
For years the discovery pipeline trusted *substring-derived* capability
tags assigned at ingest time. A school-project MCP server tagged with
``fare_comparison`` because its description happened to contain the
word "compare" would happily appear as the answer to a goal like "buy
the cheapest single malt". Substring matching has no notion of
relevance.

The judge replaces that trust with an actual relevance question:

    Goal: <user goal text>
    Required capabilities: <C1, C2, ...>
    Candidates: [<id, display_name, source, description>, ...]

    For each candidate, return:
      {relevant: bool, capabilities_served: [...], reason: "<one sentence>",
       confidence: 0.0-1.0}

The judge is invoked at **retrieval time** (per /goal request). It
batches up to ``DEFAULT_BATCH_SIZE`` candidates per LLM call. Verdicts
are *not* persisted by default — every request gets a fresh judgment
against its own goal, since the same candidate may be relevant to one
goal and irrelevant to another.

Failure model
-------------
The judge uses :class:`EscalatingChatClient`, so a primary-tier failure
escalates automatically. If both LLM tiers are down, the judge raises
:class:`NoLlmTierAvailableError` and the route handler is expected to
surface a clean refusal — there is no substring-matching fallback by
design.

If the judge succeeds at the LLM call but the model returns a verdict
for an unknown candidate id, that verdict is dropped (the model is not
allowed to invent providers). If the model omits a candidate
entirely, the candidate is treated as ``relevant=false`` so unknown
verdicts cannot fail-open.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    EscalationMetadata,
    NoLlmTierAvailableError,
    QualityVerdict,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 25
DEFAULT_MIN_CONFIDENCE = 0.5
MAX_DESCRIPTION_CHARS = 280


@dataclass(frozen=True)
class CandidateVerdict:
    """The judge's per-candidate verdict.

    ``capabilities_served`` is intersected with the goal's required
    capabilities by the caller, so we don't trust the LLM to invent
    capability ids — we only trust it to confirm membership.
    """

    candidate_id: str
    relevant: bool
    confidence: float
    reason: str
    capabilities_served: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JudgeResult:
    """Aggregated outcome of a judge invocation.

    ``accepted`` and ``rejected`` are the two halves of ``verdicts``
    after applying the confidence floor.
    """

    accepted: list[DiscoveryCandidate]
    rejected: list[DiscoveryCandidate]
    verdicts: dict[str, CandidateVerdict]
    metadata: EscalationMetadata
    min_confidence: float
    batch_count: int

    def to_summary(self) -> dict[str, Any]:
        sample_rejection = ""
        for cand in self.rejected[:1]:
            verdict = self.verdicts.get(cand.id)
            if verdict and verdict.reason:
                sample_rejection = f"{cand.id}: {verdict.reason}"
                break
        return {
            "tier_used": self.metadata.tier_used,
            "primary_label": self.metadata.primary_label,
            "fallback_label": self.metadata.fallback_label,
            "primary_attempted": self.metadata.primary_attempted,
            "primary_error": self.metadata.primary_error,
            "fallback_attempted": self.metadata.fallback_attempted,
            "fallback_error": self.metadata.fallback_error,
            "quality_check_triggered": self.metadata.quality_check_triggered,
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "min_confidence": self.min_confidence,
            "batches": self.batch_count,
            "sample_rejection_reason": sample_rejection,
        }


@dataclass
class CandidateJudge:
    """LLM-driven retrieval-time relevance filter.

    Construct once with an :class:`EscalatingChatClient`; call
    :meth:`judge` per discovery request. The judge does not persist
    verdicts (the conversation chose ``every_request_no_persist``); a
    separate audit script may use the same judge for batch cleanup.
    """

    chat_client: EscalatingChatClient
    batch_size: int = DEFAULT_BATCH_SIZE
    min_confidence: float = DEFAULT_MIN_CONFIDENCE

    def judge(
        self,
        *,
        goal: str,
        required_capabilities: list[str],
        candidates: list[DiscoveryCandidate],
        acceptance_criteria_by_capability: dict[str, str] | None = None,
    ) -> JudgeResult:
        """Run the LLM judge over ``candidates`` for ``goal``.

        ``acceptance_criteria_by_capability`` is the per-capability
        acceptance test the goal decomposer wrote for this request.
        When present, those one-sentence criteria are included in
        the judge's prompt alongside the bare capability ids — giving
        the LLM a much clearer specification of what "relevant" means
        for each capability than the slug alone. Capabilities without
        criteria fall back to slug-only matching, so unrouted
        downstream paths keep working.

        Raises :class:`NoLlmTierAvailableError` if every LLM tier
        fails on the first batch — the caller is expected to refuse the
        request rather than ship un-judged results.
        """

        import time as _time

        judge_started = _time.monotonic()
        normalized_goal = (goal or "").strip()
        normalized_caps = sorted({c.strip() for c in required_capabilities if c and c.strip()})
        criteria_map = {
            str(k).strip(): str(v).strip()
            for k, v in (acceptance_criteria_by_capability or {}).items()
            if str(k).strip() and isinstance(v, str) and v.strip()
        }
        LOGGER.info(
            "judge: starting candidates=%d capabilities=%d batch_size=%d "
            "min_confidence=%.2f",
            len(candidates),
            len(normalized_caps),
            self.batch_size,
            self.min_confidence,
        )
        if not candidates:
            LOGGER.info("judge: skipped (no candidates)")
            return JudgeResult(
                accepted=[],
                rejected=[],
                verdicts={},
                metadata=EscalationMetadata(
                    primary_label=self.chat_client.primary_label,
                    fallback_label=self.chat_client.fallback_label,
                ),
                min_confidence=self.min_confidence,
                batch_count=0,
            )

        verdicts: dict[str, CandidateVerdict] = {}
        merged_metadata = EscalationMetadata(
            primary_label=self.chat_client.primary_label,
            fallback_label=self.chat_client.fallback_label,
        )
        first_batch_failed: NoLlmTierAvailableError | None = None
        batches = list(_chunked(candidates, self.batch_size))
        for index, batch in enumerate(batches):
            try:
                batch_verdicts, batch_metadata = self._judge_batch(
                    goal=normalized_goal,
                    required_capabilities=normalized_caps,
                    batch=batch,
                    acceptance_criteria=criteria_map,
                )
            except NoLlmTierAvailableError as exc:
                # Refuse on first-batch failure — partial results are
                # worse than no results because they look complete.
                if index == 0:
                    first_batch_failed = exc
                    break
                # Subsequent batch failure: log it, keep what we have,
                # mark remaining as rejected (fail-closed) and merge the
                # error into metadata so it surfaces in the UI.
                LOGGER.warning(
                    "candidate_judge: batch %d failed after partial success: %s",
                    index,
                    exc,
                )
                merged_metadata.fallback_error = (
                    merged_metadata.fallback_error
                    or f"batch_{index}_failed: {exc}"
                )
                continue

            verdicts.update(batch_verdicts)
            _merge_metadata(merged_metadata, batch_metadata)

        if first_batch_failed is not None:
            LOGGER.warning(
                "judge: refused after %dms (first-batch failure): %s",
                int((_time.monotonic() - judge_started) * 1000),
                first_batch_failed,
            )
            # Re-raise so the route can refuse cleanly.
            raise first_batch_failed

        accepted: list[DiscoveryCandidate] = []
        rejected: list[DiscoveryCandidate] = []
        for cand in candidates:
            verdict = verdicts.get(cand.id)
            if verdict is None:
                # Model did not return a verdict for this candidate.
                # Fail-closed: treat as rejected with a synthesized
                # verdict so the UI can show an honest reason.
                fail_closed = CandidateVerdict(
                    candidate_id=cand.id,
                    relevant=False,
                    confidence=0.0,
                    reason="Judge did not return a verdict for this candidate.",
                )
                verdicts[cand.id] = fail_closed
                rejected.append(cand)
                continue
            if verdict.relevant and verdict.confidence >= self.min_confidence:
                accepted.append(cand)
            else:
                rejected.append(cand)

        elapsed_ms = int((_time.monotonic() - judge_started) * 1000)
        LOGGER.info(
            "judge: complete elapsed=%dms batches=%d accepted=%d rejected=%d "
            "primary_tier=%s tier_used=%s",
            elapsed_ms,
            len(batches),
            len(accepted),
            len(rejected),
            merged_metadata.primary_label,
            merged_metadata.tier_used,
        )
        # Per-rejection diagnostic line. Without this, every refused
        # /goal looks identical in the logs ("rejected=12") and the
        # operator can't tell whether the judge was correctly filtering
        # genuinely off-topic candidates or incorrectly rejecting
        # qualified ones. We log INFO not DEBUG because rejection
        # patterns are exactly the signal the supply gap analysis
        # needs (which providers got close enough to be judged at
        # all, and what verdict reason explained the no).
        # Format: pipe-delimited so a future log shipper can parse.
        # Cap at 25 entries to avoid log spam on huge batches.
        for cand in rejected[:25]:
            verdict = verdicts.get(cand.id)
            confidence = verdict.confidence if verdict is not None else 0.0
            reason = (verdict.reason if verdict is not None else "no verdict") or ""
            # Reasons can be multi-sentence; trim to keep one log line.
            short_reason = reason.replace("\n", " ").strip()
            if len(short_reason) > 240:
                short_reason = short_reason[:237] + "..."
            sources = ",".join(cand.source_ids) or "?"
            LOGGER.info(
                "judge: rejected provider_id=%s display_name=%r source=%s "
                "confidence=%.2f reason=%r",
                cand.id,
                cand.display_name,
                sources,
                confidence,
                short_reason,
            )
        if len(rejected) > 25:
            LOGGER.info(
                "judge: rejected (truncated): %d additional rejections not logged",
                len(rejected) - 25,
            )
        return JudgeResult(
            accepted=accepted,
            rejected=rejected,
            verdicts=verdicts,
            metadata=merged_metadata,
            min_confidence=self.min_confidence,
            batch_count=len(batches),
        )

    def _judge_batch(
        self,
        *,
        goal: str,
        required_capabilities: list[str],
        batch: list[DiscoveryCandidate],
        acceptance_criteria: dict[str, str] | None = None,
    ) -> tuple[dict[str, CandidateVerdict], EscalationMetadata]:
        messages = _build_messages(
            goal=goal,
            required_capabilities=required_capabilities,
            batch=batch,
            acceptance_criteria=acceptance_criteria or {},
        )
        result = self.chat_client.complete_with_metadata(
            messages, quality_check=_quality_check_for_judge_payload
        )
        parsed = _parse_judge_payload(result.content)
        verdicts = _verdicts_from_payload(parsed, batch=batch)
        return verdicts, result.metadata


def _quality_check_for_judge_payload(content: str) -> QualityVerdict:
    """Return ESCALATE if the primary's content cannot be parsed as a
    judge payload at all. Defers ACCEPT to the parser/coercion layer
    which can salvage common malformations (markdown fences, prepended
    chatter)."""

    parsed = _parse_judge_payload(content)
    if parsed is None or "verdicts" not in parsed:
        return QualityVerdict.ESCALATE
    verdicts = parsed.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        return QualityVerdict.ESCALATE
    return QualityVerdict.ACCEPT


_SYSTEM_PROMPT = """
You are PlanMyAgents's relevance judge.

Given a user's goal, the capabilities the planner says it needs, and a
list of discovered candidates (each with an id, display name, source,
and short description), decide which candidates can actually help with
the goal.

The user message MAY also include ``acceptance_criteria_by_capability``
— a per-capability one-sentence description of what a candidate must
actually do for it to count as "relevant" for that capability. When
present, treat those criteria as the authoritative definition of
relevance for each capability and judge candidates against them.
When absent, fall back to inferring relevance from the goal text and
the capability id.

Rules:
- Relevance is judged against the user's *real-world intent expressed in
  the goal text* and (when provided) against the per-capability
  acceptance criteria. A candidate whose ingest tags happen to include
  a required capability but whose description does not satisfy the
  matching acceptance criterion is NOT relevant.
- A capability-broad candidate (e.g. a generic "place search" tool) IS
  relevant to a domain-specific goal that needs that capability — judge
  on whether the candidate could plausibly be used for the goal, not on
  whether its description names the goal's domain.
- Mark relevant=false for: school projects, demo/test/sandbox repos,
  template repositories, blog posts, discussions, generic LLM wrappers
  with no domain capability, and anything whose description does not
  match the acceptance criterion (or, when no criterion is provided,
  the goal's actual domain).
- "capabilities_served" must be a subset of the provided
  required_capabilities list. Do not invent capability ids.
- Confidence is your own assessment, 0.0-1.0. Use 0.8+ only when the
  description clearly satisfies the acceptance criterion (or the
  capability id when no criterion is provided). Use 0.5-0.7 for
  plausible but not certain matches. Use < 0.5 for "probably wrong".
- Each candidate carries a "verification_status" field. It is NOT a
  relevance signal but it IS a confidence signal — apply this cap on
  your confidence score regardless of how good the description sounds:
    * "capability_verified": no cap (we tested this candidate).
    * "registered_in_directory": no cap (vendor-curated registry).
    * "community_listed": cap confidence at 0.6 (self-listed on a
      social network with one external artifact link cross-referenced
      — could be real, could be a phantom; the artifact link gives
      evidence but does not prove the agent works).
    * "unverified" / anything else: cap confidence at 0.7.
- Each candidate also carries an optional "provenance" object with
  signals like ``smithery_use_count``, ``marketplace_github_stars``,
  ``moltbook_karma``. Use these as TIE-BREAKERS only — they don't
  change relevance, but when two candidates serve the same capability
  prefer the one with more usage evidence.
- Reason must be one short sentence (<= 140 chars) explaining the
  verdict, written for a human reviewer.
- Return JSON only. No markdown, no commentary.

Output schema:
{
  "verdicts": [
    {
      "candidate_id": "<exact id from input>",
      "relevant": true | false,
      "confidence": 0.0-1.0,
      "capabilities_served": ["<subset of required_capabilities>"],
      "reason": "<<= 140 char explanation>"
    }
  ]
}
""".strip()


def _build_messages(
    *,
    goal: str,
    required_capabilities: list[str],
    batch: list[DiscoveryCandidate],
    acceptance_criteria: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    candidate_view = [
        {
            "id": cand.id,
            "display_name": cand.display_name,
            "source": cand.source,
            "provider_type": cand.provider_type,
            "vendor": cand.vendor,
            "url": cand.evidence_url or cand.vendor_url,
            "ingest_tagged_capabilities": [c.id for c in cand.capabilities],
            "description": _short_description(cand),
            # Sprint 2 (S2-NEW-3): expose the per-candidate
            # verification_status to the judge so it can downweight
            # ``community_listed`` (Moltbook self-listings) vs
            # ``capability_verified`` (we ran the agent's
            # discovery method and got back the declared tools).
            # See ``apps/web/src/lib/tags.ts`` for the canonical
            # status -> human label mapping.
            "verification_status": cand.verification_status,
            # Provenance signals (Smithery useCount, MCP
            # Marketplace githubStars, Moltbook karma, etc.) — the
            # judge can use these as tiebreakers when two
            # candidates serve the same capability. Kept to a
            # whitelist of judge-relevant keys so we don't waste
            # tokens on internals.
            "provenance": _provenance_for_judge(cand.metadata),
        }
        for cand in batch
    ]
    user_payload: dict[str, Any] = {
        "goal": goal or "(empty goal)",
        "required_capabilities": required_capabilities or [],
        "candidates": candidate_view,
    }
    # When the goal decomposer wrote per-capability acceptance
    # criteria, surface them to the judge as a structured map. Each
    # criterion is a single sentence specifying what a candidate
    # must actually do for it to count as relevant for that
    # capability — far more informative than the slug alone.
    criteria = {
        capability: acceptance_criteria[capability]
        for capability in (required_capabilities or [])
        if acceptance_criteria and capability in acceptance_criteria
    }
    if criteria:
        user_payload["acceptance_criteria_by_capability"] = criteria
    user = json.dumps(user_payload, indent=2, sort_keys=True)
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


_JUDGE_RELEVANT_PROVENANCE_KEYS = (
    # Smithery — adoption + reachability signals.
    "smithery_use_count",
    "smithery_is_deployed",
    "smithery_is_remote",
    # MCP Marketplace — installability + adoption signals.
    "marketplace_install_command",
    "marketplace_github_stars",
    "marketplace_rating",
    "marketplace_security_score",
    "marketplace_tool_count",
    # Moltbook — credibility signals.
    "moltbook_is_claimed",
    "moltbook_karma",
    "moltbook_corroboration_kind",
    "moltbook_ranked_post_count",
)


def _provenance_for_judge(metadata: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the metadata keys the judge prompt needs.

    The full ``metadata`` bag can hold dozens of source-specific keys;
    sending all of them inflates token usage and confuses the judge.
    We keep only the adoption/credibility/reachability signals
    documented in the system prompt as tiebreaker inputs. Anything
    else (avatar URLs, internal IDs, experimental fields) stays in
    the bag for the frontend / downstream consumers but is omitted
    here.
    """

    if not metadata:
        return {}
    return {
        key: metadata[key]
        for key in _JUDGE_RELEVANT_PROVENANCE_KEYS
        if key in metadata
    }


def _short_description(cand: DiscoveryCandidate) -> str:
    """Best-effort short description for the candidate.

    The judge needs *something* to evaluate beyond the id. We assemble
    from whatever fields the source happened to populate, then truncate.
    """

    parts: list[str] = []
    metadata_description = _metadata_description(cand.metadata)
    if metadata_description:
        parts.append(metadata_description)
    if cand.docs.setup_url and cand.docs.setup_url != cand.vendor_url:
        parts.append(f"setup: {cand.docs.setup_url}")
    if cand.docs.auth_method:
        parts.append(f"auth: {cand.docs.auth_method}")
    notes = sorted({c.notes.strip() for c in cand.capabilities if c.notes.strip()})
    if notes:
        parts.append("capability notes: " + " | ".join(notes[:3]))
    obs_notes = sorted({obs.notes.strip() for obs in cand.observations if obs.notes.strip()})
    if obs_notes:
        parts.append("observations: " + " | ".join(obs_notes[:2]))
    if cand.tools:
        tool_names = ", ".join(t.name for t in cand.tools[:5] if t.name)
        if tool_names:
            parts.append(f"tools: {tool_names}")
    text = " || ".join(parts) if parts else f"(no description; vendor={cand.vendor})"
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[: MAX_DESCRIPTION_CHARS - 3] + "..."
    return text


def _metadata_description(metadata: dict[str, Any]) -> str:
    for key in ("description", "summary", "snippet", "tagline"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _parse_judge_payload(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _verdicts_from_payload(
    payload: dict[str, Any] | None, *, batch: list[DiscoveryCandidate]
) -> dict[str, CandidateVerdict]:
    if not payload:
        return {}
    raw_verdicts = payload.get("verdicts")
    if not isinstance(raw_verdicts, list):
        return {}
    valid_ids = {cand.id for cand in batch}
    out: dict[str, CandidateVerdict] = {}
    for entry in raw_verdicts:
        if not isinstance(entry, dict):
            continue
        candidate_id = str(entry.get("candidate_id") or "").strip()
        if candidate_id not in valid_ids:
            # Model invented or hallucinated an id — drop it.
            continue
        relevant = bool(entry.get("relevant"))
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        reason = str(entry.get("reason") or "").strip()[:200]
        served_raw = entry.get("capabilities_served")
        if isinstance(served_raw, list):
            served = sorted({str(c).strip() for c in served_raw if str(c).strip()})
        else:
            served = []
        out[candidate_id] = CandidateVerdict(
            candidate_id=candidate_id,
            relevant=relevant,
            confidence=confidence,
            reason=reason,
            capabilities_served=served,
        )
    return out


def _merge_metadata(target: EscalationMetadata, source: EscalationMetadata) -> None:
    """Fold per-batch metadata into the running aggregate. The first
    batch's tier wins (typical case: every batch took the same tier);
    later disagreement is recorded in the error fields so the UI can
    flag it."""

    if not target.tier_used or target.tier_used == "none":
        target.tier_used = source.tier_used
    target.primary_attempted = target.primary_attempted or source.primary_attempted
    target.fallback_attempted = target.fallback_attempted or source.fallback_attempted
    target.quality_check_triggered = (
        target.quality_check_triggered or source.quality_check_triggered
    )
    if source.primary_error and not target.primary_error:
        target.primary_error = source.primary_error
    if source.fallback_error and not target.fallback_error:
        target.fallback_error = source.fallback_error


def _chunked(items: list[DiscoveryCandidate], size: int):
    if size <= 0:
        size = DEFAULT_BATCH_SIZE
    for i in range(0, len(items), size):
        yield items[i : i + size]


def is_judge_enabled() -> bool:
    """Feature flag: ``PLANMYAGENTS_CANDIDATE_JUDGE`` (default ``on``).

    Setting the value to ``0``/``false``/``off`` skips the judge and
    falls back to the un-judged candidate set. Provided as an escape
    hatch for debugging — production should leave the judge on.
    """

    return os.getenv("PLANMYAGENTS_CANDIDATE_JUDGE", "on").lower() not in {
        "0",
        "false",
        "off",
        "none",
        "disabled",
    }

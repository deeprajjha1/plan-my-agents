"""Searchable in-memory discovery index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.discovery.constants import PROVIDER_TYPE_PRIORITY
from planmyagents_api.discovery.dedupe import dedupe_key, merge_candidates
from planmyagents_api.discovery.freshness import freshness_status
from planmyagents_api.discovery.intent import score_intent_fit
from planmyagents_api.discovery.models import DiscoveryCandidate, DiscoverySearchResult
from planmyagents_api.discovery.query import ExpandedDiscoveryQuery, expand_discovery_query
from planmyagents_api.discovery.sources.base import DiscoverySource


@dataclass
class DiscoveryIndex:
    """Small in-memory index for discovery candidates.

    The interface is intentionally storage-agnostic so it can move to Postgres,
    SQLite FTS, or a vector store without changing callers.
    """

    candidates_by_key: dict[str, DiscoveryCandidate] = field(default_factory=dict)

    def ingest(self, candidates: list[DiscoveryCandidate]) -> None:
        for candidate in candidates:
            key = dedupe_key(candidate)
            existing = self.candidates_by_key.get(key)
            self.candidates_by_key[key] = (
                merge_candidates(existing, candidate) if existing else candidate
            )

    def ingest_sources(
        self,
        sources: list[DiscoverySource],
        *,
        capabilities: set[str],
        task_description: str,
        run_logger: Any = None,
        trigger: str = "batch",
    ) -> None:
        """Run each source and merge its candidates into the index.

        If `run_logger` is provided (a `DiscoveryRunLogger`), each source
        invocation is timed and recorded to the run-event store with
        elapsed_ms / candidates_returned / error / status. Failures in
        one source don't stop the loop — they're recorded as 'error'
        events and the next source still runs.
        """

        sorted_caps = sorted(capabilities)
        for source in sources:
            source_id = getattr(source, "source_id", source.__class__.__name__)
            source_type = source.__class__.__name__
            if run_logger is not None:
                try:
                    with run_logger.record(
                        source_id=str(source_id),
                        source_type=source_type,
                        query=task_description,
                        searched_capabilities=sorted_caps,
                        trigger=trigger,
                    ) as observation:
                        candidates = source.search(
                            capabilities=capabilities,
                            task_description=task_description,
                        )
                        observation.candidates_returned = len(candidates)
                except Exception:  # noqa: BLE001 — record-and-continue
                    continue
                self.ingest(candidates)
            else:
                self.ingest(
                    source.search(
                        capabilities=capabilities,
                        task_description=task_description,
                    )
                )

    def search(
        self,
        *,
        capabilities: set[str],
        task_description: str = "",
        limit: int = 20,
        include_stale: bool = True,
        stale_after_days: int = 30,
    ) -> list[DiscoverySearchResult]:
        expanded = expand_discovery_query(
            capabilities=capabilities, task_description=task_description
        )
        candidates = self.candidates_by_key.values()
        if not include_stale:
            candidates = [
                candidate
                for candidate in candidates
                if not freshness_status(
                    first_seen_at=candidate.first_seen_at,
                    last_seen_at=candidate.last_seen_at,
                    stale_after_days=stale_after_days,
                ).is_stale
            ]
        results = [self._score_candidate(candidate, query=expanded) for candidate in candidates]
        filtered = [result for result in results if result.score > 0]
        return sorted(
            filtered,
            key=lambda result: (
                PROVIDER_TYPE_PRIORITY.get(result.candidate.provider_type, 99),
                -result.score,
                result.candidate.id,
            ),
        )[:limit]

    def all_candidates(self) -> list[DiscoveryCandidate]:
        return sorted(
            self.candidates_by_key.values(),
            key=lambda candidate: (
                PROVIDER_TYPE_PRIORITY.get(candidate.provider_type, 99),
                candidate.id,
            ),
        )

    def _score_candidate(
        self,
        candidate: DiscoveryCandidate,
        *,
        query: ExpandedDiscoveryQuery,
    ) -> DiscoverySearchResult:
        reasons: list[str] = []
        score = 0.0
        candidate_capabilities = {capability.id for capability in candidate.capabilities}
        overlap = candidate_capabilities & query.all_capabilities
        if query.all_capabilities and not overlap:
            return DiscoverySearchResult(candidate=candidate, score=0.0, reasons=[])

        intent_fit = score_intent_fit(candidate=candidate, query=query)
        if intent_fit.excluded:
            return DiscoverySearchResult(
                candidate=candidate,
                score=0.0,
                reasons=intent_fit.reasons,
            )

        if overlap:
            score += 0.75 + (0.05 * len(overlap))
            reasons.append(f"capability_overlap:{','.join(sorted(overlap))}")

        text = f"{candidate.display_name} {candidate.vendor} {candidate.vendor_url} ".lower()
        text += " ".join(capability.id for capability in candidate.capabilities).lower()
        matched_terms = sorted(term for term in query.terms if term in text)
        if matched_terms:
            score += min(0.2, 0.03 * len(matched_terms))
            reasons.append(f"text_match:{','.join(matched_terms[:5])}")

        if intent_fit.score_delta:
            score += intent_fit.score_delta
            reasons.extend(intent_fit.reasons)

        if query.normalizations:
            normalized = ",".join(
                f"{old}->{new}" for old, new in sorted(query.normalizations.items())
            )
            reasons.append(f"normalized:{normalized}")

        priority = PROVIDER_TYPE_PRIORITY.get(candidate.provider_type, 99)
        score += max(0.0, 0.08 - (priority * 0.01))
        reasons.append(f"provider_type:{candidate.provider_type}")

        return DiscoverySearchResult(candidate=candidate, score=round(score, 4), reasons=reasons)

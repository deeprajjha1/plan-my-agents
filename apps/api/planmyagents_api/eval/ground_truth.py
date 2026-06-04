"""Ground-truth resolution for arbitrary capabilities.

A capability is *eval-able* only when ground truth can be obtained:

* exact-match — hand-authored YAML cases in ``packages/benchmarks/<cap>/``.
* rubric — a registered :class:`Rubric` scored by the Eval_Judge.
* none — neither available; the capability is non-eval-able and the reason is
  recorded.

Versions are content hashes (``gt:<cap>:<sha8>``) so "increment on change"
is automatic and reproducible — two identical case sets always produce the
same version, and any edit changes it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from planmyagents_api.benchmark.loader import BenchmarkLoadError, load_test_cases
from planmyagents_api.benchmark.models import TestCase


class GroundTruthKind(StrEnum):
    EXACT_MATCH = "exact_match"
    RUBRIC = "rubric"
    NONE = "none"


@dataclass(frozen=True)
class Rubric:
    """A scoring rubric for soft / open-ended outputs (judge-scored)."""

    capability: str
    criteria: list[str]
    version: str = ""
    scale_min: float = 0.0
    scale_max: float = 1.0

    def resolved_version(self) -> str:
        if self.version:
            return self.version
        return _content_version("rubric", self.capability, self.criteria)


@dataclass(frozen=True)
class GroundTruth:
    capability: str
    kind: GroundTruthKind
    version: str
    cases: list[TestCase] = field(default_factory=list)
    rubric: Rubric | None = None
    reason_if_none: str = ""

    @property
    def is_eval_able(self) -> bool:
        return self.kind != GroundTruthKind.NONE


def _content_version(prefix: str, capability: str, payload: object) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:8]
    return f"gt:{capability}:{digest}" if prefix == "exact" else f"{prefix}:{capability}:{digest}"


def _cases_fingerprint(cases: list[TestCase]) -> object:
    # Stable, content-only projection of the cases for hashing. Order-
    # independent so reordering YAML files doesn't churn the version.
    return sorted(
        [
            {
                "id": c.id,
                "capability": c.capability,
                "difficulty": c.difficulty,
                "inputs": c.inputs,
                "expected": c.expected,
            }
            for c in cases
        ],
        key=lambda item: item["id"],
    )


@dataclass
class GroundTruthManager:
    """Resolves ground truth for a capability."""

    benchmarks_dir: Path
    rubrics: dict[str, Rubric] = field(default_factory=dict)
    case_generator: object | None = None  # CaseGenerator | None (avoid import cycle)

    def resolve(
        self, capability: str, *, tool_schema: dict | None = None
    ) -> GroundTruth:
        # 1. Hand-authored YAML cases. These are either exact-match
        #    (field specs in `expected`) or rubric-shaped (a `rubric_version`
        #    in `expected`, scored by the Eval_Judge). Rubric-shaped cases ARE
        #    the reference inputs that make a judgment-heavy capability
        #    runnable — without them a registered rubric has nothing to score.
        cases = self._load_exact_match(capability)
        if cases:
            version = _content_version("exact", capability, _cases_fingerprint(cases))
            if self._is_rubric_case_set(cases):
                rubric = self.rubrics.get(capability) or self._rubric_from_cases(
                    capability, cases
                )
                return GroundTruth(
                    capability=capability,
                    kind=GroundTruthKind.RUBRIC,
                    version=version,
                    cases=cases,
                    rubric=rubric,
                )
            return GroundTruth(
                capability=capability,
                kind=GroundTruthKind.EXACT_MATCH,
                version=version,
                cases=cases,
            )

        # 2. Registered rubric but NO reference cases → not runnable. A rubric
        #    describes HOW to score; without reference inputs there is nothing
        #    to invoke the agent with, so the capability is non-eval-able and
        #    we say so honestly rather than emitting an empty run set.
        rubric = self.rubrics.get(capability)
        if rubric is not None:
            return GroundTruth(
                capability=capability,
                kind=GroundTruthKind.NONE,
                version="",
                rubric=rubric,
                reason_if_none=(
                    f"a rubric is registered for '{capability}' but no reference "
                    f"cases exist; add rubric-shaped YAML cases in "
                    f"packages/benchmarks/{capability}/ to make it scorable"
                ),
            )

        # 3. Optionally synthesize via the case generator. Generated sets are
        #    uncurated and can only feed functional_smoke (never published),
        #    but they still make the capability eval-able as exact-match.
        if self.case_generator is not None and tool_schema is not None:
            generated = self.case_generator.generate(
                capability=capability, tool_schema=tool_schema
            )
            if generated.cases:
                return GroundTruth(
                    capability=capability,
                    kind=GroundTruthKind.EXACT_MATCH,
                    version=generated.version,
                    cases=generated.cases,
                )

        # 4. Non-eval-able.
        return GroundTruth(
            capability=capability,
            kind=GroundTruthKind.NONE,
            version="",
            reason_if_none=(
                f"no hand-authored YAML, registered rubric, or generatable "
                f"cases for capability '{capability}'"
            ),
        )

    @staticmethod
    def _is_rubric_case_set(cases: list[TestCase]) -> bool:
        """A case set is rubric-shaped when its cases declare a rubric_version.

        We require ALL cases to be rubric-shaped (not a mix) so a capability is
        unambiguously exact-match OR judge-scored. A mixed set is a authoring
        error and falls through to exact-match (the judge hook in
        ``score_response`` only fires per-case on the ones that declare a
        rubric_version, so a mixed set would score inconsistently — we avoid
        that by treating "any non-rubric case" as exact-match-by-default).
        """

        return bool(cases) and all(
            bool(case.expected.get("rubric_version")) for case in cases
        )

    @staticmethod
    def _rubric_from_cases(capability: str, cases: list[TestCase]) -> Rubric:
        """Build a Rubric from the cases' declared criteria + version.

        Reuses what the YAML already carries (``rubric_version`` +
        ``rubric_criteria`` in each case's ``expected``) rather than requiring
        a separate registration. The union of criteria across cases is used so
        the rubric is a faithful superset; the version is the first declared
        ``rubric_version``.
        """

        version = ""
        criteria: list[str] = []
        seen: set[str] = set()
        for case in cases:
            if not version:
                version = str(case.expected.get("rubric_version") or "")
            raw = case.expected.get("rubric_criteria")
            if isinstance(raw, list):
                for item in raw:
                    text = str(item).strip()
                    if text and text not in seen:
                        seen.add(text)
                        criteria.append(text)
        if not criteria:
            criteria = ["The output correctly and completely satisfies the request."]
        return Rubric(capability=capability, criteria=criteria, version=version)

    def _load_exact_match(self, capability: str) -> list[TestCase]:
        cap_dir = self.benchmarks_dir / capability
        if not cap_dir.exists():
            return []
        try:
            return load_test_cases(cap_dir, capability=capability)
        except BenchmarkLoadError:
            return []

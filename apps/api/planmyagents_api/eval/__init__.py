"""PlanMyAgents Agent Evaluation Framework (the "Eval_Framework").

This package turns a discovered agentic candidate into honest,
provenance-bearing performance evidence and feeds it into the existing
benchmark stores (`benchmark_runs`, `agent_rankings`, `verification_records`)
so the existing credibility classifier can grade it.

Design + requirements: `.kiro/specs/agent-eval-framework/`.

Key honesty invariants (see `models.py` source taxonomy + the credibility
classifier deny-list extension):

* Verification-only results carry an ABSENT quality score (``None``), never
  a fabricated zero.
* Only ``exact_match`` / ``judge`` sources count as real runs; every other
  framework-produced ``source`` is non-real to the credibility classifier.
* Non-executable protocols (A2A / ACP / ANP today) yield verification-only
  results — never a fabricated quality score.

The package is import-light at module top-level so callers that only need the
data models (`models.py`) don't pull in network or LLM dependencies.
"""

from __future__ import annotations

from planmyagents_api.eval.models import (
    NON_REAL_EVAL_SOURCES,
    REAL_EVAL_SOURCES,
    EvalProvenance,
    EvalResult,
    EvalRunMode,
    EvalTier,
)

__all__ = [
    "EvalRunMode",
    "EvalTier",
    "EvalProvenance",
    "EvalResult",
    "REAL_EVAL_SOURCES",
    "NON_REAL_EVAL_SOURCES",
]

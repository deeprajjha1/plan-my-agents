from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Disable the discovery run-event audit log for this test module —
# `build_discovery_index` (called inside the eval harness) would
# otherwise write JSONL events into `apps/data/discovery_run_events.jsonl`.
os.environ.setdefault("PLANMYAGENTS_RUN_LOG_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource
from planmyagents_api.discovery.sources.mcp import McpCatalogSource
from planmyagents_api.discovery.sources.static import StaticDiscoverySource
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource
from planmyagents_api.evaluation.generalization import load_scenarios, run_scenarios


class GeneralizationEvalTest(unittest.TestCase):
    def test_generalization_scenarios_pass_against_curated_sources(self) -> None:
        payload = run_scenarios(
            scenarios=load_scenarios(
                ROOT / "packages" / "evals" / "generalization" / "scenarios.json"
            ),
            sources=[
                StaticDiscoverySource(),
                McpCatalogSource([str(ROOT / "packages/discovery/sources/curated_mcp_catalog.json")]),
                A2AAgentCardSource([str(ROOT / "packages/discovery/sources/curated_a2a_cards.json")]),
                AiAgentDirectorySource(
                    [str(ROOT / "packages/discovery/sources/curated_ai_agents.json")]
                ),
                WebDocDiscoverySource(
                    [str(ROOT / "packages/discovery/sources/curated_web_docs.json")]
                ),
            ],
        )

        self.assertTrue(
            payload["passed"],
            msg="\n".join(
                f"{result['scenario_id']}: {result['failures']}"
                for result in payload["results"]
                if not result["passed"]
            ),
        )


if __name__ == "__main__":
    unittest.main()

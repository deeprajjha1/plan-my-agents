"""Defence-in-depth tests for the benchmark-baseline / routing firewall.

T0-7 ships these as the test-level expression of architectural
principle P11 (Vendor-Neutrality Firewall) for the
``planmyagents_api.benchmark.baselines`` boundary. The full CI lint
that scans for ``from .marketplace`` imports inside routing code
lands in Sprint 4 T5-1; until then these tests are the canonical
gate.

If any of these fail, **do not merge** — escalate to the founder.
"""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

REGISTRY_PATH = ROOT / "packages" / "registry" / "agents.json"

# Modules that participate in customer-facing routing / planning /
# execution. None of them may import any submodule under
# ``planmyagents_api.benchmark.baselines``.
ROUTING_MODULES = (
    "planmyagents_api.agents.router",
    "planmyagents_api.agents.protocol",
    "planmyagents_api.agents.sandbox_runner",
    "planmyagents_api.workflows.executor",
    "planmyagents_api.workflows.scoring",
    "planmyagents_api.workflows.stitcher",
    "planmyagents_api.web.app",
    "planmyagents_api.web.planning",
    "planmyagents_api.planner.goal",
    "planmyagents_api.planner.goal_decomposer",
)

# IDs we know are benchmark baselines after the T0 repositioning.
# Kept here as a manual cross-check against agents.json drift —
# any new baseline added MUST also be added to this set, so a
# reviewer sees both edits in the same diff.
EXPECTED_BENCHMARK_BASELINE_IDS = frozenset(
    {
        "hunter",
        "ebay-browse",
        "shippo-shipping",
        "firecrawl",
        "stripe-payments",
        "razorpay-payments",
        "resend-emails",
    }
)


class BenchmarkBaselineFirewallTest(unittest.TestCase):
    """Imports + registry shape match the firewall contract."""

    def test_routing_modules_do_not_import_benchmark_baselines(self) -> None:
        # Import each routing module fresh; afterwards no
        # ``planmyagents_api.benchmark.baselines.*`` submodule should
        # be in sys.modules unless it was loaded by the test runner
        # itself (which it isn't — tests under tests/test_*adapter.py
        # import them via their own paths, and we exclude those here
        # because they aren't loaded by importing routing modules).
        for module_name in ROUTING_MODULES:
            with self.subTest(module=module_name):
                # Strip anything that came in via earlier test runs so
                # each module is evaluated on its own import closure.
                baseline_keys = [
                    name
                    for name in list(sys.modules)
                    if name.startswith("planmyagents_api.benchmark.baselines")
                ]
                for key in baseline_keys:
                    del sys.modules[key]
                importlib.import_module(module_name)
                leaked = [
                    name
                    for name in sys.modules
                    if name.startswith("planmyagents_api.benchmark.baselines")
                ]
                self.assertEqual(
                    leaked,
                    [],
                    f"Routing module `{module_name}` pulled in {leaked} "
                    "— this is a vendor-neutrality firewall violation. "
                    "Hand-written baselines live in benchmark/baselines/ "
                    "and may never be imported by routing-side code.",
                )

    def test_every_agents_json_baseline_entry_has_blank_adapter_module(self) -> None:
        # The registry must blank ``adapter_module`` for every
        # ``is_benchmark_baseline: true`` entry so the router's
        # dynamic loader (``_adapter_factory_from_module``) cannot
        # accidentally fall back into a baseline module.
        registry = json.loads(REGISTRY_PATH.read_text())
        for agent in registry.get("agents", []):
            if not agent.get("is_benchmark_baseline"):
                continue
            with self.subTest(agent=agent["id"]):
                self.assertEqual(
                    agent.get("adapter_module", ""),
                    "",
                    f"Baseline `{agent['id']}` has a non-blank adapter_module "
                    f"({agent.get('adapter_module')!r}). The router would try to load it.",
                )
                self.assertTrue(
                    agent.get("benchmark_adapter_module", "").startswith(
                        "planmyagents_api.benchmark.baselines."
                    ),
                    f"Baseline `{agent['id']}` must declare benchmark_adapter_module "
                    "pointing under planmyagents_api.benchmark.baselines.",
                )

    def test_agents_json_baseline_ids_match_expected_set(self) -> None:
        # If a new baseline is added without updating
        # EXPECTED_BENCHMARK_BASELINE_IDS, the firewall surface widens
        # silently. This test forces both edits in one diff.
        registry = json.loads(REGISTRY_PATH.read_text())
        actual_ids = {
            str(agent["id"])
            for agent in registry.get("agents", [])
            if agent.get("is_benchmark_baseline")
        }
        self.assertEqual(
            actual_ids,
            EXPECTED_BENCHMARK_BASELINE_IDS,
            "Mismatch between registry baseline ids and "
            "EXPECTED_BENCHMARK_BASELINE_IDS — update this test in the "
            "same PR that adds/removes a baseline so the firewall surface "
            "is reviewed each time.",
        )

    def test_no_discovered_agent_references_a_baseline_module(self) -> None:
        # discovered_agents is the parallel surface for promoted
        # candidates; those entries MUST also avoid pointing at a
        # baseline module.
        registry = json.loads(REGISTRY_PATH.read_text())
        for agent in registry.get("discovered_agents", []):
            with self.subTest(agent=agent["id"]):
                self.assertFalse(
                    str(agent.get("adapter_module", "")).startswith(
                        "planmyagents_api.benchmark.baselines."
                    ),
                    f"discovered_agent `{agent['id']}` references a baseline "
                    "module via adapter_module — this is a firewall violation.",
                )


if __name__ == "__main__":
    unittest.main()

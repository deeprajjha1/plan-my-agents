"""End-to-end integration test for slice 2 (label reconciliation +
persistence + catalog growth).

The contract this test pins down is:

1. First request (decomposer coins ``email_dispatch``; reconciler
   sees nothing similar in the catalog) → label is persisted.
2. Second request (different goal text, decomposer coins
   ``send_email_blast``; reconciler now sees ``email_dispatch`` in
   the catalog UNION because it was persisted in step 1) → the
   reconciler matches the new slug to the persisted one.
3. Sub-task slugs in the second response have been rewritten to the
   reused id, so downstream demand and gap aggregation see one
   bucket instead of two.

This is the *operational* test for slice 2 — the unit tests cover
each layer in isolation; this one exercises the seam between layers.

The test uses fakes for both LLMs (decomposer and reconciler) and a
JSON-backed label store in a temp directory so it stays hermetic.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.router import ProviderRouter
from planmyagents_api.planner.capability_catalog import CapabilityCatalog
from planmyagents_api.planner.capability_label_recorder import (
    get_capability_label_store,
    load_persisted_label_ids,
)


class _ScriptedDecomposerClient:
    """Returns one of two pre-baked decomposer payloads in order so
    each ``plan_goal_smart`` call gets the right script for the
    corresponding scenario request."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.calls = 0

    def complete(self, _messages: list[dict[str, str]]) -> str:
        if not self._payloads:
            raise AssertionError(
                "Scripted decomposer received more calls than payloads."
            )
        self.calls += 1
        return json.dumps(self._payloads.pop(0))


class _ScriptedReconcilerClient:
    """Same pattern as the scripted decomposer but for the reconciler
    LLM. Two requests, two reconciler payloads.

    The reconciler is also called with an inspected ``coined_ids``
    payload, so each invocation we record the ids to assert the
    catalog UNION is what we expect.
    """

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.observed_payloads: list[dict] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        # The user message is the second element. We parse it so
        # tests can assert on the catalog union the reconciler saw.
        user_msg = messages[1]["content"]
        try:
            self.observed_payloads.append(json.loads(user_msg))
        except json.JSONDecodeError:
            self.observed_payloads.append({})
        if not self._payloads:
            raise AssertionError(
                "Scripted reconciler received more calls than payloads."
            )
        return json.dumps(self._payloads.pop(0))


def _decomposition_payload(
    *, sub_tasks: list[dict], summary: str = "test summary", confidence: float = 0.9
) -> dict:
    return {
        "intent_summary": summary,
        "confidence": confidence,
        "sub_tasks": sub_tasks,
    }


def _sub_task(
    cap_id: str, *, description: str | None = None, search_query: str | None = None
) -> dict:
    return {
        "description": description or f"do {cap_id}",
        "user_facing_step": f"step {cap_id}",
        "search_query": search_query or f"{cap_id} api",
        "acceptance_criteria": f"agent does {cap_id}",
        "suggested_capability_id": cap_id,
    }


class LabelReconciliationE2ETests(unittest.TestCase):
    """The cross-request label-growth scenario.

    Setup configures two scripted LLMs and a per-test JSON label
    store. ``plan_goal_smart`` is called twice with the env wired so
    the decomposer and reconciler use our scripted clients and the
    catalog comes from a tiny in-memory ``CapabilityCatalog`` that
    starts empty (so the reconciler has only persisted labels to
    consider in the catalog union).
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._label_store_path = str(Path(self._tmp.name) / "labels.json")

        self._previous_env: dict[str, str | None] = {}
        env_overrides = (
            ("PLANMYAGENTS_PLANNER", "rules"),
            ("PLANMYAGENTS_GOAL_DECOMPOSER", "on"),
            ("PLANMYAGENTS_LABEL_RECONCILER", "on"),
            ("PLANMYAGENTS_GOAL_DECOMPOSER_MIN_CONFIDENCE", "0.0"),
            ("PLANMYAGENTS_LABEL_RECONCILER_MIN_CONFIDENCE", "0.7"),
            ("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH", self._label_store_path),
            # Suppress every live discovery source so the catalog
            # builder doesn't fan out HTTP calls during the test.
            ("PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY", "false"),
            ("PLANMYAGENTS_DISCOVERY_APIS_GURU", "false"),
            ("PLANMYAGENTS_DISCOVERY_HACKER_NEWS", "false"),
            ("PLANMYAGENTS_DISCOVERY_VENDOR_RSS", "false"),
            ("PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED", "false"),
            # Disable the embedding-based slug canonicaliser. This test
            # owns the reconciler-persistence path and asserts on the
            # exact coined slug (``email_dispatch``) flowing through to
            # the second-request response. When the embedder is warm
            # (e.g. another test in the suite loaded it earlier),
            # ``email_dispatch`` cosine-matches ``email_send`` at ~0.74
            # and the canonicaliser would rewrite the slug — defeating
            # the reconciler-reuse assertion. The canonicaliser has its
            # own dedicated tests; this test stays focused on the
            # reconciler.
            ("PLANMYAGENTS_SLUG_CANONICALIZATION", "false"),
        )
        for key, value in env_overrides:
            self._previous_env[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _empty_catalog(self) -> CapabilityCatalog:
        return CapabilityCatalog(aliases_by_capability={})

    def _run_first_request(self) -> dict:
        from planmyagents_api.web.planning import plan_goal_smart

        # Decomposer coins ``email_dispatch`` and ``recipient_list_lookup``
        # for a fictional outbound campaign goal. Both are net-new — the
        # catalog is empty (no candidates, no persisted labels yet).
        decomposer_client = _ScriptedDecomposerClient(
            [
                _decomposition_payload(
                    sub_tasks=[
                        _sub_task(
                            "email_dispatch",
                            description="Send the announcement email to the list.",
                        ),
                        _sub_task(
                            "recipient_list_lookup",
                            description="Pull the list of recipients for the campaign.",
                        ),
                    ]
                )
            ]
        )
        # Reconciler sees an empty catalog (no candidates, no
        # persisted labels yet) so it returns no matches — both
        # coined ids must be persisted.
        reconciler_client = _ScriptedReconcilerClient(
            [
                {
                    "matches": [
                        {
                            "coined_id": "email_dispatch",
                            "matched_to": None,
                            "confidence": 0.95,
                            "reason": "no equivalents in catalog",
                        },
                        {
                            "coined_id": "recipient_list_lookup",
                            "matched_to": None,
                            "confidence": 0.95,
                            "reason": "no equivalents in catalog",
                        },
                    ]
                }
            ]
        )

        # Patch the *factory functions* so the modules under test
        # construct our scripted fakes instead of real LLM clients.
        with (
            patch(
                "planmyagents_api.planner.goal_decomposer.build_default_escalating_client",
                return_value=decomposer_client,
            ),
            patch(
                "planmyagents_api.planner.label_reconciler.build_default_escalating_client",
                return_value=reconciler_client,
            ),
        ):
            plan, metadata = plan_goal_smart(
                "send a launch announcement to my mailing list",
                router=ProviderRouter(),
                capability_catalog=self._empty_catalog(),
            )
        self._first_reconciler_payload = (
            reconciler_client.observed_payloads[0]
            if reconciler_client.observed_payloads
            else {}
        )
        return {
            "plan": plan,
            "metadata": metadata,
            "decomposer_calls": decomposer_client.calls,
            "reconciler_payloads": reconciler_client.observed_payloads,
        }

    def _run_second_request(self) -> dict:
        from planmyagents_api.web.planning import plan_goal_smart

        # Different goal, but the decomposer (fictively) coins a
        # synonym slug: ``send_email_blast`` instead of
        # ``email_dispatch``. The reconciler must now match it to the
        # persisted ``email_dispatch`` so the sub-task slug ends up
        # rewritten and downstream consumers see the existing id.
        decomposer_client = _ScriptedDecomposerClient(
            [
                _decomposition_payload(
                    sub_tasks=[
                        _sub_task(
                            "send_email_blast",
                            description="Push the marketing email to all subscribers.",
                        ),
                    ],
                    summary="email blast scenario",
                )
            ]
        )
        reconciler_client = _ScriptedReconcilerClient(
            [
                {
                    "matches": [
                        {
                            "coined_id": "send_email_blast",
                            "matched_to": "email_dispatch",
                            "confidence": 0.92,
                            "reason": "both describe sending mail to a list.",
                        }
                    ]
                }
            ]
        )

        with (
            patch(
                "planmyagents_api.planner.goal_decomposer.build_default_escalating_client",
                return_value=decomposer_client,
            ),
            patch(
                "planmyagents_api.planner.label_reconciler.build_default_escalating_client",
                return_value=reconciler_client,
            ),
        ):
            plan, metadata = plan_goal_smart(
                "blast our newsletter to every subscriber today",
                router=ProviderRouter(),
                capability_catalog=self._empty_catalog(),
            )
        return {
            "plan": plan,
            "metadata": metadata,
            "reconciler_payloads": reconciler_client.observed_payloads,
        }

    def test_first_request_persists_two_coined_labels(self) -> None:
        result = self._run_first_request()
        meta = result["metadata"]

        # Decomposer ran exactly once.
        self.assertEqual(result["decomposer_calls"], 1)

        # Both coined labels were persisted to the JSON store.
        store = get_capability_label_store()
        ids = store.list_ids()
        self.assertEqual(ids, {"email_dispatch", "recipient_list_lookup"})

        # The rich label record carries the decomposer's description,
        # not just the slug. That's the contract the inspection
        # endpoint and any future analytics rely on.
        row = store.get("email_dispatch")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(
            row.description, "Send the announcement email to the list."
        )
        self.assertEqual(row.usage_count, 1)
        self.assertNotEqual(row.coined_from_goal_hash, "")

        # The reconciler short-circuits on an empty catalog (no
        # candidates, no persisted labels yet) so it skips the LLM
        # round-trip and reports ``skipped_no_catalog`` — but
        # persistence still runs and writes both coined ids as new
        # rows. That separation is intentional: the reconciler's job
        # is matching, the persistence's job is catalog growth, and
        # one being a no-op shouldn't disable the other.
        self.assertEqual(meta["label_reconciler"]["status"], "skipped_no_catalog")
        self.assertEqual(meta["label_reconciler"]["labels_persisted"], 2)

    def test_second_request_reuses_persisted_label(self) -> None:
        # Run the first request to seed the persisted labels.
        self._run_first_request()
        self.assertIn("email_dispatch", load_persisted_label_ids())

        # Run the second request. The reconciler should see
        # ``email_dispatch`` in the catalog union and match the new
        # ``send_email_blast`` to it.
        result = self._run_second_request()

        # The reconciler payload it actually saw must include the
        # persisted ``email_dispatch`` in ``existing_catalog_ids``,
        # because the planner's catalog UNION now includes persisted
        # labels.
        self.assertEqual(len(result["reconciler_payloads"]), 1)
        catalog_seen_by_reconciler = result["reconciler_payloads"][0][
            "existing_catalog_ids"
        ]
        self.assertIn("email_dispatch", catalog_seen_by_reconciler)
        self.assertIn("recipient_list_lookup", catalog_seen_by_reconciler)

        # The decomposer's coined slug (``send_email_blast``) must
        # have been rewritten in the sub-task list to the matched
        # persisted id. This is the demand-aggregation payoff: future
        # gap and demand events will use ``email_dispatch`` instead
        # of fragmenting into a new bucket.
        sub_tasks = result["metadata"]["decomposed_sub_tasks"]
        self.assertEqual(len(sub_tasks), 1)
        self.assertEqual(sub_tasks[0]["suggested_capability_id"], "email_dispatch")

        # ``new_capabilities`` should be empty after rewriting since
        # the only coined id was matched.
        decomposer_meta = result["metadata"]["decomposer"]
        self.assertEqual(decomposer_meta["new_capabilities"], [])
        self.assertEqual(
            decomposer_meta["catalog_reused_capabilities"], ["email_dispatch"]
        )

        # Persisted label was UPSERTed: usage_count bumped from 1 → 2.
        store = get_capability_label_store()
        row = store.get("email_dispatch")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.usage_count, 2)

        # The reconciler's metadata reflects exactly one match.
        self.assertEqual(result["metadata"]["label_reconciler"]["status"], "applied")
        matches = result["metadata"]["label_reconciler"]["matches"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["coined_id"], "send_email_blast")
        self.assertEqual(matches[0]["matched_to"], "email_dispatch")

    def test_persistence_survives_between_processes_via_json_file(self) -> None:
        # Simulate a process restart by running the first request,
        # then re-importing nothing — the JSON file is the durable
        # state and should be read by load_persisted_label_ids() on
        # the next call. (We're not actually re-importing modules;
        # the JSON path is what matters.)
        self._run_first_request()
        ids = load_persisted_label_ids()
        self.assertEqual(ids, {"email_dispatch", "recipient_list_lookup"})

        # The label file is a real file on disk in the temp dir.
        on_disk = json.loads(Path(self._label_store_path).read_text(encoding="utf-8"))
        self.assertIsInstance(on_disk, list)
        on_disk_ids = {row["id"] for row in on_disk}
        self.assertEqual(on_disk_ids, {"email_dispatch", "recipient_list_lookup"})


class LabelReconciliationDisabledTests(unittest.TestCase):
    """When ``PLANMYAGENTS_LABEL_RECONCILER=off`` the decomposer
    output flows through unchanged and nothing is persisted. This
    is the deterministic-tests escape hatch the env var was added
    for; we pin it down here so a future change can't silently make
    reconciliation mandatory.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._label_store_path = str(Path(self._tmp.name) / "labels.json")
        self._previous_env: dict[str, str | None] = {}
        env_overrides = (
            ("PLANMYAGENTS_PLANNER", "rules"),
            ("PLANMYAGENTS_GOAL_DECOMPOSER", "on"),
            ("PLANMYAGENTS_LABEL_RECONCILER", "off"),
            ("PLANMYAGENTS_GOAL_DECOMPOSER_MIN_CONFIDENCE", "0.0"),
            ("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH", self._label_store_path),
            ("PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY", "false"),
            ("PLANMYAGENTS_DISCOVERY_APIS_GURU", "false"),
            ("PLANMYAGENTS_DISCOVERY_HACKER_NEWS", "false"),
            ("PLANMYAGENTS_DISCOVERY_VENDOR_RSS", "false"),
            ("PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED", "false"),
        )
        for key, value in env_overrides:
            self._previous_env[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_disabled_reconciler_does_not_persist_anything(self) -> None:
        from planmyagents_api.web.planning import plan_goal_smart

        decomposer_client = _ScriptedDecomposerClient(
            [
                _decomposition_payload(
                    sub_tasks=[_sub_task("brand_new_label")],
                )
            ]
        )

        with patch(
            "planmyagents_api.planner.goal_decomposer.build_default_escalating_client",
            return_value=decomposer_client,
        ):
            _, metadata = plan_goal_smart(
                "test goal",
                router=ProviderRouter(),
                capability_catalog=CapabilityCatalog(aliases_by_capability={}),
            )

        self.assertEqual(metadata["label_reconciler"]["status"], "disabled")
        self.assertFalse(Path(self._label_store_path).exists())


if __name__ == "__main__":
    unittest.main()

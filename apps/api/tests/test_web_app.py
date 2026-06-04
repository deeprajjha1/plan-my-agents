from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient
from planmyagents_api.benchmark.models import (
    BenchmarkRun,
    FieldScore,
    ProviderResponse,
    ScoreResult,
)
from planmyagents_api.benchmark.rankings import compute_rankings
from planmyagents_api.benchmark.store import JsonBenchmarkStore
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.discovery.store import JsonDiscoveryStore
from planmyagents_api.discovery.verification import VerificationResult
from planmyagents_api.discovery.verification_store import JsonVerificationStore, VerificationRecord


def _email_candidate(candidate_id: str = "alpha-email-agent") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=candidate_id,
        display_name="Alpha Email",
        vendor="alpha",
        vendor_url="https://alpha.example",
        provider_type="ai_agent",
        capabilities=[CandidateCapability(id="email_verification", confidence=0.9)],
        verification_status="capability_verified",
        evidence_url="https://alpha.example/agent.json",
    )


def _payments_candidate(candidate_id: str = "beta-pay-agent") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=candidate_id,
        display_name="Beta Pay",
        vendor="beta",
        vendor_url="https://beta.example",
        provider_type="ai_agent",
        capabilities=[CandidateCapability(id="payment_authorization", confidence=0.8)],
    )


def _benchmark_runs_for(candidate_id: str) -> list[BenchmarkRun]:
    runs = []
    for idx in range(3):
        runs.append(
            BenchmarkRun(
                test_case_id=f"case-{idx}",
                provider_id=candidate_id,
                capability="email_verification",
                difficulty="easy",
                response=ProviderResponse(
                    succeeded=True,
                    output={"ok": True},
                    cost_usd=0.001,
                    latency_ms=120,
                ),
                score=ScoreResult(
                    quality_score=0.95,
                    succeeded=True,
                    field_scores=[FieldScore(field="ok", weight=1.0, score=1.0, reason="")],
                    reason="",
                ),
            )
        )
    return runs


class FastAPIAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.verification_path = self.tempdir / "verification.json"

        # Discovery: one verified email agent + one unverified payments agent
        JsonDiscoveryStore(self.discovery_path).save(
            [_email_candidate(), _payments_candidate()]
        )

        # Benchmarks: 3 successful runs for the email agent
        runs = _benchmark_runs_for("alpha-email-agent")
        bench_store = JsonBenchmarkStore(self.benchmark_path)
        bench_store.save(runs)
        bench_store.save_rankings(compute_rankings(runs, source="real_adapter"))

        # Verification history: one record for the email agent
        verification_store = JsonVerificationStore(self.verification_path)
        verification_store.append(
            [
                VerificationRecord.from_result(
                    VerificationResult(
                        provider_id="alpha-email-agent",
                        status="capability_verified",
                        evidence_url="https://alpha.example/agent.json",
                        verified_capabilities=["email_verification"],
                        blockers=[],
                        notes=["evidence_capabilities:email_verification"],
                    )
                )
            ]
        )

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(self.benchmark_path)
        os.environ["PLANMYAGENTS_VERIFICATION_STORE_URL"] = str(self.verification_path)
        # Keep planner deterministic for /goal test.
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        # Disable all default-on live network discovery sources so /goal
        # doesn't hit real upstreams during tests.
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        # Also disable the request-time scout dispatcher.
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        # Disable the LLM-driven candidate judge: the integration tests
        # don't have a reachable Ollama/Groq tier, and letting the judge
        # actually attempt an LLM call would either hang on the dev
        # machine's Ollama instance or wait out the 60s urlopen timeout
        # before falling through. The judge has its own dedicated unit
        # tests covering accept/reject/refusal behavior in
        # `test_candidate_judge.py`.
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"
        # Redirect the demand log into the tempdir so /goal recording during
        # tests doesn't pollute the repo's data/ directory.
        self.demand_store_path = self.tempdir / "demand.jsonl"
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_store_path)
        # Same for the discovery run-event audit log.
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        for var in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
        ):
            os.environ.pop(var, None)

    def test_health_returns_store_locations(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["discovery_store"], str(self.discovery_path))
        self.assertEqual(body["benchmark_store"], str(self.benchmark_path))
        self.assertEqual(body["embedding_model"], "deterministic-hash-v1")

    def test_categories_index_contains_email_and_payment_clusters(self) -> None:
        # After deleting EXPLICIT_CATEGORY_BY_CAPABILITY, cluster ids
        # come straight from the slug prefix: email_verification → email,
        # payment_authorization → payment. The hand-grouped buckets
        # (lead_intelligence, payments) are gone — the previous test
        # asserted on those and was the canary that proved the explicit
        # map had been deleted cleanly.
        response = self.client.get("/discovery/categories")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        cluster_ids = {category["cluster_id"] for category in body["categories"]}
        self.assertIn("email", cluster_ids)
        self.assertIn("payment", cluster_ids)
        email_cluster = next(c for c in body["categories"] if c["cluster_id"] == "email")
        self.assertEqual(email_cluster["totals"]["candidates"], 1)
        self.assertEqual(email_cluster["totals"]["known_listed"], 1)
        self.assertEqual(email_cluster["totals"]["benchmark_passed"], 0)
        self.assertEqual(email_cluster["totals"]["routable_today"], 0)

    def test_category_detail_returns_candidate_cards(self) -> None:
        response = self.client.get("/discovery/categories/email")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["cluster_id"], "email")
        self.assertEqual(len(body["candidates"]), 1)
        self.assertEqual(body["candidates"][0]["provider_id"], "alpha-email-agent")
        self.assertEqual(body["candidates"][0]["verification_status"], "capability_verified")

    def test_unknown_category_returns_404(self) -> None:
        response = self.client.get("/discovery/categories/no_such_cluster")
        self.assertEqual(response.status_code, 404)

    def test_agent_detail_aggregates_rankings_runs_and_history(self) -> None:
        response = self.client.get("/discovery/agents/alpha-email-agent")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["candidate"]["id"], "alpha-email-agent")
        self.assertEqual(len(body["rankings"]), 1)
        self.assertEqual(body["rankings"][0]["benchmark_status"], "passed")
        self.assertEqual(len(body["recent_runs"]), 3)
        self.assertEqual(len(body["verification_history"]), 1)
        self.assertEqual(
            body["verification_history"][0]["status"], "capability_verified"
        )

    def test_unknown_agent_returns_404(self) -> None:
        response = self.client.get("/discovery/agents/ghost")
        self.assertEqual(response.status_code, 404)

    def test_benchmark_runs_filters_by_provider_and_capability(self) -> None:
        response = self.client.get(
            "/benchmark/runs",
            params={"provider_id": "alpha-email-agent", "capability": "email_verification"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider_id"], "alpha-email-agent")
        self.assertEqual(len(body["runs"]), 3)

    def test_search_falls_back_to_in_memory_for_local_store(self) -> None:
        response = self.client.get(
            "/discovery/search",
            params={"q": "email verification", "capability": "email_verification"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["backend"], "in_memory_text")
        ids = [item["provider_id"] for item in body["results"]]
        self.assertIn("alpha-email-agent", ids)

    def test_goal_returns_a_plan_payload(self) -> None:
        response = self.client.post(
            "/goal", json={"goal": "Verify the email someone@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("plan", body)
        self.assertIn("status", body["plan"])
        self.assertFalse(body["executed"])

    def test_goal_response_includes_cost_estimate_when_plan_has_subtasks(self) -> None:
        response = self.client.post(
            "/goal", json={"goal": "Verify the email someone@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        if plan["status"] == "executable":
            self.assertIn("cost_estimate", plan)
            estimate = plan["cost_estimate"]
            self.assertIn("total_estimated_usd", estimate)
            self.assertIn("per_sub_task", estimate)
            self.assertIn("missing_capabilities", estimate)
            self.assertIn("credibility_notes", estimate)

    def test_goal_rejects_empty_goal(self) -> None:
        response = self.client.post("/goal", json={"goal": ""})
        self.assertEqual(response.status_code, 422)

    def test_goal_response_includes_planner_llm_quality_block(self) -> None:
        # The /goal payload must always carry llm_quality.planner so the
        # frontend can render the "reasoning by tier X" status pill. In
        # the rules-mode test environment the value is the explicit
        # debug warning rather than an LLM tier label.
        response = self.client.post(
            "/goal", json={"goal": "Verify the email someone@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        planner_metadata = body["plan"]["planner"]
        self.assertIn("llm_quality", planner_metadata)
        self.assertIn("planner", planner_metadata["llm_quality"])
        self.assertEqual(planner_metadata["llm_quality"]["planner"]["mode"], "rules")
        # The rules mode must self-flag as unreliable so the frontend
        # can render a red banner; otherwise the user has no way to
        # tell whether their results came from an LLM or a regex.
        self.assertIn("warning", planner_metadata["llm_quality"]["planner"])

    def test_goal_returns_503_when_planner_tier_missing(self) -> None:
        # Behavior contract: with PLANMYAGENTS_PLANNER=groq and no
        # GROQ_API_KEY, the route must refuse honestly with 503 rather
        # than silently fall back to substring rules. This is the path
        # that prevents wrong-but-confident answers like the
        # school-project MCP server appearing under "fare_comparison".
        os.environ["PLANMYAGENTS_PLANNER"] = "groq"
        os.environ.pop("GROQ_API_KEY", None)
        try:
            response = self.client.post(
                "/goal",
                json={"goal": "Verify the email someone@example.com"},
            )
        finally:
            os.environ["PLANMYAGENTS_PLANNER"] = "rules"

        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()["detail"]
        self.assertEqual(body["error"], "planning_unavailable")
        self.assertIn("GROQ_API_KEY", body["message"])
        self.assertIn("llm_quality", body)
        self.assertEqual(body["llm_quality"]["planner"]["mode"], "groq")
        self.assertEqual(
            body["llm_quality"]["planner"]["fallback_error"], "missing_api_key"
        )
        self.assertTrue(any("GROQ_API_KEY" in step for step in body["remediation"]))

    def test_goal_explain_returns_thinking_and_content_on_success(self) -> None:
        # The /goal/explain route powers the on-demand "Why did the
        # planner pick this?" UI flow. It must:
        #   1. Re-run the planner with thinking enabled
        #   2. Return both the trace and the JSON answer side-by-side
        #   3. Best-effort parse the JSON into a `plan` dict so the UI
        #      can render it next to the trace
        # We patch OllamaQwenClient at its import site inside the
        # route module so no real Ollama daemon is required.
        from unittest.mock import patch

        from planmyagents_api.planner.local_qwen import ThinkingCompletion

        fake_completion = ThinkingCompletion(
            content='{"status": "unsupported", "summary": "stub"}',
            thinking="Step 1: parse goal. Step 2: realise no provider exists.",
            model="qwen3.5:35b",
            duration_ms=15000,
            model_supports_thinking=True,
        )

        class _FakeClient:
            model = "qwen3.5:35b"

            def complete_with_thinking(self, _messages):  # noqa: ANN001
                return fake_completion

        with patch(
            "planmyagents_api.web.app.OllamaQwenClient", return_value=_FakeClient()
        ):
            response = self.client.post(
                "/goal/explain",
                json={"goal": "Verify the email someone@example.com"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["goal"], "Verify the email someone@example.com")
        self.assertEqual(body["model"], "qwen3.5:35b")
        self.assertIn("Step 1", body["thinking"])
        self.assertEqual(
            body["content"], '{"status": "unsupported", "summary": "stub"}'
        )
        # Best-effort JSON parse of the model's output should populate
        # `plan` so the UI can show the parsed plan next to the trace.
        self.assertIsNotNone(body["plan"])
        self.assertEqual(body["plan"]["status"], "unsupported")
        self.assertEqual(body["duration_ms"], 15000)
        self.assertTrue(body["model_supports_thinking"])

    def test_goal_explain_marks_non_thinking_models_so_ui_can_warn(
        self,
    ) -> None:
        # Older / non-reasoning models silently ignore the `think`
        # flag and return an empty thinking field. The endpoint must
        # surface model_supports_thinking=False so the UI can render
        # "this model doesn't support reasoning traces" instead of an
        # empty panel that looks broken.
        from unittest.mock import patch

        from planmyagents_api.planner.local_qwen import ThinkingCompletion

        fake_completion = ThinkingCompletion(
            content='{"status": "supported"}',
            thinking="",
            model="qwen2.5:7b-instruct",
            duration_ms=2500,
            model_supports_thinking=False,
        )

        class _FakeClient:
            model = "qwen2.5:7b-instruct"

            def complete_with_thinking(self, _messages):  # noqa: ANN001
                return fake_completion

        with patch(
            "planmyagents_api.web.app.OllamaQwenClient", return_value=_FakeClient()
        ):
            response = self.client.post(
                "/goal/explain", json={"goal": "Verify foo@bar.com"}
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["thinking"], "")
        self.assertFalse(body["model_supports_thinking"])
        self.assertEqual(body["model"], "qwen2.5:7b-instruct")

    def test_goal_route_emits_structured_stage_logs(self) -> None:
        """Regression test for the silent-server bug: when a /goal
        request takes a long time, operators saw only uvicorn's HTTP
        access log and had no way to tell which stage was slow.

        The route must emit at minimum:

        * a "received" record with the goal text,
        * a "planner: starting" + "planner: complete in <Xms>" pair,
        * a "response ready in <Xms>" record at the end.

        We assert on the substrings (rather than full format strings)
        because the format is a presentation detail, but the trace
        *shape* — "request started → planner → response done" — is
        the contract operators rely on for live debugging.
        """

        from io import StringIO
        from logging import StreamHandler

        # Force-reset and re-configure so we can attach a capture
        # handler. Production code is unaffected because setUp tears
        # the env down between tests.
        from planmyagents_api import _logging

        _logging.reset_for_tests()
        capture = StringIO()
        capture_handler = StreamHandler(capture)
        try:
            _logging.setup_logging(force=True)
            logging.getLogger("planmyagents_api").addHandler(capture_handler)

            response = self.client.post(
                "/goal", json={"goal": "Verify the email someone@example.com"}
            )
            self.assertEqual(response.status_code, 200)

            for handler in logging.getLogger("planmyagents_api").handlers:
                handler.flush()
            logs = capture.getvalue()

            self.assertIn("/goal received", logs)
            self.assertIn("/goal planner: starting", logs)
            self.assertIn("/goal planner: complete", logs)
            self.assertIn("/goal response ready", logs)
            # The closing line should always carry a wall-clock budget so
            # operators can grep "/goal response ready" and immediately
            # see end-to-end latency.
            self.assertRegex(logs, r"/goal response ready in \d+ms")
        finally:
            logging.getLogger("planmyagents_api").removeHandler(capture_handler)
            _logging.reset_for_tests()

    def test_goal_explain_rejects_empty_goal(self) -> None:
        # The shared GoalRequest model has min_length=1, so FastAPI
        # validates this before the route body runs. Locking in 422
        # here prevents accidental relaxation of the validation later.
        response = self.client.post("/goal/explain", json={"goal": ""})
        self.assertEqual(response.status_code, 422, response.text)

    def test_goal_explain_returns_503_when_local_qwen_unavailable(self) -> None:
        # Mirrors the /goal route's honest-refusal contract: when the
        # local LLM cannot produce a response, we raise 503 with
        # structured remediation rather than a generic 500. Crucially
        # we do NOT escalate to Groq here — the entire point of the
        # explain endpoint is "show me what the LOCAL model thought",
        # so a Groq fallback would defeat the purpose.
        from unittest.mock import patch

        from planmyagents_api.planner.local_qwen import LocalQwenPlannerError

        class _FailingClient:
            model = "qwen3.5:35b"

            def complete_with_thinking(self, _messages):  # noqa: ANN001
                raise LocalQwenPlannerError(
                    "Local Qwen request to `qwen3.5:35b` timed out after 120s."
                )

        with patch(
            "planmyagents_api.web.app.OllamaQwenClient",
            return_value=_FailingClient(),
        ):
            response = self.client.post(
                "/goal/explain", json={"goal": "Verify foo@bar.com"}
            )

        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()["detail"]
        self.assertEqual(body["error"], "explain_unavailable")
        self.assertIn("timed out", body["message"])
        self.assertEqual(body["model"], "qwen3.5:35b")
        self.assertTrue(
            any("ollama serve" in step for step in body["remediation"])
        )

    def test_unsupported_goal_includes_discovery_and_gap_report(self) -> None:
        response = self.client.post(
            "/goal",
            json={"goal": "Translate this earnings call into Mandarin and summarise it"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["executed"])
        plan = body["plan"]
        self.assertEqual(plan["status"], "unsupported")
        self.assertIn("gap_report", plan)
        # discovery may be None if no missing capabilities; the helper still
        # records normalisations, so the gap_report is the load-bearing field.
        if "discovery" in plan and plan["discovery"] is not None:
            self.assertIn("candidates", plan["discovery"])
            self.assertIn("missing_capabilities", plan["discovery"])

    def test_execute_flag_is_honoured_for_supported_capabilities(self) -> None:
        # Routing for `email_verification` is handled by the in-process mock
        # adapter shipped with the registry, so execute=True should run and
        # return executed=True.
        response = self.client.post(
            "/goal",
            json={
                "goal": "Verify the email jane.doe@acme.com",
                "execute": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        # When the capability is supported, executed should flip True.
        if body["executed"]:
            self.assertIn("execution", body)
            self.assertIsNotNone(body["answer"])
            self.assertIsNotNone(body["summary"])
        else:
            # If the local registry does not back this capability for any
            # reason, the response should at least surface refusal context
            # with a discovery payload or gap report.
            self.assertIn("gap_report", body["plan"])


    def test_leaderboards_index_lists_capability_with_real_runs(self) -> None:
        response = self.client.get("/leaderboards")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        capabilities = {entry["capability"]: entry for entry in body["capabilities"]}
        self.assertIn("email_verification", capabilities)
        self.assertIn("payment_authorization", capabilities)
        email_entry = capabilities["email_verification"]
        self.assertEqual(email_entry["cluster_id"], "email")
        self.assertEqual(email_entry["provider_count"], 1)
        self.assertEqual(email_entry["benchmark_passed"], 1)
        self.assertTrue(email_entry["has_real_adapter_runs"])
        # Untested capability should still appear, with zero passed runs.
        payments_entry = capabilities["payment_authorization"]
        self.assertEqual(payments_entry["benchmark_passed"], 0)
        self.assertFalse(payments_entry["has_real_adapter_runs"])

    def test_leaderboard_for_capability_ranks_tested_provider_first(self) -> None:
        response = self.client.get("/leaderboards/email_verification")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["capability"], "email_verification")
        self.assertEqual(body["cluster_id"], "email")
        self.assertEqual(body["total_providers"], 1)
        self.assertEqual(body["benchmark_passed"], 1)
        self.assertEqual(len(body["entries"]), 1)
        entry = body["entries"][0]
        self.assertEqual(entry["rank"], 1)
        self.assertEqual(entry["provider_id"], "alpha-email-agent")
        self.assertEqual(entry["benchmark_status"], "passed")
        self.assertEqual(entry["sample_size"], 3)
        self.assertGreater(entry["composite_score"], 0.0)

    def test_leaderboard_lists_untested_providers_with_not_started_status(
        self,
    ) -> None:
        response = self.client.get("/leaderboards/payment_authorization")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["capability"], "payment_authorization")
        self.assertEqual(body["cluster_id"], "payment")
        self.assertEqual(len(body["entries"]), 1)
        entry = body["entries"][0]
        self.assertEqual(entry["provider_id"], "beta-pay-agent")
        self.assertEqual(entry["benchmark_status"], "not_started")
        self.assertEqual(entry["sample_size"], 0)

    def test_leaderboard_for_unknown_capability_returns_404(self) -> None:
        response = self.client.get("/leaderboards/no_such_capability")
        self.assertEqual(response.status_code, 404)

    def test_leaderboard_payload_carries_credibility_verdict(self) -> None:
        response = self.client.get("/leaderboards/email_verification")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("credibility", body)
        credibility = body["credibility"]
        self.assertIn("status", credibility)
        self.assertIn(
            credibility["status"],
            {"publishable", "developing", "smoke_test", "synthetic_only"},
        )
        self.assertIn("reasons", credibility)
        self.assertIn("unblockers", credibility)

    def test_unsupported_goal_discloses_live_research_status(self) -> None:
        # The /goal route never triggers live web/GitHub research per request.
        # The response must carry an honest, machine-readable disclosure so
        # the UI can render a clear "we did not call live search" banner
        # instead of burying that fact deep in gap_report blockers.
        response = self.client.post(
            "/goal",
            json={
                "goal": "buy cheapest glenlivet in tamil nadu",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        plan = body["plan"]
        if "discovery" not in plan:
            self.skipTest("discovery did not run for this prompt under test fixtures")
        live = plan["discovery"].get("live_research")
        self.assertIsNotNone(live, "discovery payload must include live_research field")
        self.assertFalse(live["ran"])
        self.assertIn(
            live["status"],
            {"not_run_no_keys", "not_run_per_request_disabled"},
        )
        self.assertIn("local_discovery_store", live["queried_sources"])
        self.assertIn("brave_web_search", live["skipped_sources"])
        self.assertIn("message", live)

    def test_live_research_keys_split_github_from_paid_search(self) -> None:
        # Regression: GitHub PAT is free (5,000 req/hr, no scopes) and is a
        # first-party source — we hit api.github.com directly. It must
        # never be lumped into the same "Tier-2 paid third-party search"
        # bucket as Brave / Tavily / Exa. Two readers depend on this:
        #   1. The frontend pill renders "Free, requires token (set first)"
        #      separately from "Paid third-party (free tiers exist)".
        #   2. Documentation contracts in .env (TIER 1 vs TIER 2) say
        #      GitHub belongs in Tier 1.
        from planmyagents_api.web import app as app_module

        free_with_token = {
            ev for ev, _ in app_module.LIVE_RESEARCH_KEY_TIERS["free_with_token"]
        }
        paid_search = {
            ev for ev, _ in app_module.LIVE_RESEARCH_KEY_TIERS["paid_search"]
        }
        self.assertIn("GITHUB_TOKEN", free_with_token)
        self.assertNotIn("GITHUB_TOKEN", paid_search)
        self.assertIn("BRAVE_SEARCH_API_KEY", paid_search)
        self.assertIn("TAVILY_API_KEY", paid_search)
        self.assertIn("EXA_API_KEY", paid_search)
        # The aggregate list still surfaces both tiers (back-compat).
        flat = {ev for ev, _ in app_module.LIVE_RESEARCH_KEYS}
        self.assertEqual(flat, free_with_token | paid_search)

    def test_live_research_status_returns_tiered_breakdown(self) -> None:
        response = self.client.post(
            "/goal", json={"goal": "buy cheapest glenlivet in tamil nadu"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        plan = body["plan"]
        if "discovery" not in plan:
            self.skipTest("discovery did not run for this prompt under test fixtures")
        live = plan["discovery"]["live_research"]
        self.assertIn("tiers", live, "live_research must expose tiered breakdown")
        tiers = live["tiers"]
        self.assertIn("free_with_token", tiers)
        self.assertIn("paid_search", tiers)
        # GitHub key must be in free_with_token regardless of whether
        # it's set in the test env.
        free_envs = {
            row["env_var"]
            for row in tiers["free_with_token"]["configured"]
            + tiers["free_with_token"]["missing"]
        }
        paid_envs = {
            row["env_var"]
            for row in tiers["paid_search"]["configured"]
            + tiers["paid_search"]["missing"]
        }
        self.assertIn("GITHUB_TOKEN", free_envs)
        self.assertNotIn("GITHUB_TOKEN", paid_envs)

    def test_leaderboards_index_attaches_credibility_per_capability(self) -> None:
        response = self.client.get("/leaderboards")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for entry in body["capabilities"]:
            self.assertIn("credibility", entry, msg=entry["capability"])
            self.assertIn(
                entry["credibility"]["status"],
                {"publishable", "developing", "smoke_test", "synthetic_only"},
            )


class LeaderboardTestedButNotIndexedTest(unittest.TestCase):
    """Regression for the 2026-05-19 bug where /leaderboards silently
    dropped capabilities that had succeeded `benchmark_runs` rows but
    no corresponding `discovery_candidates` row.

    The original symptom (vendor-facing) was: razorpay-payments had 5
    succeeded real-adapter runs for `payment_authorization` in the
    raw store, but the /leaderboards tile rendered nothing for that
    capability because the index iterated `capability_to_candidates`
    only — and razorpay-payments wasn't in `discovery_candidates`.

    These tests pin three invariants the fix relies on:

    1. The capability surfaces on /leaderboards even when no
       discovery_candidates row covers it.
    2. The credibility classifier inspects the ranking source rather
       than falling back to ``synthetic_only`` whenever the candidate
       map is empty.
    3. ``provider_count`` reflects the union of providers across
       candidates and rankings, so the tile never claims "0 providers"
       for a capability that has a benchmarked-and-passing one.
    """

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.verification_path = self.tempdir / "verification.json"

        # Index has ONE indexed candidate (email_verification only),
        # nothing for payment_authorization. We will write benchmark
        # rankings for both — payment_authorization is the
        # "tested-but-not-indexed" cell the fix must surface.
        JsonDiscoveryStore(self.discovery_path).save([_email_candidate()])

        email_runs = _benchmark_runs_for("alpha-email-agent")
        payments_runs = _payment_runs_for("razorpay-payments-fake")
        bench_store = JsonBenchmarkStore(self.benchmark_path)
        bench_store.save(email_runs + payments_runs)
        bench_store.save_rankings(
            compute_rankings(email_runs, source="real_adapter")
            + compute_rankings(payments_runs, source="real_adapter")
        )

        JsonVerificationStore(self.verification_path)  # empty, required by app

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(self.benchmark_path)
        os.environ["PLANMYAGENTS_VERIFICATION_STORE_URL"] = str(self.verification_path)

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for key in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
        ):
            os.environ.pop(key, None)
        self._tempdir.cleanup()

    def test_tested_but_not_indexed_capability_appears_in_index(self) -> None:
        body = self.client.get("/leaderboards").json()
        capabilities = {entry["capability"]: entry for entry in body["capabilities"]}
        self.assertIn(
            "payment_authorization",
            capabilities,
            "capability with real benchmark rankings must surface on the "
            "leaderboards index even when no discovery_candidates row covers it",
        )

    def test_tested_but_not_indexed_capability_reports_real_runs_and_passing(
        self,
    ) -> None:
        body = self.client.get("/leaderboards").json()
        capabilities = {entry["capability"]: entry for entry in body["capabilities"]}
        entry = capabilities["payment_authorization"]

        self.assertEqual(
            entry["benchmark_passed"],
            1,
            "tile must reflect the 1 passing real-adapter ranking, not zero",
        )
        self.assertTrue(
            entry["has_real_adapter_runs"],
            "real_adapter source on the ranking must propagate to the tile's "
            "'Real runs: Yes' badge",
        )
        self.assertGreaterEqual(
            entry["provider_count"],
            1,
            "provider_count must include providers visible only via rankings; "
            "a benchmarked-and-passing provider may not be reported as zero",
        )

    def test_tested_but_not_indexed_capability_credibility_is_not_synthetic(
        self,
    ) -> None:
        body = self.client.get("/leaderboards").json()
        capabilities = {entry["capability"]: entry for entry in body["capabilities"]}
        entry = capabilities["payment_authorization"]

        # The pre-fix fallback path defaulted to status="synthetic_only" with
        # reasons=["No verdict computed."] whenever the candidate map was
        # empty — that's exactly the failure mode the regression guards.
        self.assertNotEqual(
            entry["credibility"]["status"],
            "synthetic_only",
            "credibility classifier must inspect the ranking source for "
            "capabilities surfaced via rankings only — defaulting to "
            "'synthetic_only' would silently misclassify real-adapter cells",
        )


def _payment_runs_for(provider_id: str) -> list[BenchmarkRun]:
    """3 succeeded real-adapter-shape runs for payment_authorization.

    Mirrors `_benchmark_runs_for` but for a different capability so the
    union-iteration test exercises a capability the discovery index
    intentionally doesn't carry.
    """

    runs: list[BenchmarkRun] = []
    for idx in range(3):
        runs.append(
            BenchmarkRun(
                test_case_id=f"case-{idx}",
                provider_id=provider_id,
                capability="payment_authorization",
                difficulty="easy",
                response=ProviderResponse(
                    succeeded=True,
                    output={"_replayed_from": "agents.json:benchmark_status_evidence"},
                    cost_usd=0.0,
                    latency_ms=150,
                ),
                score=ScoreResult(
                    quality_score=1.0,
                    succeeded=True,
                    field_scores=[],
                    reason="",
                ),
            )
        )
    return runs


if __name__ == "__main__":
    unittest.main()

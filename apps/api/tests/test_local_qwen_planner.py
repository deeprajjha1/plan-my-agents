from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.local_qwen import (
    _PLAN_JSON_SCHEMA,
    _PLANNER_SYSTEM_PROMPT,
    LocalQwenPlannerError,
    OllamaQwenClient,
    ThinkingCompletion,
    _validate_plan_payload,
    plan_goal_with_local_qwen,
)
from planmyagents_api.registry.loader import load_registry


class FakeChatClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages: list[dict[str, str]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return json.dumps(self.payload)


class MarkdownJsonChatClient(FakeChatClient):
    def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return f"```json\n{json.dumps(self.payload)}\n```"


class LocalQwenPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_registry(ROOT / "packages" / "registry" / "agents.json")

    def test_accepts_executable_plan_using_registered_capabilities(self) -> None:
        client = FakeChatClient(
            {
                "status": "executable",
                "summary": "Find companies and enrich contacts.",
                "sub_tasks": [
                    {
                        "capability": "semantic_search",
                        "description": "Find EU SaaS companies",
                        "inputs": {"query": "EU SaaS companies"},
                    },
                    {
                        "capability": "contact_enrichment",
                        "description": "Find CTO contacts",
                        "inputs": {"title": "CTO"},
                    },
                ],
                "refusal_reasons": [],
                "missing_capabilities": [],
            }
        )

        plan = plan_goal_with_local_qwen(
            "Find CTO emails for EU SaaS companies", self.registry, client=client
        )

        self.assertTrue(plan.executable)
        self.assertEqual(
            ["semantic_search", "contact_enrichment"], [task.capability for task in plan.sub_tasks]
        )
        self.assertIn("registry", client.messages[1]["content"])

    def test_rejects_executable_plan_with_capability_outside_registry(self) -> None:
        # ``crypto_onramp`` deliberately shares no token surface with any
        # current registry slug (contact_enrichment, email_verification,
        # web_scraping, semantic_search, company_data_lookup) so the
        # embedding-soft-match resolver returns no neighbour, and the
        # plan correctly falls into the unsupported branch with the
        # original slug surfaced as a discovery target. We avoid using
        # slugs like ``travel_search`` here because token overlap with
        # ``semantic_search`` produces a soft-match — that's the *new*
        # design and would invalidate this test's intent of exercising
        # the registry-rejection path.
        client = FakeChatClient(
            {
                "status": "executable",
                "summary": "Move USDC across borders.",
                "sub_tasks": [
                    {
                        "capability": "crypto_onramp",
                        "description": "Onramp USD to USDC",
                        "inputs": {"amount_usd": 50000},
                    }
                ],
                "refusal_reasons": [],
                "missing_capabilities": [],
            }
        )

        plan = plan_goal_with_local_qwen(
            "Move USDC for a vendor", self.registry, client=client
        )

        self.assertFalse(plan.executable)
        self.assertIn("crypto_onramp", plan.missing_capabilities)
        self.assertIn("outside the registry", plan.summary)

    def test_parses_markdown_wrapped_json(self) -> None:
        client = MarkdownJsonChatClient(
            {
                "status": "unsupported",
                "summary": "Travel booking is not in the registry.",
                "sub_tasks": [],
                "refusal_reasons": ["No travel providers are registered."],
                "missing_capabilities": ["travel_search", "booking_execution"],
            }
        )

        plan = plan_goal_with_local_qwen("Book me a flight", self.registry, client=client)

        self.assertFalse(plan.executable)
        self.assertEqual(["booking_execution", "travel_search"], plan.missing_capabilities)

    def test_rejects_unresolved_dependency_placeholders(self) -> None:
        client = FakeChatClient(
            {
                "status": "executable",
                "summary": "Find and verify contacts.",
                "sub_tasks": [
                    {
                        "capability": "semantic_search",
                        "description": "Find companies",
                        "inputs": {"query": "EU SaaS companies"},
                    },
                    {
                        "capability": "contact_enrichment",
                        "description": "Find CTO contacts",
                        "inputs": {"company_names": ["from_semantic_search_results"]},
                    },
                ],
                "refusal_reasons": [],
                "missing_capabilities": [],
            }
        )

        plan = plan_goal_with_local_qwen(
            "Find CTO emails for EU SaaS companies", self.registry, client=client
        )

        self.assertFalse(plan.executable)
        self.assertIn("dependency_resolution", plan.missing_capabilities)

    def test_expands_email_verification_email_lists(self) -> None:
        client = FakeChatClient(
            {
                "status": "executable",
                "summary": "Verify emails.",
                "sub_tasks": [
                    {
                        "capability": "email_verification",
                        "description": "Verify supplied emails",
                        "inputs": {"emails": ["jane@acme.com", "bob@example.com"]},
                    }
                ],
                "refusal_reasons": [],
                "missing_capabilities": [],
            }
        )

        plan = plan_goal_with_local_qwen(
            "Verify jane@acme.com and bob@example.com", self.registry, client=client
        )

        self.assertTrue(plan.executable)
        self.assertEqual(
            ["jane@acme.com", "bob@example.com"], [task.inputs["email"] for task in plan.sub_tasks]
        )


def _fake_ollama_response(
    *, content: str | None, thinking: str | None
) -> io.BytesIO:
    """Build a fake Ollama /api/chat response body the same shape that
    request.urlopen returns (.read() yields bytes). Used by
    ``OllamaQwenClient`` tests to avoid touching a real Ollama
    daemon."""

    body: dict[str, object] = {
        "model": "qwen3.5:35b",
        "message": {"role": "assistant"},
        "done": True,
    }
    message = body["message"]
    assert isinstance(message, dict)
    if content is not None:
        message["content"] = content
    if thinking is not None:
        message["thinking"] = thinking
    raw = json.dumps(body).encode("utf-8")
    return io.BytesIO(raw)


class _FakeUrlopenContext:
    """Minimal context-manager wrapper around an in-memory bytes stream
    so ``urlopen(...).read()`` works with our fake response."""

    def __init__(self, stream: io.BytesIO) -> None:
        self.stream = stream

    def __enter__(self) -> io.BytesIO:
        return self.stream

    def __exit__(self, *_: object) -> None:
        return None


class OllamaQwenClientCompleteWithThinkingTests(unittest.TestCase):
    """Cover the on-demand thinking trace path that powers the
    ``/goal/explain`` endpoint and the "Why did the planner pick
    this?" UI flow.

    We mock ``urlopen`` rather than hit a real Ollama daemon so the
    test runs in CI / on dev machines without a model loaded."""

    def test_returns_both_content_and_thinking_for_thinking_models(
        self,
    ) -> None:
        client = OllamaQwenClient()
        response = _FakeUrlopenContext(
            _fake_ollama_response(
                content='{"status": "unsupported"}',
                thinking="Step 1: parse goal. Step 2: pick capabilities.",
            )
        )

        with patch("urllib.request.urlopen", return_value=response):
            result = client.complete_with_thinking(
                [{"role": "user", "content": "go"}]
            )

        self.assertIsInstance(result, ThinkingCompletion)
        self.assertEqual(result.content, '{"status": "unsupported"}')
        self.assertIn("Step 1", result.thinking)
        self.assertTrue(result.model_supports_thinking)
        self.assertGreaterEqual(result.duration_ms, 0)
        self.assertEqual(result.model, client.model)

    def test_marks_non_thinking_models_explicitly(self) -> None:
        # Older / non-reasoning models (qwen2.5, llama3.x) silently
        # ignore the `think` flag and return an empty thinking field.
        # The flag on ThinkingCompletion lets the UI show "model
        # doesn't support thinking" instead of an empty trace panel —
        # crucial so users don't think the API is broken.
        client = OllamaQwenClient()
        response = _FakeUrlopenContext(
            _fake_ollama_response(
                content='{"status": "supported"}',
                thinking="",
            )
        )

        with patch("urllib.request.urlopen", return_value=response):
            result = client.complete_with_thinking(
                [{"role": "user", "content": "go"}]
            )

        self.assertEqual(result.thinking, "")
        self.assertFalse(result.model_supports_thinking)
        self.assertEqual(result.content, '{"status": "supported"}')

    def test_request_payload_forces_think_true_regardless_of_instance(
        self,
    ) -> None:
        # The whole point of complete_with_thinking is that it forces
        # the model to emit a reasoning trace even when the instance
        # default (driven by PLANMYAGENTS_QWEN_THINK) is False. If
        # this regresses, the explain endpoint silently returns empty
        # thinking — exactly the failure mode the endpoint exists to
        # prevent.
        client = OllamaQwenClient(think=False)
        captured: dict[str, object] = {}

        def fake_urlopen(req: object, timeout: float) -> _FakeUrlopenContext:  # noqa: ARG001
            data = getattr(req, "data", b"")
            captured["payload"] = json.loads(data.decode("utf-8"))
            return _FakeUrlopenContext(
                _fake_ollama_response(
                    content='{"ok": true}', thinking="reasoning"
                )
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.complete_with_thinking([{"role": "user", "content": "go"}])

        self.assertEqual(captured["payload"]["think"], True)
        self.assertEqual(captured["payload"]["format"], "json")

    def test_complete_still_respects_instance_think_flag(self) -> None:
        # Sanity check: the new method must NOT change the behavior of
        # the plain .complete() method, which routes through the same
        # _post helper but passes self.think (so PLANMYAGENTS_QWEN_THINK
        # still controls the production /goal hot path).
        client = OllamaQwenClient(think=False)
        captured: dict[str, object] = {}

        def fake_urlopen(req: object, timeout: float) -> _FakeUrlopenContext:  # noqa: ARG001
            data = getattr(req, "data", b"")
            captured["payload"] = json.loads(data.decode("utf-8"))
            return _FakeUrlopenContext(
                _fake_ollama_response(content='{"ok": true}', thinking=None)
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            content = client.complete([{"role": "user", "content": "go"}])

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(captured["payload"]["think"], False)

    def test_raises_when_model_returns_empty_content(self) -> None:
        client = OllamaQwenClient()
        response = _FakeUrlopenContext(
            _fake_ollama_response(content="", thinking="some reasoning")
        )

        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(LocalQwenPlannerError) as exc_ctx:
                client.complete_with_thinking(
                    [{"role": "user", "content": "go"}]
                )

        self.assertIn("empty planner response", str(exc_ctx.exception))

    def test_http_404_model_not_found_is_actionable(self) -> None:
        from urllib import error as url_error

        class _ModelNotFoundHTTPError(url_error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="http://127.0.0.1:11434/api/chat",
                    code=404,
                    msg="Not Found",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=None,
                )

            def read(self) -> bytes:  # type: ignore[override]
                return b'{"error":"model \'qwen3.5:35b\' not found"}'

        client = OllamaQwenClient(model="qwen3.5:35b")
        with patch("urllib.request.urlopen", side_effect=_ModelNotFoundHTTPError):
            with self.assertRaises(LocalQwenPlannerError) as exc_ctx:
                client.complete([{"role": "user", "content": "go"}])

        message = str(exc_ctx.exception)
        self.assertIn("not installed", message)
        self.assertIn("ollama pull qwen3.5:35b", message)
        self.assertNotIn("cannot reach Ollama", message)


class _RetryThenSucceedClient:
    """Test double that fails the first ``fail_count`` calls with the
    payload from ``bad_payloads`` and then returns ``good_payload``.

    Used to exercise the validation-retry loop without hitting a real
    LLM. Captures every messages list it received so tests can assert
    the retry actually fed the validation errors back to the model."""

    def __init__(
        self,
        bad_payloads: list[dict | str],
        good_payload: dict | None = None,
    ) -> None:
        self.bad_payloads = list(bad_payloads)
        self.good_payload = good_payload
        self.messages_seen: list[list[dict[str, str]]] = []
        self.call_count = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages_seen.append(list(messages))
        self.call_count += 1
        if self.bad_payloads:
            payload = self.bad_payloads.pop(0)
            if isinstance(payload, str):
                return payload
            return json.dumps(payload)
        if self.good_payload is None:
            raise AssertionError("test client had no remaining payloads")
        return json.dumps(self.good_payload)


class PlannerSystemPromptTests(unittest.TestCase):
    """Regression tests on the system prompt itself.

    The whole reason the planner is being domain-agnostic is that the
    previous prompt embedded six handwritten "Decomposition heuristics"
    buckets and four worked examples in regulated finance / travel.
    These tests assert those phrases are gone and stay gone — if a
    future edit reintroduces any of them, CI fails before the bias
    reaches production."""

    FORBIDDEN_PHRASES = (
        # Heuristic-bucket headings from the old prompt.
        "Decomposition heuristics",
        "Cross-border or regulated transactions",
        "Real-estate or large-asset purchases",
        "Crypto / digital-asset payments",
        "Immigration / residency",
        "Multi-currency commerce",
        # Worked example signals.
        "Worked examples",
        "Help me buy a house in Dubai",
        "UAE Golden Visa",
        "round-trip flight NYC",
        # Capability slugs the old prompt was hand-feeding the model —
        # if these reappear in the prompt it means someone re-added a
        # domain hint.
        "kyc_aml_check",
        "vasp_compliance_check",
        "fare_comparison",
    )

    def test_prompt_is_free_of_domain_hints(self) -> None:
        for phrase in self.FORBIDDEN_PHRASES:
            self.assertNotIn(
                phrase,
                _PLANNER_SYSTEM_PROMPT,
                f"Domain hint reintroduced into planner prompt: {phrase!r}",
            )

    def test_prompt_states_open_world_capability_contract(self) -> None:
        # The prompt MUST tell the LLM that capability slugs are open-world
        # (any descriptive snake_case). Without this, the model conservatively
        # tries to match a closed list and returns nothing for novel domains.
        self.assertIn("snake_case", _PLANNER_SYSTEM_PROMPT)
        self.assertIn("OPEN", _PLANNER_SYSTEM_PROMPT)


class PlanValidatorTests(unittest.TestCase):
    """Direct tests on ``_validate_plan_payload``. The validator is the
    contract; covering it directly catches schema regressions long
    before they reach a live LLM call."""

    def test_accepts_minimal_executable_plan(self) -> None:
        errors = _validate_plan_payload(
            {
                "status": "executable",
                "summary": "do the thing",
                "sub_tasks": [
                    {"capability": "semantic_search", "description": "find them"}
                ],
            },
            _PLAN_JSON_SCHEMA,
        )
        self.assertEqual([], errors)

    def test_accepts_minimal_unsupported_plan(self) -> None:
        # Unsupported plans don't require sub_tasks (because the goal isn't
        # routable). This is encoded via additional_required_when.
        errors = _validate_plan_payload(
            {"status": "unsupported", "summary": "no providers"},
            _PLAN_JSON_SCHEMA,
        )
        self.assertEqual([], errors)

    def test_rejects_missing_status(self) -> None:
        errors = _validate_plan_payload(
            {"summary": "do the thing"}, _PLAN_JSON_SCHEMA
        )
        self.assertTrue(any("'status'" in e for e in errors))

    def test_rejects_invalid_status_enum(self) -> None:
        errors = _validate_plan_payload(
            {"status": "maybe", "summary": "x"}, _PLAN_JSON_SCHEMA
        )
        self.assertTrue(any("not in enum" in e for e in errors))

    def test_rejects_executable_without_sub_tasks(self) -> None:
        # Conditional required: status==executable means sub_tasks must
        # be present and non-empty. A status==executable without sub_tasks
        # is the LLM lying about feasibility.
        errors = _validate_plan_payload(
            {"status": "executable", "summary": "x"}, _PLAN_JSON_SCHEMA
        )
        self.assertTrue(
            any("sub_tasks" in e and "non-empty" in e for e in errors),
            msg=f"expected sub_tasks-non-empty error, got {errors}",
        )

    def test_rejects_executable_with_empty_sub_tasks(self) -> None:
        errors = _validate_plan_payload(
            {"status": "executable", "summary": "x", "sub_tasks": []},
            _PLAN_JSON_SCHEMA,
        )
        self.assertTrue(any("non-empty" in e for e in errors))

    def test_rejects_sub_task_missing_capability(self) -> None:
        errors = _validate_plan_payload(
            {
                "status": "executable",
                "summary": "x",
                "sub_tasks": [{"description": "do it"}],
            },
            _PLAN_JSON_SCHEMA,
        )
        self.assertTrue(any("'capability'" in e for e in errors))

    def test_rejects_non_object_root(self) -> None:
        errors = _validate_plan_payload(["hi"], _PLAN_JSON_SCHEMA)
        self.assertTrue(any("expected object" in e for e in errors))

    def test_tolerates_extra_unknown_fields(self) -> None:
        # We want the LLM to be free to include debug/metadata fields
        # without us having to schema-list them. Validator should ignore.
        errors = _validate_plan_payload(
            {
                "status": "unsupported",
                "summary": "x",
                "debug_field": {"foo": 1},
                "any_extra": [1, 2, 3],
            },
            _PLAN_JSON_SCHEMA,
        )
        self.assertEqual([], errors)


class PlannerValidationRetryTests(unittest.TestCase):
    """End-to-end tests of the validation-retry loop inside
    ``plan_goal_with_local_qwen``."""

    def setUp(self) -> None:
        self.registry = load_registry(
            ROOT / "packages" / "registry" / "agents.json"
        )

    def test_retries_once_on_invalid_first_response_then_succeeds(self) -> None:
        # First call: missing required `summary`. Second call: valid.
        # The retry path must capture validation errors into the messages
        # list it sends on the second call so the model can correct.
        good = {
            "status": "executable",
            "summary": "valid plan",
            "sub_tasks": [
                {
                    "capability": "semantic_search",
                    "description": "search",
                    "inputs": {},
                }
            ],
        }
        client = _RetryThenSucceedClient(
            bad_payloads=[{"status": "executable"}],
            good_payload=good,
        )

        plan = plan_goal_with_local_qwen("do something", self.registry, client=client)

        self.assertTrue(plan.executable)
        self.assertEqual(client.call_count, 2)
        # The retry message must include the validator's error feedback
        # so the model knows what to fix. We assert on the keyword
        # "Validation errors" which the feedback formatter writes.
        retry_messages = client.messages_seen[1]
        retry_user_text = retry_messages[-1]["content"]
        self.assertIn("Validation errors", retry_user_text)

    def test_raises_after_exhausting_retries(self) -> None:
        # Both calls return invalid payloads. The loop runs
        # _PLANNER_VALIDATION_RETRIES + 1 times then raises.
        client = _RetryThenSucceedClient(
            bad_payloads=[{"status": "executable"}, {"status": "executable"}],
        )

        with self.assertRaises(LocalQwenPlannerError) as exc_ctx:
            plan_goal_with_local_qwen("do something", self.registry, client=client)

        self.assertIn("validation", str(exc_ctx.exception).lower())
        self.assertEqual(client.call_count, 2)

    def test_retries_on_non_json_then_succeeds(self) -> None:
        # First call returns garbage that JSON parsing can't recover.
        # The loop should log a JSON-parse warning, append feedback,
        # and retry — same path as a schema failure.
        good = {
            "status": "unsupported",
            "summary": "no providers",
        }
        client = _RetryThenSucceedClient(
            bad_payloads=["sorry I can't help with that today"],
            good_payload=good,
        )

        plan = plan_goal_with_local_qwen("anything", self.registry, client=client)

        self.assertFalse(plan.executable)
        self.assertEqual(client.call_count, 2)


class PlannerCapabilitySoftMatchTests(unittest.TestCase):
    """Lock the integration between ``_plan_from_payload`` and
    ``CapabilityIndex.match_slug``. The planner is the single largest
    consumer of soft matching; if this regresses, the whole "planner
    emits open-world slugs" contract degrades silently into "planner
    must hit registry exactly", which is the bug Slice 1 was meant to
    eliminate."""

    def setUp(self) -> None:
        self.registry = load_registry(
            ROOT / "packages" / "registry" / "agents.json"
        )

    def test_exact_slug_still_resolves_to_executable(self) -> None:
        # Baseline: the exact-match path must be untouched. A planner
        # that emits a registry-byte-identical slug should still produce
        # an executable plan with no resolver intervention.
        client = FakeChatClient(
            {
                "status": "executable",
                "summary": "Run a search.",
                "sub_tasks": [
                    {
                        "capability": "semantic_search",
                        "description": "Find candidates",
                        "inputs": {"query": "EU SaaS"},
                    }
                ],
            }
        )

        plan = plan_goal_with_local_qwen("find candidates", self.registry, client=client)

        self.assertTrue(plan.executable)
        self.assertEqual(["semantic_search"], [t.capability for t in plan.sub_tasks])

    def test_unresolvable_slug_falls_into_missing_capabilities(self) -> None:
        # A slug that the embedder cannot match to any registry id (the
        # default DeterministicHashEmbedder + 0.78 threshold rarely
        # produces matches) must drop the goal to unsupported and
        # surface the original slug in missing_capabilities so the
        # discovery layer has a real search target.
        client = FakeChatClient(
            {
                "status": "executable",
                "summary": "Use a wholly novel capability.",
                "sub_tasks": [
                    {
                        "capability": "vendor_kyb_check",
                        "description": "Run KYB on the vendor",
                        "inputs": {},
                    }
                ],
            }
        )

        plan = plan_goal_with_local_qwen(
            "screen a vendor", self.registry, client=client
        )

        self.assertFalse(plan.executable)
        self.assertIn("vendor_kyb_check", plan.missing_capabilities)

    def test_missing_capabilities_are_not_rewritten_by_resolver(self) -> None:
        # On the unsupported path, missing_capabilities from the LLM are
        # preserved verbatim (they're discovery targets — rewriting them
        # to a near-but-wrong registry slug would mask the real gap).
        # This is a deliberate asymmetry vs. sub_tasks resolution.
        client = FakeChatClient(
            {
                "status": "unsupported",
                "summary": "Need crypto onramp.",
                "missing_capabilities": [
                    "crypto_onramp",
                    "stablecoin_transfer",
                ],
                "refusal_reasons": ["No registered crypto provider."],
            }
        )

        plan = plan_goal_with_local_qwen(
            "send USDC to vendor", self.registry, client=client
        )

        self.assertFalse(plan.executable)
        self.assertEqual(
            ["crypto_onramp", "stablecoin_transfer"],
            sorted(plan.missing_capabilities),
        )


class PlannerMessagesPayloadTests(unittest.TestCase):
    """Lock the contract between the planner and the LLM."""

    def setUp(self) -> None:
        self.registry = load_registry(
            ROOT / "packages" / "registry" / "agents.json"
        )

    def test_user_message_includes_output_schema(self) -> None:
        # The schema travels in the user message so the model sees the
        # exact contract it must satisfy on every call. Without this the
        # validation-retry loop would still kick in but the model has no
        # structured ground truth on what shape we want.
        client = _RetryThenSucceedClient(
            bad_payloads=[],
            good_payload={
                "status": "unsupported",
                "summary": "no providers",
            },
        )

        plan_goal_with_local_qwen("anything", self.registry, client=client)

        user_text = client.messages_seen[0][1]["content"]
        self.assertIn("output_schema", user_text)
        self.assertIn("status", user_text)
        self.assertIn("missing_capabilities", user_text)


if __name__ == "__main__":
    unittest.main()

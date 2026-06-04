"""Unit tests for the human-fallback suggester.

Covers:

* Happy path — LLM returns a well-formed payload, caller gets a
  ``HumanFallbackSuggestion`` with validated alternatives.
* URL validation — http/https only, must have a host with a dot;
  hallucinated bare-hostnames / mailto / file URLs are dropped.
* Host-level dedup — the same site under www / non-www variants
  collapses to one entry; the first occurrence wins to mirror the
  prompt's "rank by best fit" contract.
* Hard cap on returned entries — even when the model returns 20+
  alternatives we never emit more than ``HARD_CAP_ALTERNATIVES_RETURNED``.
* Empty alternatives — model parses but every URL is invalid; result
  carries ``status="empty"`` so the UI can show the soft fallback
  panel instead of the alternatives list.
* Empty missing_capabilities — the suggester short-circuits with
  ``status="skipped_no_capabilities"`` and never invokes the LLM.
* LLM unavailability — caller gets ``status="unavailable"`` and the
  reason text. Never raises.
* Malformed JSON — same degradation path as LLM failure.
* Prompt contract — no domain bias enumerated, system prompt asks for
  real public sites only and forbids fabricated URLs.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.llm.escalating_client import (
    EscalationMetadata,
    NoLlmTierAvailableError,
)
from planmyagents_api.planner.human_fallback_suggester import (
    HARD_CAP_ALTERNATIVES_RETURNED,
    HumanFallbackSuggestion,
    suggest_human_alternatives,
    suggester_enabled,
)


class _FakeChatClient:
    """Returns a pre-baked JSON payload and records calls for
    prompt-contract assertions."""

    def __init__(self, payload: dict | str) -> None:
        if isinstance(payload, dict):
            self._content = json.dumps(payload)
        else:
            self._content = payload
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self._content


class _FailingChatClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, _messages: list[dict[str, str]]) -> str:
        raise self._exc


class HappyPathTests(unittest.TestCase):
    def test_returns_validated_alternatives_in_declaration_order(self) -> None:
        client = _FakeChatClient(
            {
                "alternatives": [
                    {
                        "url": "https://livingliquidz.com",
                        "name": "Living Liquidz",
                        "why": "India-wide retailer with browseable inventory.",
                    },
                    {
                        "url": "https://tonique.in",
                        "name": "Tonique",
                        "why": "Bangalore retailer; prices listed online.",
                    },
                ]
            }
        )

        result = suggest_human_alternatives(
            goal="find liquor stores in southern india",
            missing_capabilities=["store_locator", "price_comparison"],
            client=client,
        )

        self.assertIsInstance(result, HumanFallbackSuggestion)
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(result.alternatives), 2)
        # Declaration order is preserved — the prompt asked the model
        # to rank best-fit-first and we trust that ordering downstream.
        self.assertEqual(result.alternatives[0].name, "Living Liquidz")
        self.assertEqual(result.alternatives[1].name, "Tonique")
        self.assertEqual(
            result.alternatives[0].url, "https://livingliquidz.com"
        )
        self.assertEqual(len(client.calls), 1)

    def test_includes_user_facing_step_text_when_decomposed(self) -> None:
        """When the decomposer ran, the suggester must include the
        per-capability ``user_facing_step`` in the user message so the
        model can reason about intent rather than slug shape."""

        client = _FakeChatClient({"alternatives": []})
        suggest_human_alternatives(
            goal="ship invitation emails",
            missing_capabilities=["bulk_email_dispatch"],
            decomposed_sub_tasks=[
                {
                    "suggested_capability_id": "bulk_email_dispatch",
                    "user_facing_step": "Send a launch announcement to my list",
                    "description": "Bulk email send to opt-in subscribers.",
                }
            ],
            client=client,
        )

        self.assertEqual(len(client.calls), 1)
        user_msg = client.calls[0][1]
        self.assertEqual(user_msg["role"], "user")
        payload = json.loads(user_msg["content"])
        self.assertEqual(
            payload["missing_capabilities"][0]["user_facing_step"],
            "Send a launch announcement to my list",
        )


class UrlValidationTests(unittest.TestCase):
    def test_drops_alternatives_with_non_http_scheme(self) -> None:
        client = _FakeChatClient(
            {
                "alternatives": [
                    {
                        "url": "mailto:hello@example.com",
                        "name": "Mailto",
                        "why": "...",
                    },
                    {
                        "url": "file:///local",
                        "name": "Local file",
                        "why": "...",
                    },
                    {
                        "url": "https://example.com",
                        "name": "Example",
                        "why": "Real site.",
                    },
                ]
            }
        )

        result = suggest_human_alternatives(
            goal="anything",
            missing_capabilities=["x"],
            client=client,
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(result.alternatives), 1)
        self.assertEqual(result.alternatives[0].url, "https://example.com")

    def test_drops_alternatives_with_no_host(self) -> None:
        client = _FakeChatClient(
            {
                "alternatives": [
                    {"url": "https:///just-path", "name": "Bad", "why": "..."},
                    {"url": "https://nodot", "name": "NoDot", "why": "..."},
                    {"url": "not a url at all", "name": "Junk", "why": "..."},
                ]
            }
        )
        result = suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        # Every URL was malformed; the model "ran" but produced nothing
        # validated. Surface the empty case so the UI can render the
        # softer "we tried but couldn't find any" panel.
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.alternatives, [])

    def test_drops_alternatives_with_missing_name(self) -> None:
        """Without a brand label, the link looks like spam in the UI.
        Better to drop than to synthesise a label from the host."""

        client = _FakeChatClient(
            {
                "alternatives": [
                    {"url": "https://example.com", "name": "", "why": "..."},
                    {
                        "url": "https://other.com",
                        "name": "Other",
                        "why": "Real one.",
                    },
                ]
            }
        )
        result = suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        self.assertEqual(len(result.alternatives), 1)
        self.assertEqual(result.alternatives[0].name, "Other")


class HostDedupTests(unittest.TestCase):
    def test_dedupes_www_and_non_www_variants(self) -> None:
        client = _FakeChatClient(
            {
                "alternatives": [
                    {
                        "url": "https://example.com",
                        "name": "Example (no www)",
                        "why": "First occurrence wins.",
                    },
                    {
                        "url": "https://www.example.com/path",
                        "name": "Example (with www)",
                        "why": "Should be dropped as duplicate host.",
                    },
                    {
                        "url": "https://other.com",
                        "name": "Other",
                        "why": "Different host, kept.",
                    },
                ]
            }
        )
        result = suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        # First occurrence wins so the kept one is "Example (no www)".
        names = [alt.name for alt in result.alternatives]
        self.assertEqual(len(result.alternatives), 2)
        self.assertIn("Example (no www)", names)
        self.assertIn("Other", names)
        self.assertNotIn("Example (with www)", names)


class HardCapTests(unittest.TestCase):
    def test_never_returns_more_than_hard_cap(self) -> None:
        # Model misbehaves and emits 20 alternatives. We must cap at
        # HARD_CAP_ALTERNATIVES_RETURNED regardless.
        client = _FakeChatClient(
            {
                "alternatives": [
                    {
                        "url": f"https://example-{i}.com",
                        "name": f"Example {i}",
                        "why": "Real-looking entry.",
                    }
                    for i in range(20)
                ]
            }
        )
        result = suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(
            len(result.alternatives), HARD_CAP_ALTERNATIVES_RETURNED
        )


class ShortCircuitTests(unittest.TestCase):
    def test_empty_capabilities_skips_llm_call(self) -> None:
        # Use a client that would explode if invoked — proves the
        # short-circuit happens before the LLM is reached.
        client = _FailingChatClient(RuntimeError("must not be called"))
        result = suggest_human_alternatives(
            goal="anything", missing_capabilities=[], client=client
        )
        self.assertEqual(result.status, "skipped_no_capabilities")
        self.assertEqual(result.alternatives, [])

    def test_blank_capability_strings_treated_as_empty(self) -> None:
        client = _FailingChatClient(RuntimeError("must not be called"))
        result = suggest_human_alternatives(
            goal="anything",
            missing_capabilities=["  ", ""],
            client=client,
        )
        self.assertEqual(result.status, "skipped_no_capabilities")


class FailureTests(unittest.TestCase):
    def test_no_llm_tier_available_yields_unavailable(self) -> None:
        meta = EscalationMetadata(
            tier_used="none",
            primary_label="qwen",
            primary_attempted=True,
            primary_error="connection refused",
        )
        client = _FailingChatClient(
            NoLlmTierAvailableError(meta, message="all tiers down")
        )

        result = suggest_human_alternatives(
            goal="x",
            missing_capabilities=["x"],
            client=client,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertIn("all tiers down", result.reason)
        self.assertEqual(result.alternatives, [])

    def test_malformed_response_yields_unavailable(self) -> None:
        # Not JSON at all. The tolerant parser bails and the suggester
        # returns ``unavailable`` so the UI degrades the same way it
        # would for an LLM tier outage.
        client = _FakeChatClient("this is just prose, no JSON here")
        result = suggest_human_alternatives(
            goal="x",
            missing_capabilities=["x"],
            client=client,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.alternatives, [])

    def test_payload_missing_alternatives_key_yields_unavailable(self) -> None:
        client = _FakeChatClient({"results": []})  # wrong key
        result = suggest_human_alternatives(
            goal="x",
            missing_capabilities=["x"],
            client=client,
        )
        self.assertEqual(result.status, "unavailable")

    def test_response_with_markdown_fence_is_parsed(self) -> None:
        # The tolerant parser strips ```json fences as the reconciler
        # does; matching shape avoids surprises when the same hosted
        # model wraps both surfaces' output.
        client = _FakeChatClient(
            '```json\n{"alternatives": [{"url": "https://example.com",'
            ' "name": "Example", "why": "Real."}]}\n```'
        )
        result = suggest_human_alternatives(
            goal="x",
            missing_capabilities=["x"],
            client=client,
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(result.alternatives), 1)


class PromptContractTests(unittest.TestCase):
    def test_system_prompt_forbids_fabricated_urls(self) -> None:
        client = _FakeChatClient({"alternatives": []})
        suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        system_msg = client.calls[0][0]["content"]
        self.assertIn("real", system_msg.lower())
        self.assertIn("public", system_msg.lower())
        self.assertIn("do not invent", system_msg.lower())

    def test_system_prompt_demands_strict_json(self) -> None:
        client = _FakeChatClient({"alternatives": []})
        suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        system_msg = client.calls[0][0]["content"]
        # Same anti-prose stance as the decomposer/reconciler — the
        # tolerant parser handles markdown fences but the prompt
        # forbids them anyway so the happy path is clean.
        self.assertIn("strict json", system_msg.lower())
        self.assertIn("no markdown", system_msg.lower())

    def test_system_prompt_does_not_enumerate_domain_examples(self) -> None:
        """Same anti-bias principle as the decomposer/reconciler: the
        prompt must NOT enumerate "for X domain, suggest Y site" hints
        that would steer the model toward whatever vertical the
        examples named."""

        client = _FakeChatClient({"alternatives": []})
        suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        system_msg = client.calls[0][0]["content"].lower()
        # No vertical-specific brand mentions allowed in the prompt.
        for forbidden in (
            "yelp",
            "google maps",
            "amazon",
            "stripe",
            "perplexity",
            "living liquidz",
        ):
            self.assertNotIn(
                forbidden, system_msg,
                f"system prompt must not name vendor '{forbidden}' (biases output)",
            )


class EnvFlagTests(unittest.TestCase):
    def test_suggester_enabled_default_is_true(self) -> None:
        import os

        # Ensure no override is present from outer test env.
        prev = os.environ.pop("PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER", None)
        try:
            self.assertTrue(suggester_enabled())
        finally:
            if prev is not None:
                os.environ["PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER"] = prev

    def test_off_values_disable_suggester(self) -> None:
        import os

        for value in ("off", "0", "false", "no", "DISABLED"):
            prev = os.environ.get("PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER")
            os.environ["PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER"] = value
            try:
                self.assertFalse(
                    suggester_enabled(),
                    f"value {value!r} should disable the suggester",
                )
            finally:
                if prev is None:
                    os.environ.pop("PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER")
                else:
                    os.environ["PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER"] = prev


class JsonRoundTripTests(unittest.TestCase):
    def test_to_json_shape_matches_wire_contract(self) -> None:
        client = _FakeChatClient(
            {
                "alternatives": [
                    {
                        "url": "https://example.com",
                        "name": "Example",
                        "why": "Real public site.",
                    }
                ]
            }
        )
        result = suggest_human_alternatives(
            goal="x", missing_capabilities=["x"], client=client
        )
        payload = result.to_json()
        # Stable wire contract — the frontend reads exactly these
        # keys. If we ever rename one we want a test failure here so
        # the type drift is caught at the API surface.
        self.assertEqual(set(payload.keys()), {"status", "reason", "alternatives"})
        self.assertEqual(
            set(payload["alternatives"][0].keys()), {"url", "name", "why"}
        )


if __name__ == "__main__":
    unittest.main()

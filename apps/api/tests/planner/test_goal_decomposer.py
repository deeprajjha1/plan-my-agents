"""Unit tests for the LLM-driven goal decomposer.

The decomposer is the single LLM stage that replaces the older
intent_mapper + coverage_audit pair. Tests use a deterministic
``FakeChatClient`` so the prompt contract and parser behaviour are
exercised without touching a real LLM.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.llm.escalating_client import (
    EscalationMetadata,
    NoLlmTierAvailableError,
)
from planmyagents_api.planner.capability_catalog import build_capability_catalog
from planmyagents_api.planner.goal_decomposer import (
    DecomposedSubTask,
    GoalDecomposition,
    GoalDecompositionError,
    decompose_goal,
)
from planmyagents_api.planner.local_qwen import LocalQwenPlannerError


class _FakeChatClient:
    """Minimal stub that returns a pre-baked JSON payload as the
    completion content. Records the messages it was called with for
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


def _car_procurement_payload() -> dict:
    """A representative LLM payload for a research-and-procurement
    style goal. None of the sub-tasks happen to be in the catalog
    used in tests below, so all five are treated as freshly coined.
    Structured as the test expects in the assertions."""
    return {
        "intent_summary": (
            "Research car brands offering corporate fleet discounts in "
            "Bengaluru and decide which is best for the buyer's 100-unit order."
        ),
        "confidence": 0.82,
        "sub_tasks": [
            {
                "description": (
                    "Find which car brands run corporate fleet programs in "
                    "India and what each program offers."
                ),
                "user_facing_step": "Search the web for OEM fleet programs in India.",
                "search_query": "web search API",
                "acceptance_criteria": (
                    "Returns search results from public web sources for arbitrary queries."
                ),
                "suggested_capability_id": "web_search",
            },
            {
                "description": (
                    "Look up the fleet-sales contact at each brand's "
                    "Bengaluru dealership network."
                ),
                "user_facing_step": "Look up dealer contacts for each brand in Bengaluru.",
                "search_query": "company contact enrichment API",
                "acceptance_criteria": (
                    "Resolves a company name + city into one or more named "
                    "contacts with email or phone."
                ),
                "suggested_capability_id": "contact_enrichment",
            },
            {
                "description": (
                    "Fill the OEM fleet inquiry form on each dealer's website "
                    "with the buyer's RFQ details."
                ),
                "user_facing_step": "Fill OEM fleet inquiry forms for each dealer.",
                "search_query": "browser automation MCP",
                "acceptance_criteria": (
                    "Drives a real browser to navigate to a URL, fill fields, "
                    "and submit a form."
                ),
                "suggested_capability_id": "browser_automation",
            },
            {
                "description": (
                    "Email each dealer with the RFQ when no public web form "
                    "is available."
                ),
                "user_facing_step": "Email each dealer with the RFQ.",
                "search_query": "send email API",
                "acceptance_criteria": (
                    "Sends an arbitrary email message via SMTP or a hosted "
                    "transactional email API."
                ),
                "suggested_capability_id": "email_sending",
            },
            {
                "description": (
                    "Aggregate the returned quotes into a single comparison "
                    "table the buyer can review."
                ),
                "user_facing_step": "Compare collected quotes side by side.",
                "search_query": "structured data comparison",
                "acceptance_criteria": (
                    "Accepts multiple structured records and returns a "
                    "side-by-side ranked comparison."
                ),
                "suggested_capability_id": "comparison_tabulation",
            },
        ],
    }


def _commerce_catalog():
    """A minimal capability catalog used to exercise the catalog-hint
    code path. Intentionally has none of the labels the LLM in
    ``_car_procurement_payload`` chose, so we can verify those go
    into ``new_capabilities`` rather than being rejected."""
    return build_capability_catalog(
        [
            normalize_candidate(
                {
                    "id": "commerce-agent",
                    "display_name": "Commerce Agent",
                    "vendor": "Test Directory",
                    "vendor_url": "https://example.com/commerce",
                    "provider_type": "a2a_agent",
                    "capabilities": [
                        {"id": "product_search", "notes": "Discover matching products."},
                        {"id": "price_comparison", "notes": "Compare offers and prices."},
                    ],
                },
                source="test",
            )
        ]
    )


def _hint_catalog_with_existing_label():
    """A capability catalog whose ids overlap one of the labels the
    fake LLM payload returns — used to verify the
    ``catalog_reused_capabilities`` partition."""
    return build_capability_catalog(
        [
            normalize_candidate(
                {
                    "id": "search-agent",
                    "display_name": "Search Agent",
                    "vendor": "Test Directory",
                    "vendor_url": "https://example.com/search",
                    "provider_type": "a2a_agent",
                    "capabilities": [
                        {"id": "web_search", "notes": "Web search across the open web."}
                    ],
                },
                source="test",
            )
        ]
    )


class GoalDecomposerHappyPathTests(unittest.TestCase):
    def test_returns_full_decomposition_on_well_formed_payload(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())

        decomposition = decompose_goal(
            "I want to buy 100 cars for my top 100 employees in bengaluru, "
            "help me decide which car brands will give discounts and better rates",
            catalog_hint=_commerce_catalog(),
            client=client,
        )

        self.assertEqual(5, len(decomposition.sub_tasks))
        self.assertEqual(0.82, decomposition.confidence)
        self.assertEqual(
            [
                "web_search",
                "contact_enrichment",
                "browser_automation",
                "email_sending",
                "comparison_tabulation",
            ],
            decomposition.suggested_capability_ids,
        )

    def test_partitions_capabilities_into_reused_and_new(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())

        decomposition = decompose_goal(
            "buy 100 cars in bengaluru",
            catalog_hint=_hint_catalog_with_existing_label(),
            client=client,
        )

        # ``web_search`` exists in the hint; the other four are coined.
        self.assertIn("web_search", decomposition.catalog_reused_capabilities)
        self.assertNotIn("web_search", decomposition.new_capabilities)
        for new_cap in (
            "contact_enrichment",
            "browser_automation",
            "email_sending",
            "comparison_tabulation",
        ):
            self.assertIn(new_cap, decomposition.new_capabilities)

    def test_treats_all_capabilities_as_new_when_catalog_hint_is_empty(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())

        decomposition = decompose_goal(
            "buy 100 cars in bengaluru",
            catalog_hint=None,
            client=client,
        )

        self.assertEqual([], decomposition.catalog_reused_capabilities)
        self.assertEqual(
            [
                "browser_automation",
                "comparison_tabulation",
                "contact_enrichment",
                "email_sending",
                "web_search",
            ],
            sorted(decomposition.new_capabilities),
        )


class GoalDecomposerPromptContractTests(unittest.TestCase):
    """The prompt is the architectural fix for the old commerce-bias
    bug. These tests pin its key invariants so a future edit can't
    silently re-introduce the bias."""

    def test_system_prompt_does_not_enumerate_capability_families(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal("buy 100 cars in bengaluru", client=client)

        system = client.calls[0][0]["content"]

        forbidden_substrings = (
            # The old prompts enumerated these to "help" the LLM and
            # ended up biasing it. The new prompt must not enumerate
            # any one family.
            "compliance",
            "cross-border",
            "cross_border",
            "border",
            "customs",
            "duties",
            "import/export",
            "payment requirements",
            "identity requirements",
        )
        for needle in forbidden_substrings:
            self.assertNotIn(
                needle.lower(),
                system.lower(),
                f"system prompt must not enumerate '{needle}' "
                "(re-introduces the commerce/compliance bias bug)",
            )

    def test_system_prompt_explicitly_allows_coining_new_labels(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal("buy 100 cars in bengaluru", client=client)

        system = client.calls[0][0]["content"]
        self.assertIn("coin", system.lower())
        self.assertIn("snake_case", system.lower())

    def test_user_message_passes_catalog_hint_when_provided(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal(
            "buy 100 cars in bengaluru",
            catalog_hint=_commerce_catalog(),
            client=client,
        )

        user = client.calls[0][1]["content"]
        self.assertIn("catalog_hint", user)
        self.assertIn("product_search", user)

    def test_user_message_omits_catalog_hint_when_absent(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal("buy 100 cars in bengaluru", client=client)

        user = client.calls[0][1]["content"]
        self.assertNotIn("catalog_hint", user)


class GoalDecomposerParserToleranceTests(unittest.TestCase):
    def test_accepts_payload_wrapped_in_markdown_fences(self) -> None:
        wrapped = "```json\n" + json.dumps(_car_procurement_payload()) + "\n```"
        client = _FakeChatClient(wrapped)

        decomposition = decompose_goal("buy 100 cars in bengaluru", client=client)

        self.assertEqual(5, len(decomposition.sub_tasks))

    def test_normalises_capability_id_with_spaces_and_hyphens(self) -> None:
        payload = _car_procurement_payload()
        payload["sub_tasks"][0]["suggested_capability_id"] = "Web Search"
        payload["sub_tasks"][1]["suggested_capability_id"] = "contact-enrichment"
        client = _FakeChatClient(payload)

        decomposition = decompose_goal("buy 100 cars in bengaluru", client=client)

        self.assertEqual("web_search", decomposition.sub_tasks[0].suggested_capability_id)
        self.assertEqual(
            "contact_enrichment", decomposition.sub_tasks[1].suggested_capability_id
        )


class GoalDecomposerValidationTests(unittest.TestCase):
    def test_rejects_empty_goal(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())
        with self.assertRaises(GoalDecompositionError):
            decompose_goal("   ", client=client)

    def test_rejects_response_with_no_sub_tasks(self) -> None:
        client = _FakeChatClient(
            {"intent_summary": "x", "confidence": 0.9, "sub_tasks": []}
        )
        with self.assertRaises(GoalDecompositionError):
            decompose_goal("buy 100 cars in bengaluru", client=client)

    def test_rejects_sub_task_with_empty_required_field(self) -> None:
        payload = _car_procurement_payload()
        payload["sub_tasks"][0]["search_query"] = ""
        client = _FakeChatClient(payload)

        with self.assertRaises(GoalDecompositionError):
            decompose_goal("buy 100 cars in bengaluru", client=client)

    def test_rejects_invalid_capability_id_format(self) -> None:
        payload = _car_procurement_payload()
        payload["sub_tasks"][0]["suggested_capability_id"] = "1bad-start"
        client = _FakeChatClient(payload)

        with self.assertRaises(GoalDecompositionError):
            decompose_goal("buy 100 cars in bengaluru", client=client)

    def test_rejects_response_below_min_confidence(self) -> None:
        payload = _car_procurement_payload()
        payload["confidence"] = 0.20
        client = _FakeChatClient(payload)

        with self.assertRaises(GoalDecompositionError):
            decompose_goal(
                "buy 100 cars in bengaluru", client=client, min_confidence=0.55
            )

    def test_caps_sub_tasks_at_max(self) -> None:
        payload = _car_procurement_payload()
        # Inject one duplicate slot to push past the cap; the
        # decomposer should slice rather than raise.
        extra = dict(payload["sub_tasks"][0])
        extra["suggested_capability_id"] = "extra_cap_a"
        payload["sub_tasks"] = payload["sub_tasks"] + [extra] * 6  # 11 total

        client = _FakeChatClient(payload)
        decomposition = decompose_goal("buy 100 cars in bengaluru", client=client)
        self.assertLessEqual(len(decomposition.sub_tasks), 8)


class GoalDecomposerLlmFailureTests(unittest.TestCase):
    def test_wraps_qwen_timeout_as_decomposition_error(self) -> None:
        client = _FailingChatClient(LocalQwenPlannerError("timed out"))
        with self.assertRaises(GoalDecompositionError):
            decompose_goal("buy 100 cars in bengaluru", client=client)

    def test_wraps_no_llm_tier_as_decomposition_error(self) -> None:
        metadata = EscalationMetadata(
            primary_label="qwen-test",
            primary_attempted=True,
            primary_error="connection refused",
        )
        client = _FailingChatClient(NoLlmTierAvailableError(metadata))
        with self.assertRaises(GoalDecompositionError):
            decompose_goal("buy 100 cars in bengaluru", client=client)


class GoalDecomposerEscalationTests(unittest.TestCase):
    """Verify that when ``decompose_goal`` is given an
    :class:`EscalatingChatClient`, malformed JSON from tier 1 escalates
    to tier 2 instead of dead-ending with ``GoalDecompositionError``.

    This is the BUG-1 fix: ``llama-4-scout`` returning a sub-task with
    an empty ``search_query`` used to kill the entire decomposition
    path. With the quality_check wired up, the next Groq model gets a
    chance to produce a well-formed plan.
    """

    def test_malformed_first_tier_escalates_to_second_tier(self) -> None:
        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        # Tier 1 returns a syntactically valid JSON with one bad
        # sub-task — exactly the failure mode the audit caught.
        bad = dict(_car_procurement_payload())
        bad_subs = [dict(s) for s in bad["sub_tasks"]]
        bad_subs[0] = dict(bad_subs[0])
        bad_subs[0]["search_query"] = ""  # empty → reject
        bad["sub_tasks"] = bad_subs
        # Tier 2 returns a clean payload.
        good = _car_procurement_payload()

        tier_1 = _FakeChatClient(bad)
        tier_2 = _FakeChatClient(good)
        escalating = EscalatingChatClient(
            tiers=[tier_1, tier_2], tier_labels=["scout", "70b"]
        )

        decomposition = decompose_goal(
            "buy 100 cars in bengaluru", client=escalating
        )

        # Tier 2's payload won — proves quality_check escalated.
        self.assertEqual(5, len(decomposition.sub_tasks))
        # Both tiers were called.
        self.assertEqual(len(tier_1.calls), 1)
        self.assertEqual(len(tier_2.calls), 1)
        # Provenance shows we landed on the fallback (tier 1 = primary,
        # tier 2 = fallback in legacy semantics).
        self.assertEqual(escalating.last_metadata.tier_used, "fallback")

    def test_well_formed_first_tier_short_circuits_no_escalation(self) -> None:
        """The quality_check must NOT be over-eager — a clean payload
        from tier 1 must short-circuit the chain. Otherwise the
        rotation costs latency on every request even when the
        primary tier is healthy.
        """

        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        good = _car_procurement_payload()
        tier_1 = _FakeChatClient(good)
        tier_2 = _FakeChatClient(good)
        escalating = EscalatingChatClient(
            tiers=[tier_1, tier_2], tier_labels=["scout", "70b"]
        )

        decomposition = decompose_goal(
            "buy 100 cars in bengaluru", client=escalating
        )

        self.assertEqual(5, len(decomposition.sub_tasks))
        self.assertEqual(len(tier_1.calls), 1)
        self.assertEqual(len(tier_2.calls), 0)  # never reached
        self.assertEqual(escalating.last_metadata.tier_used, "primary")

    def test_unparseable_first_tier_escalates_to_second(self) -> None:
        """Tier 1 returns a non-JSON string — quality_check rejects,
        chain moves to tier 2. Without escalation the parser would
        raise ``GoalDecompositionError`` without ever trying the
        next tier.
        """

        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        tier_1 = _FakeChatClient("totally not json at all")
        tier_2 = _FakeChatClient(_car_procurement_payload())
        escalating = EscalatingChatClient(
            tiers=[tier_1, tier_2], tier_labels=["broken", "good"]
        )

        decomposition = decompose_goal(
            "buy 100 cars in bengaluru", client=escalating
        )

        self.assertEqual(5, len(decomposition.sub_tasks))

    def test_low_confidence_first_tier_escalates(self) -> None:
        """A well-formed but low-confidence answer must escalate too —
        the floor exists for a reason and a slower tier may produce a
        more decisive answer.
        """

        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        low = dict(_car_procurement_payload())
        low["confidence"] = 0.20  # below 0.55 floor
        good = _car_procurement_payload()

        tier_1 = _FakeChatClient(low)
        tier_2 = _FakeChatClient(good)
        escalating = EscalatingChatClient(
            tiers=[tier_1, tier_2], tier_labels=["unsure", "confident"]
        )

        decomposition = decompose_goal(
            "buy 100 cars in bengaluru", client=escalating
        )

        # Tier 2's high-confidence answer won.
        self.assertGreaterEqual(decomposition.confidence, 0.55)
        self.assertEqual(escalating.last_metadata.tier_used, "fallback")


class DecomposedSubTaskRoundtripTests(unittest.TestCase):
    def test_to_json_includes_all_fields(self) -> None:
        sub = DecomposedSubTask(
            description="d",
            user_facing_step="u",
            search_query="s",
            acceptance_criteria="a",
            suggested_capability_id="c_id",
        )
        self.assertEqual(
            {
                "description": "d",
                "user_facing_step": "u",
                "search_query": "s",
                "acceptance_criteria": "a",
                "suggested_capability_id": "c_id",
            },
            sub.to_json(),
        )

    def test_decomposition_to_json_round_trip(self) -> None:
        sub = DecomposedSubTask(
            description="d",
            user_facing_step="u",
            search_query="s",
            acceptance_criteria="a",
            suggested_capability_id="c_id",
        )
        decomposition = GoalDecomposition(
            intent_summary="x",
            sub_tasks=[sub],
            confidence=0.8,
            catalog_reused_capabilities=[],
            new_capabilities=["c_id"],
        )
        out = decomposition.to_json()
        self.assertEqual("x", out["intent_summary"])
        self.assertEqual(["c_id"], out["new_capabilities"])
        self.assertEqual(1, len(out["sub_tasks"]))


class DecomposerCatalogDescriptionsPromptTests(unittest.TestCase):
    """Pin the prompt-contract behaviour of the new
    ``catalog_descriptions`` argument so a future refactor can't
    silently drop the descriptions or merge them back into the
    flat-list form that produced the "0 agents discovered" bug.

    These are prompt-contract tests, not behaviour tests: they
    inspect what the LLM is *shown* (via the recorded messages on
    ``_FakeChatClient``), not what it does in response. Behavioural
    coverage that the LLM actually reuses described slugs lives in
    :class:`DecomposerCatalogDescriptionsBehaviourTests` below."""

    def _sample_descriptions(self) -> dict[str, dict[str, list[str] | str]]:
        """Two descriptions that mirror real registry entries but are
        compact enough to assert against in full. ``web_search`` is the
        slug the gift goal SHOULD reuse; including it here lets us
        verify the prompt would actually surface it."""
        return {
            "web_search": {
                "description": (
                    "Query a general-purpose web search engine and return "
                    "ranked results. Default tool for open-web research."
                ),
                "examples": [
                    "Find gift ideas for a 3-year-old",
                    "Look up vendors that sell a product",
                ],
            },
            "payment_authorization": {
                "description": (
                    "Initiate or authorise a payment via a payments "
                    "processor like Stripe or Razorpay."
                ),
                "examples": [
                    "Charge a customer's card",
                    "Authorise a payment for an e-commerce order",
                ],
            },
        }

    def test_router_supported_block_is_emitted_when_descriptions_provided(self) -> None:
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal(
            "find a gift for my daughter",
            catalog_hint=None,
            catalog_descriptions=self._sample_descriptions(),
            client=client,
        )
        user = client.calls[0][1]["content"]
        self.assertIn("catalog_hint", user)
        self.assertIn("router_supported", user)
        self.assertIn("web_search", user)
        self.assertIn("payment_authorization", user)
        # The descriptions themselves must reach the LLM verbatim — that
        # is the whole point of this argument. Asserting on a substring
        # is brittle to wording edits in `_sample_descriptions`, but the
        # alternative (round-tripping JSON and walking it) is overkill
        # for a prompt-contract test.
        self.assertIn("ranked results", user)
        self.assertIn("Stripe or Razorpay", user)
        self.assertIn("Find gift ideas for a 3-year-old", user)

    def test_other_known_ids_block_is_emitted_for_undescribed_catalog_ids(
        self,
    ) -> None:
        """Slugs in ``catalog_hint`` but NOT in ``catalog_descriptions``
        belong in the secondary pool. Verifies the prompt distinguishes
        executable-by-router slugs from purely-discovered ones."""
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal(
            "find a gift for my daughter",
            catalog_hint=_commerce_catalog(),  # product_search, price_comparison
            catalog_descriptions={
                "web_search": {
                    "description": "Web search.",
                    "examples": ["Find gift ideas", "Look up vendors"],
                }
            },
            client=client,
        )
        user = client.calls[0][1]["content"]
        self.assertIn("other_known_capability_ids", user)
        self.assertIn("product_search", user)
        # `price_comparison` is in the commerce catalog hint and is
        # NOT in catalog_descriptions, so it falls into the secondary
        # pool. (In production it would be in BOTH; this test isolates
        # the partition logic.)
        self.assertIn("price_comparison", user)
        # web_search is described → it lands in `router_supported`,
        # not the secondary pool.
        self.assertIn("router_supported", user)
        self.assertIn("web_search", user)

    def test_empty_descriptions_reduces_to_legacy_flat_list_behaviour(self) -> None:
        """When ``catalog_descriptions`` is ``None`` or ``{}`` the
        prompt MUST match the legacy shape (no ``router_supported``
        block) so existing operator expectations and existing tests
        still hold.

        The check parses the user message back as JSON instead of
        substring-matching, because the literal string ``router_supported``
        appears inside the ``explanation`` text regardless — the
        invariant we care about is that the JSON STRUCTURE has no
        ``router_supported`` key, not that the word never appears."""
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal(
            "buy 100 cars in bengaluru",
            catalog_hint=_commerce_catalog(),
            catalog_descriptions=None,
            client=client,
        )
        user_msg = client.calls[0][1]["content"]
        parsed = json.loads(user_msg)
        self.assertIn("catalog_hint", parsed)
        self.assertNotIn("router_supported", parsed["catalog_hint"])
        self.assertIn("other_known_capability_ids", parsed["catalog_hint"])
        self.assertIn("product_search", parsed["catalog_hint"]["other_known_capability_ids"])

    def test_no_catalog_at_all_omits_catalog_hint_entirely(self) -> None:
        """No hint AND no descriptions → no catalog_hint key in the
        user message. Same as the legacy ``test_user_message_omits_*``
        invariant, repeated here with the new argument shape."""
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal(
            "buy 100 cars in bengaluru",
            catalog_hint=None,
            catalog_descriptions=None,
            client=client,
        )
        user = client.calls[0][1]["content"]
        self.assertNotIn("catalog_hint", user)
        self.assertNotIn("router_supported", user)

    def test_prompt_instructs_strong_reuse_preference(self) -> None:
        """The system prompt must explicitly tell the LLM to STRONGLY
        prefer router_supported reuse. Without that instruction the
        descriptions are window-dressing and the LLM still coins
        synonyms — exactly the bug this whole fix exists to prevent.

        Asserting on the literal word "STRONGLY" is a deliberate
        signal-vs-noise tradeoff: it's specific enough that a future
        rewrite removing the emphasis would fail this test, and
        explicit enough that a contributor reading the test understands
        what the assertion is protecting."""
        client = _FakeChatClient(_car_procurement_payload())
        decompose_goal("anything", client=client)
        system = client.calls[0][0]["content"]
        self.assertIn("STRONGLY", system)
        self.assertIn("router_supported", system)
        # The example mappings in the prompt are the LLM's only
        # explicit signal that a "buy / purchase" sub-task should
        # reuse `payment_authorization`. Lose these and the prompt
        # regresses to the pre-fix wording that produced the bug.
        self.assertIn("payment_authorization", system)
        self.assertIn("web_search", system)
        self.assertIn("price_comparison", system)


class DecomposerCatalogDescriptionsBehaviourTests(unittest.TestCase):
    """Behaviour tests: when a fake LLM returns a payload that DOES
    reuse described slugs, the resulting decomposition correctly
    partitions them as catalog-reused (not new). These prove the
    full pipeline credits reuse correctly so leaderboards and gap
    signals stay honest."""

    def _gift_buying_payload_with_reuse(self) -> dict:
        """Simulates what a correctly-prompted LLM SHOULD return for
        the gift-buying goal: 4 sub-tasks that all reuse
        router-supported slugs.

        This is the contract the prompt fix is trying to elicit from
        the real LLM. The behavioural test asserts that IF the LLM
        returns this shape, the decomposer book-keeps it correctly —
        not that the real LLM will actually return this shape (a
        live-LLM eval owns that)."""
        return {
            "intent_summary": (
                "Help the user pick and buy a birthday gift for their "
                "3-year-old daughter in Bengaluru."
            ),
            "confidence": 0.9,
            "sub_tasks": [
                {
                    "description": "Research gift ideas suitable for 3-year-olds.",
                    "user_facing_step": "Find gift ideas for a 3-year-old.",
                    "search_query": "web search API",
                    "acceptance_criteria": (
                        "Returns ranked open-web search results for arbitrary queries."
                    ),
                    "suggested_capability_id": "web_search",
                },
                {
                    "description": "Compare prices for the candidate gifts.",
                    "user_facing_step": "Compare prices across vendors.",
                    "search_query": "price comparison API",
                    "acceptance_criteria": (
                        "Compares listed prices for the same SKU across vendors."
                    ),
                    "suggested_capability_id": "price_comparison",
                },
                {
                    "description": "Find Bengaluru stores that stock the chosen gift.",
                    "user_facing_step": "Find Bengaluru stores stocking the gift.",
                    "search_query": "store locator API",
                    "acceptance_criteria": (
                        "Returns physical store locations near a city for a brand."
                    ),
                    "suggested_capability_id": "store_locator",
                },
                {
                    "description": "Authorise payment for the selected gift.",
                    "user_facing_step": "Pay for the gift.",
                    "search_query": "payments processor API",
                    "acceptance_criteria": (
                        "Creates a charge against a customer card via a payments processor."
                    ),
                    "suggested_capability_id": "payment_authorization",
                },
            ],
        }

    def _gift_described_capabilities(self) -> dict[str, dict[str, list[str] | str]]:
        return {
            slug: {"description": f"{slug} description.", "examples": [f"{slug} example"]}
            for slug in ("web_search", "price_comparison", "store_locator", "payment_authorization")
        }

    def test_described_slugs_count_as_reused_not_coined(self) -> None:
        """The regression test for the user-facing failure mode: a goal
        whose sub-tasks all map to router-supported capabilities must
        produce a decomposition where every slug is `catalog_reused`
        and `new_capabilities` is empty. The prior behaviour coined
        all four as new, which made discovery dispatch with slugs no
        scout source could match."""
        client = _FakeChatClient(self._gift_buying_payload_with_reuse())
        decomposition = decompose_goal(
            "I want to buy a surprise birthday gift for my daughter who is "
            "3 years old in Bengaluru, suggest me some options and can you buy it",
            catalog_hint=None,
            catalog_descriptions=self._gift_described_capabilities(),
            client=client,
        )

        self.assertEqual(4, len(decomposition.sub_tasks))
        self.assertEqual(
            ["web_search", "price_comparison", "store_locator", "payment_authorization"],
            decomposition.suggested_capability_ids,
        )
        # The book-keeping contract: described slugs that appear in the
        # output count as catalog-reused, not as freshly-coined. Without
        # this the discovery-gap leaderboard would report 4 false-coin
        # signals per gift-style request.
        self.assertEqual(
            sorted([
                "payment_authorization",
                "price_comparison",
                "store_locator",
                "web_search",
            ]),
            sorted(decomposition.catalog_reused_capabilities),
        )
        self.assertEqual([], decomposition.new_capabilities)

    def test_mixed_reuse_and_coin_partitions_correctly(self) -> None:
        """A sub-task whose slug is NOT in the descriptions must still
        land in `new_capabilities`. The partition is described-pool
        membership, not "did the LLM say `catalog_reused`"."""

        payload = self._gift_buying_payload_with_reuse()
        # Replace one slug with a freshly coined one not in the
        # described pool; everything else still reuses.
        payload["sub_tasks"][2]["suggested_capability_id"] = "gift_wrapping_service"
        client = _FakeChatClient(payload)

        decomposition = decompose_goal(
            "buy and wrap a gift",
            catalog_hint=None,
            catalog_descriptions=self._gift_described_capabilities(),
            client=client,
        )

        self.assertIn("gift_wrapping_service", decomposition.new_capabilities)
        self.assertNotIn(
            "gift_wrapping_service", decomposition.catalog_reused_capabilities
        )
        self.assertIn("web_search", decomposition.catalog_reused_capabilities)


if __name__ == "__main__":
    unittest.main()

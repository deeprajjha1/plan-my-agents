"""Tests for the escalating chat client.

The escalating client is the single source of truth for "which LLM
answered this request and why". These tests pin the contract so future
refactors can't quietly reintroduce silent fallbacks.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    NoLlmTierAvailableError,
    QualityVerdict,
    build_default_escalating_client,
)


@dataclass
class StubClient:
    """Minimal ChatClient-compatible stub used by the tests."""

    model: str = "stub-model"
    response: str = "ok"
    raises: type[Exception] | None = None
    calls: list[list[dict[str, str]]] = field(default_factory=list)

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises("simulated transport failure")
        return self.response


class EscalatingChatClientPrimarySuccessTests(unittest.TestCase):
    def test_primary_success_returns_primary_content_and_records_metadata(self) -> None:
        primary = StubClient(response="primary answer")
        fallback = StubClient(response="fallback answer")
        client = EscalatingChatClient(
            primary=primary,
            fallback=fallback,
            primary_label="qwen",
            fallback_label="groq",
        )

        result = client.complete_with_metadata([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "primary answer")
        self.assertEqual(result.metadata.tier_used, "primary")
        self.assertTrue(result.metadata.primary_attempted)
        self.assertFalse(result.metadata.fallback_attempted)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 0)

    def test_complete_returns_bare_string_for_protocol_compatibility(self) -> None:
        primary = StubClient(response="primary answer")
        client = EscalatingChatClient(primary=primary, primary_label="qwen")

        text = client.complete([{"role": "user", "content": "hi"}])

        self.assertEqual(text, "primary answer")
        self.assertEqual(client.last_metadata.tier_used, "primary")


class EscalatingChatClientPrimaryFailureTests(unittest.TestCase):
    def test_primary_transport_error_falls_back_to_fallback(self) -> None:
        primary = StubClient(raises=RuntimeError)
        fallback = StubClient(response="fallback answer")
        client = EscalatingChatClient(
            primary=primary, fallback=fallback,
            primary_label="qwen", fallback_label="groq",
        )

        result = client.complete_with_metadata([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "fallback answer")
        self.assertEqual(result.metadata.tier_used, "fallback")
        self.assertTrue(result.metadata.primary_attempted)
        self.assertIn("RuntimeError", result.metadata.primary_error or "")
        self.assertTrue(result.metadata.fallback_attempted)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 1)

    def test_primary_empty_response_falls_back_to_fallback(self) -> None:
        primary = StubClient(response="   ")
        fallback = StubClient(response="real answer")
        client = EscalatingChatClient(primary=primary, fallback=fallback)

        result = client.complete_with_metadata([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "real answer")
        self.assertEqual(result.metadata.tier_used, "fallback")
        self.assertEqual(result.metadata.primary_error, "empty_response")


class EscalatingChatClientQualityCheckTests(unittest.TestCase):
    def test_quality_check_accept_short_circuits_fallback(self) -> None:
        primary = StubClient(response="primary answer")
        fallback = StubClient(response="fallback answer")
        client = EscalatingChatClient(primary=primary, fallback=fallback)

        result = client.complete_with_metadata(
            [{"role": "user", "content": "hi"}],
            quality_check=lambda _content: QualityVerdict.ACCEPT,
        )

        self.assertEqual(result.content, "primary answer")
        self.assertEqual(result.metadata.tier_used, "primary")
        self.assertFalse(result.metadata.quality_check_triggered)
        self.assertEqual(len(fallback.calls), 0)

    def test_quality_check_escalate_uses_fallback(self) -> None:
        primary = StubClient(response="schema-broken")
        fallback = StubClient(response="schema-valid")
        client = EscalatingChatClient(primary=primary, fallback=fallback)

        result = client.complete_with_metadata(
            [{"role": "user", "content": "hi"}],
            quality_check=lambda content: (
                QualityVerdict.ACCEPT if content == "schema-valid" else QualityVerdict.ESCALATE
            ),
        )

        self.assertEqual(result.content, "schema-valid")
        self.assertEqual(result.metadata.tier_used, "fallback")
        self.assertTrue(result.metadata.quality_check_triggered)

    def test_quality_check_exception_treated_as_escalate(self) -> None:
        """A buggy quality_check on the primary tier must NOT crash the
        chain — it must escalate exactly the way an ``ESCALATE`` verdict
        would. The contract is "tier 1 quality_check raised ⇒ try the
        next tier". We pass a ``boom`` that raises only on the first
        invocation so the fallback gets a chance to be evaluated
        normally and ship its content. (If ``boom`` raised on every
        call, every tier would escalate and the chain would correctly
        raise NoLlmTierAvailableError — that's the right behaviour
        under N-tier rotation, but it would mask the contract this
        test is pinning.)
        """

        primary = StubClient(response="anything")
        fallback = StubClient(response="hosted answer")
        client = EscalatingChatClient(primary=primary, fallback=fallback)

        invocations = {"count": 0}

        def boom(_content: str) -> QualityVerdict:
            invocations["count"] += 1
            if invocations["count"] == 1:
                raise ValueError("buggy quality_check")
            return QualityVerdict.ACCEPT

        result = client.complete_with_metadata(
            [{"role": "user", "content": "hi"}], quality_check=boom
        )

        self.assertEqual(result.content, "hosted answer")
        self.assertEqual(result.metadata.tier_used, "fallback")
        self.assertIn("ValueError", result.metadata.primary_error or "")


class EscalatingChatClientNoTierTests(unittest.TestCase):
    def test_both_tiers_fail_raises_no_llm_tier_available(self) -> None:
        primary = StubClient(raises=RuntimeError)
        fallback = StubClient(raises=ConnectionError)
        client = EscalatingChatClient(
            primary=primary, fallback=fallback,
            primary_label="qwen", fallback_label="groq",
        )

        with self.assertRaises(NoLlmTierAvailableError) as ctx:
            client.complete_with_metadata([{"role": "user", "content": "hi"}])

        meta = ctx.exception.metadata
        self.assertEqual(meta.tier_used, "none")
        self.assertTrue(meta.primary_attempted)
        self.assertTrue(meta.fallback_attempted)
        self.assertIn("RuntimeError", meta.primary_error or "")
        self.assertIn("ConnectionError", meta.fallback_error or "")

    def test_no_primary_no_fallback_raises_immediately(self) -> None:
        client = EscalatingChatClient(primary=None, fallback=None)

        with self.assertRaises(NoLlmTierAvailableError) as ctx:
            client.complete([{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.metadata.tier_used, "none")
        self.assertFalse(ctx.exception.metadata.primary_attempted)
        self.assertFalse(ctx.exception.metadata.fallback_attempted)

    def test_no_primary_falls_through_to_fallback(self) -> None:
        fallback = StubClient(response="fallback only")
        client = EscalatingChatClient(
            primary=None, fallback=fallback, fallback_label="groq",
        )

        result = client.complete_with_metadata([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "fallback only")
        self.assertEqual(result.metadata.tier_used, "fallback")
        self.assertFalse(result.metadata.primary_attempted)


class BuildDefaultEscalatingClientTests(unittest.TestCase):
    def test_default_chain_uses_ollama_qwen_as_primary(self) -> None:
        # When the caller injects an explicit primary, that primary is
        # used regardless of tier-order auto-detection. This is the
        # contract every other test in this module relies on.
        primary = StubClient(model="qwen-test", response="ok")
        client = build_default_escalating_client(primary=primary)

        self.assertIs(client.primary, primary)
        self.assertEqual(client.primary_label, "qwen-test")

    def test_default_chain_skips_groq_without_api_key(self) -> None:
        with self._patched_env({"GROQ_API_KEY": None}):
            primary = StubClient(model="qwen-test", response="ok")
            client = build_default_escalating_client(primary=primary)

        self.assertIs(client.primary, primary)
        self.assertIsNone(client.fallback)
        self.assertEqual(client.fallback_label, "")

    def test_auto_mode_promotes_groq_to_primary_when_key_set(self) -> None:
        """When GROQ_API_KEY is set and tier order is 'auto' (default),
        Groq is primary and Qwen is fallback. This is the change that
        collapsed the canonical ``/goal`` smoke test from ~7 min
        (Qwen-primary, 5 sequential 60-110s calls) to ~30s.
        """

        # Clear ``GROQ_MODELS`` explicitly so this test sees the
        # single-tier Groq behaviour even if the host's shell or the
        # repo .env has the rotation configured. The N-tier rotation
        # is exercised by ``BuildDefaultEscalatingClientGroqRotationTests``
        # below; this test is specifically about the legacy
        # primary/fallback shape.
        with self._patched_env(
            {
                "GROQ_API_KEY": "test-key-not-used-because-construction-is-lazy",
                "GROQ_MODELS": None,
                "GROQ_MODEL": None,
                "PLANMYAGENTS_LLM_TIER_ORDER": None,  # default = auto
            }
        ):
            client = build_default_escalating_client()

        # Primary is Groq, fallback is Qwen.
        self.assertEqual(type(client.primary).__name__, "GroqChatClient")
        self.assertEqual(type(client.fallback).__name__, "OllamaQwenClient")

    def test_qwen_first_override_keeps_qwen_primary_even_with_key(self) -> None:
        """``PLANMYAGENTS_LLM_TIER_ORDER=qwen_first`` is the historical
        Qwen-primary behaviour. Useful for cost control: when latency is
        acceptable, Qwen is free and Groq is metered. This override
        must beat the auto-detection.
        """

        with self._patched_env(
            {
                "GROQ_API_KEY": "test-key",
                "GROQ_MODELS": None,
                "GROQ_MODEL": None,
                "PLANMYAGENTS_LLM_TIER_ORDER": "qwen_first",
            }
        ):
            client = build_default_escalating_client()

        self.assertEqual(type(client.primary).__name__, "OllamaQwenClient")
        self.assertEqual(type(client.fallback).__name__, "GroqChatClient")

    def test_groq_first_override_without_key_degrades_to_qwen_primary(self) -> None:
        """``PLANMYAGENTS_LLM_TIER_ORDER=groq_first`` requests Groq
        primary, but without ``GROQ_API_KEY`` we can't construct a Groq
        client. The conservative behaviour is to degrade to Qwen-only
        rather than to crash the route.
        """

        with self._patched_env(
            {
                "GROQ_API_KEY": None,
                "PLANMYAGENTS_LLM_TIER_ORDER": "groq_first",
            }
        ):
            client = build_default_escalating_client()

        self.assertEqual(type(client.primary).__name__, "OllamaQwenClient")
        self.assertIsNone(client.fallback)

    def test_unknown_tier_order_falls_back_to_auto(self) -> None:
        """A typo'd tier order should not 500 the process. We log a
        warning and use 'auto'. With GROQ_API_KEY present that means
        Groq primary.
        """

        with self._patched_env(
            {
                "GROQ_API_KEY": "test-key",
                "GROQ_MODELS": None,
                "GROQ_MODEL": None,
                "PLANMYAGENTS_LLM_TIER_ORDER": "groqfirst",  # typo
            }
        ):
            client = build_default_escalating_client()

        # auto + GROQ_API_KEY = Groq primary.
        self.assertEqual(type(client.primary).__name__, "GroqChatClient")

    def test_explicit_fallback_overrides_env(self) -> None:
        primary = StubClient(model="qwen-test")
        fallback = StubClient(model="groq-test")
        client = build_default_escalating_client(primary=primary, fallback=fallback)

        self.assertIs(client.fallback, fallback)
        self.assertEqual(client.fallback_label, "groq-test")

    @staticmethod
    def _patched_env(updates: dict[str, str | None]):  # type: ignore[no-untyped-def]
        """Context manager that sets / unsets multiple env vars and
        restores them on exit. ``None`` means "remove this var for the
        duration of the block".
        """

        import os

        class _PatchEnv:
            def __enter__(self_inner) -> None:
                self_inner._previous: dict[str, str | None] = {}
                for name, value in updates.items():
                    self_inner._previous[name] = os.environ.get(name)
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
                return None

            def __exit__(self_inner, *_exc) -> None:
                for name, prior in self_inner._previous.items():
                    if prior is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = prior

        return _PatchEnv()


class EscalatingChatClientNTierTests(unittest.TestCase):
    """Verify the new N-tier API: passing ``tiers=[...]`` walks the
    list in order, escalating on transport failure or quality_check.

    This is the production path for the Groq model rotation
    (``[scout, llama-3.3-70b, ollama]``). Without rotation, a single
    Groq model returning malformed JSON would dead-end the request
    in the slow Ollama tier; with rotation, it just escalates to
    the next Groq model first.
    """

    def test_tiers_walked_in_order_until_one_succeeds(self) -> None:
        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        tier_a = StubClient(model="groq-scout", raises=RuntimeError)
        tier_b = StubClient(model="groq-70b", raises=ConnectionError)
        tier_c = StubClient(model="groq-qwen", response="qwen wins")
        tier_d = StubClient(model="ollama-qwen", response="ollama")
        client = EscalatingChatClient(
            tiers=[tier_a, tier_b, tier_c, tier_d],
            tier_labels=["groq-scout", "groq-70b", "groq-qwen", "ollama-qwen"],
        )

        result = client.complete_with_metadata([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "qwen wins")
        # Tier_d never invoked — the chain stopped at first success.
        self.assertEqual(len(tier_a.calls), 1)
        self.assertEqual(len(tier_b.calls), 1)
        self.assertEqual(len(tier_c.calls), 1)
        self.assertEqual(len(tier_d.calls), 0)
        # Per-tier metadata is filled for the attempted ones.
        labels_attempted = [
            a.label for a in result.metadata.tier_attempts if a.attempted
        ]
        self.assertEqual(labels_attempted, ["groq-scout", "groq-70b", "groq-qwen"])
        # Successful tier is the third — ``tier_used`` reflects index
        # via the ``tier_<n>`` convention for non-primary/non-fallback
        # positions.
        self.assertEqual(result.metadata.tier_used, "tier_2")

    def test_quality_check_escalates_through_multiple_tiers(self) -> None:
        """The motivation for this whole feature: malformed JSON from
        ``llama-4-scout`` should escalate to ``llama-3.3-70b`` BEFORE
        falling to the slow local Ollama tier. Without N-tier, the
        chain would be Groq → Ollama directly.
        """

        from planmyagents_api.llm.escalating_client import (
            EscalatingChatClient,
            QualityVerdict,
        )

        scout = StubClient(model="scout", response='{"sub_tasks": []}')  # bad
        llama70 = StubClient(model="70b", response='{"sub_tasks": []}')  # also bad
        ollama = StubClient(model="ollama", response='{"sub_tasks": [1]}')  # good
        client = EscalatingChatClient(
            tiers=[scout, llama70, ollama],
            tier_labels=["scout", "70b", "ollama"],
        )

        def quality_check(content: str) -> QualityVerdict:
            # Reject empty sub_tasks list — that's exactly the BUG-1
            # failure mode the rotation is meant to recover from.
            return (
                QualityVerdict.ACCEPT
                if '"sub_tasks": [1]' in content
                else QualityVerdict.ESCALATE
            )

        result = client.complete_with_metadata(
            [{"role": "user", "content": "hi"}], quality_check=quality_check
        )

        self.assertEqual(result.content, '{"sub_tasks": [1]}')
        self.assertEqual(result.metadata.tier_used, "tier_2")
        # All three tiers were called.
        self.assertEqual(len(scout.calls), 1)
        self.assertEqual(len(llama70.calls), 1)
        self.assertEqual(len(ollama.calls), 1)
        # Both rejected tiers have quality_check_triggered=True.
        triggered = [
            a.label for a in result.metadata.tier_attempts if a.quality_check_triggered
        ]
        self.assertEqual(triggered, ["scout", "70b"])

    def test_all_tiers_fail_raises_with_per_tier_metadata(self) -> None:
        from planmyagents_api.llm.escalating_client import (
            EscalatingChatClient,
            NoLlmTierAvailableError,
        )

        client = EscalatingChatClient(
            tiers=[
                StubClient(model="t1", raises=RuntimeError),
                StubClient(model="t2", raises=ConnectionError),
                StubClient(model="t3", raises=TimeoutError),
            ],
            tier_labels=["t1", "t2", "t3"],
        )

        with self.assertRaises(NoLlmTierAvailableError) as ctx:
            client.complete([{"role": "user", "content": "hi"}])

        meta = ctx.exception.metadata
        # Every tier has an attempt entry with the right label and an
        # error string. Operators read this verbatim from the
        # /goal refusal payload to diagnose outages.
        self.assertEqual(
            [a.label for a in meta.tier_attempts], ["t1", "t2", "t3"]
        )
        self.assertTrue(all(a.attempted for a in meta.tier_attempts))
        self.assertTrue(all(a.error for a in meta.tier_attempts))
        self.assertIn("RuntimeError", meta.tier_attempts[0].error or "")
        self.assertIn("ConnectionError", meta.tier_attempts[1].error or "")
        self.assertIn("TimeoutError", meta.tier_attempts[2].error or "")

    def test_legacy_primary_fallback_still_populates_metadata(self) -> None:
        """Backwards-compat: passing ``primary=`` / ``fallback=`` must
        still populate the legacy ``primary_*`` / ``fallback_*``
        metadata fields exactly the way the old API did. UI badges and
        run-log readers depend on this shape.
        """

        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        primary = StubClient(model="qwen", raises=RuntimeError)
        fallback = StubClient(model="groq", response="hosted")
        client = EscalatingChatClient(
            primary=primary,
            fallback=fallback,
            primary_label="qwen",
            fallback_label="groq",
        )

        result = client.complete_with_metadata(
            [{"role": "user", "content": "hi"}]
        )

        self.assertEqual(result.content, "hosted")
        self.assertTrue(result.metadata.primary_attempted)
        self.assertIn("RuntimeError", result.metadata.primary_error or "")
        self.assertTrue(result.metadata.fallback_attempted)
        self.assertEqual(result.metadata.tier_used, "fallback")
        # New tier_attempts list also populated for the legacy path.
        self.assertEqual(len(result.metadata.tier_attempts), 2)
        self.assertEqual(result.metadata.tier_attempts[0].label, "qwen")
        self.assertEqual(result.metadata.tier_attempts[1].label, "groq")

    def test_passing_both_tiers_and_primary_raises(self) -> None:
        """Mixed-construction is almost certainly a bug — refuse
        ambiguity at construction time. Otherwise a refactor that
        starts populating ``tiers`` while leaving the old
        ``primary``/``fallback`` kwargs in place would silently pick
        one path and ignore the other, producing puzzling test
        failures downstream.
        """

        from planmyagents_api.llm.escalating_client import EscalatingChatClient

        with self.assertRaises(ValueError):
            EscalatingChatClient(
                primary=StubClient(model="p"),
                tiers=[StubClient(model="t")],
            )


class GroqModelsParseTests(unittest.TestCase):
    """``GROQ_MODELS`` parsing decides how many Groq tiers
    ``build_default_escalating_client`` constructs. Tests pin the
    parse contract: empty entries stripped, duplicates removed, order
    preserved.
    """

    @staticmethod
    def _patched_env(updates: dict[str, str | None]):  # type: ignore[no-untyped-def]
        # Reuse the helper from BuildDefaultEscalatingClientTests by
        # composition — it's defined on the class so we instantiate
        # one to grab the static method.
        return BuildDefaultEscalatingClientTests._patched_env(updates)

    def test_parses_comma_separated_list_in_order(self) -> None:
        from planmyagents_api.llm.escalating_client import _parse_groq_models

        with self._patched_env({"GROQ_MODELS": "alpha,beta,gamma"}):
            self.assertEqual(_parse_groq_models(), ["alpha", "beta", "gamma"])

    def test_strips_whitespace_and_empty_entries(self) -> None:
        from planmyagents_api.llm.escalating_client import _parse_groq_models

        with self._patched_env(
            {"GROQ_MODELS": " alpha , , beta ,  "}
        ):
            self.assertEqual(_parse_groq_models(), ["alpha", "beta"])

    def test_deduplicates_repeated_entries(self) -> None:
        from planmyagents_api.llm.escalating_client import _parse_groq_models

        with self._patched_env({"GROQ_MODELS": "a,b,a,c,b"}):
            self.assertEqual(_parse_groq_models(), ["a", "b", "c"])

    def test_falls_back_to_single_groq_model_when_models_unset(self) -> None:
        from planmyagents_api.llm.escalating_client import _parse_groq_models

        with self._patched_env(
            {"GROQ_MODELS": None, "GROQ_MODEL": "single-model"}
        ):
            self.assertEqual(_parse_groq_models(), ["single-model"])

    def test_empty_models_with_no_single_model_yields_empty_string(self) -> None:
        """When neither var is set, return ``[""]`` — empty string
        signals the GroqChatClient to use its own DEFAULT_GROQ_MODEL.
        Tests construct GroqChatClient with model="" exactly to
        exercise this default-resolution path.
        """

        from planmyagents_api.llm.escalating_client import _parse_groq_models

        with self._patched_env(
            {"GROQ_MODELS": None, "GROQ_MODEL": None}
        ):
            self.assertEqual(_parse_groq_models(), [""])


class BuildDefaultEscalatingClientGroqRotationTests(unittest.TestCase):
    """When ``GROQ_MODELS`` lists multiple models AND ``GROQ_API_KEY``
    is set, ``build_default_escalating_client`` constructs one Groq
    tier PER MODEL, then appends Ollama as the final tier. This is
    the wiring that makes A1 work end-to-end.
    """

    @staticmethod
    def _patched_env(updates: dict[str, str | None]):  # type: ignore[no-untyped-def]
        return BuildDefaultEscalatingClientTests._patched_env(updates)

    def test_groq_models_list_creates_one_tier_per_model(self) -> None:
        from planmyagents_api.llm.escalating_client import (
            build_default_escalating_client,
        )

        with self._patched_env(
            {
                "GROQ_API_KEY": "test-key",
                "GROQ_MODELS": "model-a,model-b,model-c",
                "PLANMYAGENTS_LLM_TIER_ORDER": None,  # default = auto
            }
        ):
            client = build_default_escalating_client()

        # Expect 4 tiers: 3 Groq + 1 Ollama (Ollama is appended unless
        # construction raised, which it doesn't for OllamaQwenClient
        # since construction is lazy — no network call until complete).
        self.assertEqual(len(client.tiers), 4)
        # Confirm the first three are GroqChatClient instances with
        # the right model attributes (proves the rotation).
        models = [getattr(t, "model", None) for t in client.tiers[:3]]
        self.assertEqual(models, ["model-a", "model-b", "model-c"])
        # Final tier is the Ollama Qwen client.
        self.assertEqual(type(client.tiers[3]).__name__, "OllamaQwenClient")

    def test_groq_models_unset_falls_back_to_single_groq_model(self) -> None:
        """No ``GROQ_MODELS`` env → single Groq tier (using
        ``GROQ_MODEL``). Preserves pre-rotation behaviour for users
        who haven't opted in.
        """

        from planmyagents_api.llm.escalating_client import (
            build_default_escalating_client,
        )

        with self._patched_env(
            {
                "GROQ_API_KEY": "test-key",
                "GROQ_MODELS": None,
                "GROQ_MODEL": "only-this-one",
                "PLANMYAGENTS_LLM_TIER_ORDER": None,
            }
        ):
            client = build_default_escalating_client()

        # 1 Groq + 1 Ollama = 2 tiers.
        self.assertEqual(len(client.tiers), 2)
        # ``GroqChatClient`` strips a leading "models/" or similar but
        # otherwise preserves the model id verbatim — assert prefix to
        # cope with future client-side normalisation.
        self.assertTrue(getattr(client.tiers[0], "model", "").endswith("only-this-one"))
        self.assertEqual(type(client.tiers[1]).__name__, "OllamaQwenClient")

    def test_qwen_first_with_groq_models_puts_ollama_first(self) -> None:
        """``qwen_first`` must respect the rotation but still keep
        Ollama at the front of the chain. Use case: cost control
        (Ollama is free) with Groq rotation as the paid escalation
        path.
        """

        from planmyagents_api.llm.escalating_client import (
            build_default_escalating_client,
        )

        with self._patched_env(
            {
                "GROQ_API_KEY": "test-key",
                "GROQ_MODELS": "fast,slow",
                "PLANMYAGENTS_LLM_TIER_ORDER": "qwen_first",
            }
        ):
            client = build_default_escalating_client()

        # Order: ollama, fast, slow.
        self.assertEqual(type(client.tiers[0]).__name__, "OllamaQwenClient")
        self.assertEqual(getattr(client.tiers[1], "model", None), "fast")
        self.assertEqual(getattr(client.tiers[2], "model", None), "slow")


if __name__ == "__main__":
    unittest.main()

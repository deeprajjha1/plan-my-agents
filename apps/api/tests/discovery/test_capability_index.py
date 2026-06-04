"""Unit tests for ``CapabilityIndex``.

These tests cover the contract the planner and discovery sources rely on
when we delete ``CAPABILITY_SYNONYMS`` and the explicit category map. We
deliberately use a tiny synthetic embedder for most tests so the behaviour
is reproducible regardless of which embedder the env selects — running
these against real Ollama would also work but would be flaky.
"""

from __future__ import annotations

import math
import os
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.capability_index import (
    DEFAULT_MATCH_THRESHOLD,
    CapabilityIndex,
    _build_default_index,
    _load_default_registry_capabilities,
    get_capability_index,
    get_default_capability_index,
)
from planmyagents_api.discovery.embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DeterministicHashEmbedder,
)


@dataclass(frozen=True)
class _CannedEmbedder:
    """Test embedder returning a hand-crafted vector per text.

    Lets a test pin exact cosine similarities without depending on the
    statistical behaviour of the hash embedder, which makes assertions
    on threshold cutoffs hermetic.
    """

    vectors: dict[str, list[float]] = field(default_factory=dict)
    dimension: int = 4
    name: str = "canned-test-embedder"

    def encode(self, text: str) -> list[float]:
        if text in self.vectors:
            vec = self.vectors[text]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            return [v / norm for v in vec]
        # Fall back to all-zeros so unknown text never accidentally
        # matches anything (cosine = 0 against any non-zero vector).
        return [0.0] * self.dimension


class CapabilityIndexExactMatchTests(unittest.TestCase):
    """Whatever embedder is configured, a slug that is byte-identical
    to a registry slug must always resolve to itself with no encoder
    call. This is the cheapest, most-frequent path."""

    def test_exact_match_returns_same_slug(self) -> None:
        index = CapabilityIndex(
            ["semantic_search", "email_verification"],
            embedder=DeterministicHashEmbedder(),
        )
        self.assertEqual("semantic_search", index.match_slug("semantic_search"))
        self.assertEqual(
            "email_verification", index.match_slug("email_verification")
        )

    def test_blank_or_unknown_slug_returns_none(self) -> None:
        index = CapabilityIndex(
            ["semantic_search"], embedder=DeterministicHashEmbedder()
        )
        self.assertIsNone(index.match_slug(""))
        self.assertIsNone(index.match_slug("   "))

    def test_empty_registry_returns_none(self) -> None:
        index = CapabilityIndex([], embedder=DeterministicHashEmbedder())
        self.assertIsNone(index.match_slug("anything"))


class CapabilityIndexThresholdTests(unittest.TestCase):
    """Use the canned embedder to pin exact cosine similarities so the
    threshold cutoff behaviour is hermetic and not dependent on the
    statistical accident of the hash embedder."""

    def setUp(self) -> None:
        # Two registry capabilities, both at unit length after normalisation.
        # 'company data lookup' is identical to 'business search' (cosine 1.0)
        # to test above-threshold matches; 'email verification' is far away
        # (cosine ~0.0) to test below-threshold rejects.
        self.embedder = _CannedEmbedder(
            vectors={
                "company data lookup": [1.0, 0.0, 0.0, 0.0],
                "email verification": [0.0, 1.0, 0.0, 0.0],
                # Test slug aliases that should map to the registry caps.
                "business search": [1.0, 0.0, 0.0, 0.0],  # cosine=1.0 with company
                "ping system": [0.0, 0.0, 1.0, 0.0],  # cosine=0.0 with both
                "noisy email check": [0.2, 0.95, 0.05, 0.0],  # close to email
            }
        )
        self.index = CapabilityIndex(
            ["company_data_lookup", "email_verification"],
            embedder=self.embedder,
            match_threshold=0.78,
        )

    def test_high_similarity_resolves_to_best_capability(self) -> None:
        self.assertEqual(
            "company_data_lookup", self.index.match_slug("business_search")
        )

    def test_below_threshold_returns_none(self) -> None:
        self.assertIsNone(self.index.match_slug("ping_system"))

    def test_threshold_override_promotes_borderline_match(self) -> None:
        # Use a threshold loose enough that a noisy near-match passes.
        self.assertEqual(
            "email_verification",
            self.index.match_slug("noisy_email_check", threshold=0.5),
        )
        # And tight enough that it fails again.
        self.assertIsNone(
            self.index.match_slug("noisy_email_check", threshold=0.99)
        )

    def test_default_threshold_is_read_from_module_constant(self) -> None:
        # Lock the default-threshold contract so a future env-var rename
        # doesn't silently change planner behaviour. Bound is intentionally
        # wide because the right value depends on the configured embedder
        # backend (hash vs Ollama dense); we just want a sanity guardrail.
        self.assertGreater(DEFAULT_MATCH_THRESHOLD, 0.0)
        self.assertLessEqual(DEFAULT_MATCH_THRESHOLD, 1.0)


class CapabilityIndexInferFromTextTests(unittest.TestCase):
    """``infer_from_text`` is the replacement for the old
    CAPABILITY_SYNONYMS lookup that 7 discovery sources used. The
    contract the sources need: a set of registry slugs whose embedding
    sits within the threshold of the input text."""

    def setUp(self) -> None:
        self.embedder = _CannedEmbedder(
            vectors={
                "company data lookup": [1.0, 0.0, 0.0, 0.0],
                "email verification": [0.0, 1.0, 0.0, 0.0],
                "Find me a B2B contact database for SaaS companies": [
                    0.95,
                    0.0,
                    0.05,
                    0.0,
                ],
                "Verify these email addresses": [0.0, 0.92, 0.0, 0.05],
                "What's the weather in Bengaluru?": [0.0, 0.0, 1.0, 0.0],
            }
        )
        self.index = CapabilityIndex(
            ["company_data_lookup", "email_verification"],
            embedder=self.embedder,
            match_threshold=0.78,
        )

    def test_text_close_to_one_capability_returns_only_that_one(self) -> None:
        self.assertEqual(
            {"company_data_lookup"},
            self.index.infer_from_text(
                "Find me a B2B contact database for SaaS companies"
            ),
        )

    def test_text_close_to_other_capability_returns_only_that_one(self) -> None:
        self.assertEqual(
            {"email_verification"},
            self.index.infer_from_text("Verify these email addresses"),
        )

    def test_text_far_from_all_capabilities_returns_empty(self) -> None:
        # Crucial regression: the OLD synonym dict would over-infer
        # `semantic_search` for any query containing "find" or "search".
        # The embedding-based infer must be honest and return nothing
        # when the text genuinely doesn't match any registry capability.
        self.assertEqual(
            set(),
            self.index.infer_from_text("What's the weather in Bengaluru?"),
        )

    def test_empty_text_returns_empty(self) -> None:
        self.assertEqual(set(), self.index.infer_from_text(""))
        self.assertEqual(set(), self.index.infer_from_text("   "))


class CapabilityIndexSlugToTextTests(unittest.TestCase):
    """The slug-to-text conversion is what lets a token-level embedder
    see ``capability_id`` as ``capability id``. If this regresses, the
    hash embedder essentially stops finding any matches because
    underscored ids share no tokens with English text."""

    def test_underscores_become_spaces(self) -> None:
        embedder = DeterministicHashEmbedder()
        index = CapabilityIndex(["email_verification"], embedder=embedder)
        # The hash embedder is statistical, so we don't assert a specific
        # vector — just that the conversion ran and produced *something*
        # non-zero (otherwise infer_from_text would always return empty).
        vec = index._capability_vectors["email_verification"]  # noqa: SLF001
        self.assertEqual(DEFAULT_EMBEDDING_DIM, len(vec))
        self.assertTrue(any(v != 0.0 for v in vec))


class CapabilityIndexFactoryTests(unittest.TestCase):
    """``get_capability_index`` is the production accessor — it must
    cache by registry composition so background workers don't rebuild
    the index on every call."""

    def test_factory_returns_index_with_requested_capabilities(self) -> None:
        index = get_capability_index(["a", "b", "c"])
        self.assertEqual(("a", "b", "c"), tuple(sorted(index.capability_ids)))

    def test_factory_dedupes_and_strips(self) -> None:
        index = get_capability_index(["a", "  a", "b", "", "  "])
        self.assertEqual(("a", "b"), tuple(sorted(index.capability_ids)))

    def test_passing_explicit_embedder_bypasses_cache(self) -> None:
        # Tests need hermetic instances. If the cache leaked, two test
        # methods could see each other's embedder state. The escape
        # hatch is: pass embedder= explicitly.
        e1 = _CannedEmbedder()
        e2 = _CannedEmbedder()
        i1 = get_capability_index(["x"], embedder=e1)
        i2 = get_capability_index(["x"], embedder=e2)
        self.assertIsNot(i1, i2)


class GetDefaultCapabilityIndexTests(unittest.TestCase):
    """``get_default_capability_index`` is the discovery-source entry
    point — the single call that replaces every ``CAPABILITY_SYNONYMS``
    iteration across 7 source files. It must (a) load the live
    registry's capabilities, (b) survive missing/broken registry paths
    without crashing, (c) cache so it isn't a per-call disk read."""

    def setUp(self) -> None:
        # Always start tests with both caches cleared so
        # PLANMYAGENTS_REGISTRY_PATH overrides take effect AND we
        # don't return a stale CapabilityIndex from a prior test.
        _load_default_registry_capabilities.cache_clear()
        _build_default_index.cache_clear()

    def tearDown(self) -> None:
        # Critical: also clear ``_build_default_index`` here. If we
        # only clear the registry-path cache, the *built index* (which
        # is keyed on (cap_tuple, embedder_name)) still holds the
        # empty-registry CapabilityIndex constructed during this
        # test. The next test in the suite that calls
        # ``get_default_capability_index()`` would then get a CACHE
        # HIT on the empty tuple and silently inherit "no inferences
        # possible", which manifests as flaky `inferred_capabilities`
        # assertions in unrelated tests (test_discovery_index).
        _load_default_registry_capabilities.cache_clear()
        _build_default_index.cache_clear()
        os.environ.pop("PLANMYAGENTS_REGISTRY_PATH", None)

    def test_returns_index_built_from_live_registry(self) -> None:
        index = get_default_capability_index()
        # The shipped registry has at least one capability.
        self.assertGreater(len(index.capability_ids), 0)
        # And every shipped capability id should be present in the index.
        self.assertIn("semantic_search", index.capability_ids)

    def test_returns_empty_index_when_registry_path_missing(self) -> None:
        os.environ["PLANMYAGENTS_REGISTRY_PATH"] = "/tmp/does-not-exist.json"
        _load_default_registry_capabilities.cache_clear()
        index = get_default_capability_index()
        self.assertEqual((), index.capability_ids)
        # And the index degrades to "no inferences" for any input — the
        # critical "fail-safe-not-fail-loud" property for a function that
        # runs inside cron / discovery workers.
        self.assertIsNone(index.match_slug("anything"))
        self.assertEqual(set(), index.infer_from_text("any text"))


class InferCapabilitiesForSourceErrorHandlingTests(unittest.TestCase):
    """Regression: when the embedder raises (Ollama down, oversized
    input, OOM, network blip), discovery sources MUST keep working.
    Today they're called from inside the /goal request path, so any
    raised exception 500s the user-facing route.

    The contract: ``infer_capabilities_for_source`` swallows the
    embedder error, logs a warning, and returns whatever the
    keyword-only ``extra``/``fallback`` paths produce — which for an
    empty extra/fallback is an empty set. That's strictly better than
    crashing and matches the source-level fail-safe behaviour that
    ``get_default_capability_index`` itself already implements when
    the registry is missing.
    """

    def setUp(self) -> None:
        _load_default_registry_capabilities.cache_clear()
        _build_default_index.cache_clear()

    def tearDown(self) -> None:
        _load_default_registry_capabilities.cache_clear()
        _build_default_index.cache_clear()
        os.environ.pop("PLANMYAGENTS_REGISTRY_PATH", None)

    def test_returns_empty_set_when_embedder_raises(self) -> None:
        from unittest.mock import patch

        from planmyagents_api.discovery.capability_index import (
            infer_capabilities_for_source,
        )
        from planmyagents_api.discovery.embeddings import EmbedderError

        class _BoomIndex:
            def infer_from_text(self, _text):
                raise EmbedderError("Ollama embeddings request failed: HTTP 500")

        with patch(
            "planmyagents_api.discovery.capability_index."
            "get_default_capability_index",
            return_value=_BoomIndex(),
        ):
            result = infer_capabilities_for_source(text="any input")

        self.assertEqual(set(), result)

    def test_keyword_extra_still_matches_when_embedder_raises(self) -> None:
        """Source-level keyword overrides MUST still apply even when the
        embedder is down. Otherwise an Ollama outage degrades discovery
        from "best-effort semantic + literal" to literally nothing.
        """

        from unittest.mock import patch

        from planmyagents_api.discovery.capability_index import (
            infer_capabilities_for_source,
        )
        from planmyagents_api.discovery.embeddings import EmbedderError

        class _BoomIndex:
            def infer_from_text(self, _text):
                raise EmbedderError("oversized input")

        with patch(
            "planmyagents_api.discovery.capability_index."
            "get_default_capability_index",
            return_value=_BoomIndex(),
        ):
            result = infer_capabilities_for_source(
                text="A description of a stripe payment integration",
                extra={"payment_authorization": {"stripe", "payment"}},
            )

        self.assertIn("payment_authorization", result)

    def test_fallback_still_applies_when_embedder_raises(self) -> None:
        from unittest.mock import patch

        from planmyagents_api.discovery.capability_index import (
            infer_capabilities_for_source,
        )
        from planmyagents_api.discovery.embeddings import EmbedderError

        class _BoomIndex:
            def infer_from_text(self, _text):
                raise EmbedderError("daemon restarted")

        with patch(
            "planmyagents_api.discovery.capability_index."
            "get_default_capability_index",
            return_value=_BoomIndex(),
        ):
            result = infer_capabilities_for_source(
                text="something",
                fallback="general_research",
            )

        self.assertEqual({"general_research"}, result)


class CapabilityIndexEmbedderFailureTests(unittest.TestCase):
    """Defence-in-depth: ``CapabilityIndex.match_slug`` and
    ``infer_from_text`` are called from both the planner path and
    the API search route. An embedder failure (Ollama OOM, oversized
    input, daemon restart) used to bubble out and 500 the user-
    facing /goal request even when the wrapper helpers had been
    hardened. These tests pin the new contract: both methods must
    treat embedder errors as a "no match / no inference" outcome,
    not raise.
    """

    def _build_index_with_boom_embedder(self) -> CapabilityIndex:
        from planmyagents_api.discovery.embeddings import EmbedderError

        @dataclass(frozen=True)
        class _BoomEmbedder:
            name: str = "boom"
            dimension: int = 4

            def encode(self, _text: str) -> list[float]:
                raise EmbedderError("simulated Ollama 500")

        # Use a real CapabilityIndex with a working embedder for the
        # registry encoding step (constructor encodes each capability
        # once at build time), then swap to the boom embedder for
        # query encoding. This mirrors the real failure mode where
        # the index is healthy at startup but the daemon falls over
        # mid-request.
        good = DeterministicHashEmbedder(dimension=4)
        index = CapabilityIndex(
            capability_ids=("payment_authorization", "store_locator"),
            embedder=good,
            match_threshold=0.30,
        )
        # Surgical swap of the private embedder reference. We don't
        # add a public setter because no production caller has a
        # reason to do this — the test is the only legitimate user.
        object.__setattr__(index, "_embedder", _BoomEmbedder())
        return index

    def test_match_slug_returns_none_when_embedder_raises(self) -> None:
        index = self._build_index_with_boom_embedder()
        # An exact-match slug short-circuits before encode(), so
        # use a non-exact slug to force the embedder path.
        result = index.match_slug("pay_processing")
        self.assertIsNone(result)

    def test_infer_from_text_returns_empty_set_when_embedder_raises(self) -> None:
        index = self._build_index_with_boom_embedder()
        result = index.infer_from_text("any non-empty query")
        self.assertEqual(set(), result)

    def test_match_slug_exact_match_unaffected_by_embedder_failure(self) -> None:
        """The exact-match short-circuit must NOT call the embedder.
        This is the property that makes the defence-in-depth safe:
        slugs the planner already wrote in canonical form (the
        common case in practice) keep working even when the
        embedder is completely down.
        """

        index = self._build_index_with_boom_embedder()
        result = index.match_slug("payment_authorization")
        self.assertEqual("payment_authorization", result)


if __name__ == "__main__":
    unittest.main()

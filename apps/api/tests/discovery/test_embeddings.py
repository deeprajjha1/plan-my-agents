from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DeterministicHashEmbedder,
    EmbedderError,
    OpenAIEmbedder,
    cosine_similarity,
    embedder_from_env,
)
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.discovery.service import (
    candidate_text_for_embedding,
    search_candidates_with_embeddings,
)
from planmyagents_api.discovery.store import JsonDiscoveryStore


class DeterministicHashEmbedderTest(unittest.TestCase):
    def test_dimension_is_configurable(self) -> None:
        embedder = DeterministicHashEmbedder(dimension=128)
        vector = embedder.encode("anything")
        self.assertEqual(len(vector), 128)

    def test_encoding_is_stable_across_calls(self) -> None:
        embedder = DeterministicHashEmbedder(dimension=64)
        first = embedder.encode("payment processing")
        second = embedder.encode("payment processing")
        self.assertEqual(first, second)

    def test_l2_normalised(self) -> None:
        embedder = DeterministicHashEmbedder(dimension=64)
        vector = embedder.encode("vector norm should be one")
        norm = sum(x * x for x in vector) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_empty_string_returns_zero_vector(self) -> None:
        embedder = DeterministicHashEmbedder(dimension=32)
        self.assertEqual(embedder.encode(""), [0.0] * 32)

    def test_shared_tokens_produce_higher_similarity_than_unrelated(self) -> None:
        embedder = DeterministicHashEmbedder(dimension=DEFAULT_EMBEDDING_DIM)
        crypto_query = embedder.encode("crypto payments stablecoin checkout")
        crypto_doc = embedder.encode("payments processor for crypto stablecoin merchant")
        unrelated = embedder.encode("flight booking and hotel reservation agent")
        self.assertGreater(
            cosine_similarity(crypto_query, crypto_doc),
            cosine_similarity(crypto_query, unrelated),
        )

    def test_factory_falls_back_to_deterministic_when_no_model_set(self) -> None:
        embedder = embedder_from_env()
        self.assertIsInstance(embedder, DeterministicHashEmbedder)


class SearchCandidatesWithEmbeddingsFallbackTest(unittest.TestCase):
    """The service layer must transparently fall back to in-memory text
    search when the store is not Postgres."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "discovery.json"
        store = JsonDiscoveryStore(self.path)
        store.save(
            [
                DiscoveryCandidate(
                    id="alpha-payments-agent",
                    display_name="Alpha Payments",
                    vendor="alphapay",
                    vendor_url="https://alphapay.example",
                    provider_type="ai_agent",
                    capabilities=[
                        CandidateCapability(id="payment_authorization", confidence=0.9)
                    ],
                ),
                DiscoveryCandidate(
                    id="beta-flight-agent",
                    display_name="Beta Flights",
                    vendor="betaair",
                    vendor_url="https://betaair.example",
                    provider_type="ai_agent",
                    capabilities=[CandidateCapability(id="travel_search", confidence=0.9)],
                ),
            ]
        )

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_fallback_to_in_memory_for_non_postgres_store(self) -> None:
        result = search_candidates_with_embeddings(
            store_url=str(self.path),
            query="payments authorization",
            capability="payment_authorization",
            limit=5,
        )
        self.assertEqual(result["backend"], "in_memory_text")
        self.assertEqual(result["embedder"], "deterministic-hash-v1")
        self.assertGreaterEqual(len(result["results"]), 1)
        ids = [item["provider_id"] for item in result["results"]]
        self.assertIn("alpha-payments-agent", ids)

    def test_provider_type_filter_excludes_other_types(self) -> None:
        result = search_candidates_with_embeddings(
            store_url=str(self.path),
            query="flight",
            capability="travel_search",
            provider_type="mcp_server",
            limit=5,
        )
        self.assertEqual(result["results"], [])


class SearchCandidatesWithEmbeddingsRoutingUnwrapTest(unittest.TestCase):
    """Regression for the audit-flagged pgvector bypass.

    `discovery_store_for_path` returns a `RoutingDiscoveryStore` facade
    that wraps the actual backend. The previous implementation did
    `isinstance(store, PostgresDiscoveryStore)` directly on the facade,
    which always failed and silently fell back to in-memory text search
    even when the operator had a pgvector-backed Postgres deployment.

    This test verifies we unwrap the routing facade BEFORE the
    isinstance check, so a Postgres-backed store actually routes
    through `search_by_embedding`.
    """

    def test_postgres_backed_routing_store_uses_pgvector_path(self) -> None:
        # Stand up a fake Postgres-backed routing store without a real
        # database. We subclass PostgresDiscoveryStore so the isinstance
        # check passes and override the two methods we touch.
        from planmyagents_api.discovery import service as discovery_service
        from planmyagents_api.discovery.store import (
            PostgresDiscoveryStore,
            RoutingDiscoveryStore,
        )

        # PostgresDiscoveryStore is a frozen dataclass, so the subclass
        # has to honour that — initialise via the parent constructor and
        # store mutable test state on a plain dict attached through
        # object.__setattr__ (the same pattern the real adapters use).
        class _FakePostgres(PostgresDiscoveryStore):
            def __init__(self) -> None:
                super().__init__(dsn="postgresql://fake/fake")
                object.__setattr__(self, "_search_calls", [])

            @property
            def search_calls(self):  # type: ignore[override]
                return self._search_calls

            def search_by_embedding(  # type: ignore[override]
                self,
                vector,
                *,
                capability=None,
                provider_type=None,
                limit=20,
            ):
                self._search_calls.append(
                    {
                        "vector_len": len(vector),
                        "capability": capability,
                        "provider_type": provider_type,
                        "limit": limit,
                    }
                )
                return [
                    {
                        "candidate": {
                            "id": "fake-pg-agent",
                            "display_name": "Fake Postgres Agent",
                            "vendor": "fakepg",
                            "vendor_url": "https://fakepg.example",
                            "provider_type": "ai_agent",
                            "capabilities": [
                                {"id": "payment_authorization", "confidence": 0.9}
                            ],
                        },
                        "similarity": 0.812345,
                    }
                ]

        class _FakeApisStore:
            def load(self):
                return []

            def save(self, _records):
                return None

        fake_pg = _FakePostgres()
        routing = RoutingDiscoveryStore(fake_pg, _FakeApisStore())

        original = discovery_service.discovery_store_for_path
        discovery_service.discovery_store_for_path = lambda _url: routing
        try:
            result = search_candidates_with_embeddings(
                store_url="postgresql://fake/fake",
                query="payment processor",
                capability="payment_authorization",
                limit=3,
                embedder=DeterministicHashEmbedder(dimension=DEFAULT_EMBEDDING_DIM),
            )
        finally:
            discovery_service.discovery_store_for_path = original

        self.assertEqual(result["backend"], "postgres+pgvector")
        self.assertEqual(len(fake_pg.search_calls), 1)
        self.assertEqual(fake_pg.search_calls[0]["capability"], "payment_authorization")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["provider_id"], "fake-pg-agent")
        self.assertEqual(result["results"][0]["embedding_similarity"], 0.8123)


class CandidateTextForEmbeddingTest(unittest.TestCase):
    def test_combines_name_vendor_capability_ids_and_notes(self) -> None:
        text = candidate_text_for_embedding(
            {
                "display_name": "Crypto Pay Agent",
                "vendor": "cryptopay-co",
                "provider_type": "ai_agent",
                "capabilities": [
                    {"id": "payment_authorization", "notes": "stablecoin checkout"},
                    "fraud_screening",
                ],
                "evidence_url": "https://cryptopay.example/agent.json",
            }
        )
        for needle in [
            "Crypto Pay Agent",
            "cryptopay-co",
            "ai_agent",
            "payment_authorization",
            "stablecoin checkout",
            "fraud_screening",
            "cryptopay.example",
        ]:
            self.assertIn(needle, text)

    def test_includes_probed_mcp_tool_names_and_descriptions(self) -> None:
        """Gap 1 regression: tool surface text must reach the embedder.

        A vanilla MCP catalogue entry is tagged with a generic capability
        like ``general_research`` but exposes a tool literally named
        ``send_email``. Without tool text in the embedding, semantic
        search for "send transactional email" never reaches that server.
        This test pins the contract that probed tools (``tools/list``)
        contribute their name + description to the embedding text.
        """

        text = candidate_text_for_embedding(
            {
                "display_name": "Mail MCP Server",
                "vendor": "mail-mcp",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "general_research", "notes": ""}],
                "tools": [
                    {
                        "name": "send_email",
                        "description": "Send a transactional email to a recipient",
                        "input_schema": {"title": "Recipient email address"},
                    },
                    {
                        "name": "list_inbox",
                        "description": "List recent inbox messages",
                    },
                ],
                "evidence_url": "https://mail-mcp.example/server.json",
            }
        )
        self.assertIn("send_email", text)
        self.assertIn("Send a transactional email", text)
        self.assertIn("Recipient email address", text)
        self.assertIn("list_inbox", text)
        self.assertIn("List recent inbox messages", text)

    def test_includes_a2a_skills_alongside_mcp_tools(self) -> None:
        """A2A providers expose ``skills`` instead of ``tools``; both
        describe a callable surface so both must contribute text."""

        text = candidate_text_for_embedding(
            {
                "display_name": "Refund Specialist Agent",
                "vendor": "agentlabs",
                "provider_type": "a2a_agent",
                "capabilities": [{"id": "refund_processing", "notes": ""}],
                "skills": [
                    {
                        "name": "issue_refund",
                        "description": "Issue a refund for a completed order",
                    }
                ],
            }
        )
        self.assertIn("issue_refund", text)
        self.assertIn("Issue a refund for a completed order", text)

    def test_truncates_very_long_tool_descriptions_per_tool(self) -> None:
        """Per-tool truncation prevents one chatty tool from starving the
        embedding budget. The embedder also truncates downstream, but
        per-tool capping happens at a meaningful boundary instead of
        slicing tool #2 in half."""

        long_description = "x" * 5000
        text = candidate_text_for_embedding(
            {
                "display_name": "Verbose Server",
                "provider_type": "mcp_server",
                "tools": [
                    {"name": "alpha", "description": long_description},
                    {"name": "beta", "description": "short"},
                ],
            }
        )
        # Both tool names survive, even though `alpha`'s description is
        # vastly larger than the per-tool budget — the cap is per-field,
        # not per-document.
        self.assertIn("alpha", text)
        self.assertIn("beta", text)
        self.assertIn("short", text)
        # The huge description is sliced down — at most the per-tool cap
        # plus a tiny separator before the next field. We assert "much
        # less than the input" rather than a precise length so the test
        # is robust to small wording changes in adjacent fields.
        self.assertLess(text.count("x"), 1000)

    def test_caps_total_number_of_embedded_tools(self) -> None:
        """Only the first 32 callable entries (tools + skills, in order)
        contribute to the embedding text. Servers like Smithery routinely
        expose 80+ tools; embedding all of them wastes the limited
        embedder window without proportionally improving recall."""

        many_tools = [
            {"name": f"tool_{i:03d}", "description": f"desc {i}"}
            for i in range(64)
        ]
        text = candidate_text_for_embedding(
            {
                "display_name": "Big Server",
                "provider_type": "mcp_server",
                "tools": many_tools,
            }
        )
        self.assertIn("tool_000", text)
        self.assertIn("tool_031", text)
        # 32-onwards must be excluded — they exceed _MAX_TOOLS_EMBEDDED.
        self.assertNotIn("tool_032", text)
        self.assertNotIn("tool_063", text)

    def test_silently_skips_malformed_tool_entries(self) -> None:
        """Discovery payloads are union-shaped across many sources. Bad
        rows must not crash the embedder — they're silently skipped."""

        text = candidate_text_for_embedding(
            {
                "display_name": "Mixed Server",
                "provider_type": "mcp_server",
                "tools": [
                    None,
                    "not-a-dict",
                    {"name": "", "description": "no name → skipped"},
                    {"description": "no name field at all"},
                    {"name": "valid_tool", "description": "kept"},
                ],
            }
        )
        self.assertIn("valid_tool", text)
        self.assertIn("kept", text)
        self.assertNotIn("no name → skipped", text)
        self.assertNotIn("no name field at all", text)

    def test_tools_field_absent_behaves_as_before_gap_one(self) -> None:
        """Backwards compat: a candidate payload with no ``tools`` field
        must produce exactly the same text as the pre-Gap-1 implementation
        (display_name + vendor + provider_type + capability id/notes +
        evidence_url). This protects every cached embedding written by
        save_merge before this change."""

        text = candidate_text_for_embedding(
            {
                "display_name": "Crypto Pay Agent",
                "vendor": "cryptopay-co",
                "provider_type": "ai_agent",
                "capabilities": [
                    {"id": "payment_authorization", "notes": "stablecoin checkout"},
                ],
                "evidence_url": "https://cryptopay.example/agent.json",
            }
        )
        self.assertEqual(
            text,
            (
                "Crypto Pay Agent cryptopay-co ai_agent "
                "payment_authorization stablecoin checkout "
                "https://cryptopay.example/agent.json"
            ),
        )


class OpenAIEmbedderTest(unittest.TestCase):
    """Network is monkey-patched — these tests exercise request shape,
    response parsing, and error handling without ever calling OpenAI.
    """

    def _fake_response(self, body: bytes):
        import io

        return io.BytesIO(body)

    def test_encode_sends_correct_request_shape(self) -> None:
        from unittest.mock import patch

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["body"] = req.data
            captured["headers"] = dict(req.header_items())
            return self._fake_response(
                b'{"data":[{"embedding":[0.1, 0.2, 0.3]}]}'
            )

        embedder = OpenAIEmbedder(api_key="sk-test", dimension=3)
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            vec = embedder.encode("hello world")

        self.assertEqual(len(vec), 3)
        # Vector is L2-normalised (sum of squares ≈ 1).
        self.assertAlmostEqual(sum(x * x for x in vec), 1.0, places=6)
        self.assertEqual(
            captured["url"], "https://api.openai.com/v1/embeddings"
        )
        # Auth header must be set.
        headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(
            headers_lower["authorization"], "Bearer sk-test"
        )
        # Body is the requested model + input.
        import json as _json

        sent_body = _json.loads(captured["body"])
        self.assertEqual(sent_body["model"], "text-embedding-3-small")
        self.assertEqual(sent_body["input"], "hello world")

    def test_encode_returns_zero_vector_for_empty_input(self) -> None:
        embedder = OpenAIEmbedder(api_key="sk-test", dimension=4)
        self.assertEqual([0.0, 0.0, 0.0, 0.0], embedder.encode(""))

    def test_encode_raises_on_http_error(self) -> None:
        from unittest.mock import patch
        from urllib import error as urllib_error

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            import io as _io

            raise urllib_error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, _io.BytesIO(b"")
            )

        embedder = OpenAIEmbedder(api_key="sk-bad")
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            with self.assertRaises(EmbedderError):
                embedder.encode("hello")

    def test_encode_raises_on_malformed_response(self) -> None:
        from unittest.mock import patch

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            return self._fake_response(b'{"not": "what we expect"}')

        embedder = OpenAIEmbedder(api_key="sk-test")
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            with self.assertRaises(EmbedderError):
                embedder.encode("hello")

    def test_name_default_includes_model(self) -> None:
        embedder = OpenAIEmbedder(api_key="sk-test")
        self.assertEqual("openai:text-embedding-3-small", embedder.name)
        custom = OpenAIEmbedder(
            api_key="sk-test", model="text-embedding-3-large"
        )
        self.assertEqual("openai:text-embedding-3-large", custom.name)

    def test_long_input_is_truncated_before_send(self) -> None:
        """Regression: APIs.guru ships ``description`` fields up to ~50KB
        which overruns the OpenAI embeddings context window and 400s
        the request mid-/goal flow. Truncation MUST happen at the
        embedder boundary so no caller has to know the model's limit.
        """

        from unittest.mock import patch

        from planmyagents_api.discovery.embeddings import (
            MAX_INPUT_CHARS_OPENAI,
        )

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["body"] = req.data
            return self._fake_response(
                b'{"data":[{"embedding":[0.1, 0.2, 0.3]}]}'
            )

        embedder = OpenAIEmbedder(api_key="sk-test", dimension=3)
        oversized = "a" * (MAX_INPUT_CHARS_OPENAI * 3)
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            embedder.encode(oversized)

        import json as _json

        sent_body = _json.loads(captured["body"])
        self.assertEqual(len(sent_body["input"]), MAX_INPUT_CHARS_OPENAI)


class OllamaEmbedderInputTruncationTest(unittest.TestCase):
    """Regression: nomic-embed-text 500s on inputs that exceed its
    context window. Truncation prevents the upstream from ever
    seeing oversized content.
    """

    def _fake_response(self, body: bytes):
        import io

        return io.BytesIO(body)

    def test_long_input_is_truncated_before_send(self) -> None:
        from unittest.mock import patch

        from planmyagents_api.discovery.embeddings import (
            MAX_INPUT_CHARS_OLLAMA,
            OllamaEmbedder,
        )

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["body"] = req.data
            return self._fake_response(b'{"embedding":[0.1, 0.2, 0.3]}')

        embedder = OllamaEmbedder(model="nomic-embed-text", dimension=3)
        oversized = "z" * (MAX_INPUT_CHARS_OLLAMA * 5)
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            embedder.encode(oversized)

        import json as _json

        sent_body = _json.loads(captured["body"])
        self.assertEqual(len(sent_body["prompt"]), MAX_INPUT_CHARS_OLLAMA)

    def test_short_input_is_not_truncated(self) -> None:
        """Truncation must be a no-op for normal-sized inputs.
        Otherwise we might be inadvertently mutating data that the
        embedder is supposed to see verbatim.
        """

        from unittest.mock import patch

        from planmyagents_api.discovery.embeddings import OllamaEmbedder

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["body"] = req.data
            return self._fake_response(b'{"embedding":[0.1, 0.2, 0.3]}')

        embedder = OllamaEmbedder(model="nomic-embed-text", dimension=3)
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            embedder.encode("verify these emails")

        import json as _json

        sent_body = _json.loads(captured["body"])
        self.assertEqual(sent_body["prompt"], "verify these emails")


class OllamaConcurrencyLimitTest(unittest.TestCase):
    """Verify the module-level Ollama semaphore caps the number of
    in-flight ``encode`` calls. Without this cap the daemon
    routinely 500s under burst load (multiple scouts × multiple
    candidates × per-source classification) — the tests pin the
    contract that adding more callers cannot exceed the semaphore
    capacity.
    """

    def test_concurrent_encodes_cap_at_semaphore_capacity(self) -> None:
        """At most ``_OLLAMA_CONCURRENCY`` encode() calls can be in
        flight simultaneously. The test launches twice that many
        threads and asserts the high-water mark never exceeds the
        cap.
        """

        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch

        from planmyagents_api.discovery.embeddings import (
            _OLLAMA_CONCURRENCY,
            OllamaEmbedder,
        )

        in_flight = 0
        peak = 0
        lock = threading.Lock()
        proceed = threading.Event()

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            import io
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # Hold the connection until every thread has reached
            # this point (or close to it) so the semaphore actually
            # has to gate the latecomers. Bounded wait keeps the
            # test from hanging if something goes wrong.
            proceed.wait(timeout=2.0)
            with lock:
                in_flight -= 1
            return io.BytesIO(b'{"embedding":[0.1,0.2,0.3]}')

        embedder = OllamaEmbedder(model="nomic-embed-text", dimension=3)
        worker_count = _OLLAMA_CONCURRENCY * 2

        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [
                    pool.submit(embedder.encode, f"text-{i}")
                    for i in range(worker_count)
                ]
                # Give the first batch time to be admitted by the
                # semaphore but blocked on ``proceed`` before
                # releasing the gate. Without this delay the test
                # could spuriously pass by serialising the calls.
                time.sleep(0.05)
                proceed.set()
                for future in futures:
                    future.result(timeout=3.0)

        self.assertLessEqual(peak, _OLLAMA_CONCURRENCY)


class OllamaEmbedderTransportRetryTest(unittest.TestCase):
    """Verify ``OllamaEmbedder`` retries once on transient transport
    failure. Cold model loads (model unloaded after idle timeout, then
    reloaded mid-call) routinely produce one connection-reset before
    the daemon stabilises; without a retry, every cold-load triggers
    an :class:`EmbedderError` in production.
    """

    def _success_response(self, body: bytes):
        import io

        return io.BytesIO(body)

    def test_retry_recovers_from_transient_transport_failure(self) -> None:
        from unittest.mock import patch
        from urllib import error as urllib_error

        from planmyagents_api.discovery.embeddings import OllamaEmbedder

        attempts: list[int] = []

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            attempts.append(1)
            if len(attempts) == 1:
                # First call: simulate transient connection reset.
                raise urllib_error.URLError("connection reset by peer")
            return self._success_response(b'{"embedding":[0.1,0.2,0.3]}')

        embedder = OllamaEmbedder(
            model="nomic-embed-text", dimension=3, timeout_seconds=0.5
        )
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            vec = embedder.encode("hello")

        self.assertEqual(len(vec), 3)
        # Second attempt succeeded — proves the retry runs.
        self.assertEqual(len(attempts), 2)

    def test_two_consecutive_failures_raise(self) -> None:
        from unittest.mock import patch
        from urllib import error as urllib_error

        from planmyagents_api.discovery.embeddings import EmbedderError, OllamaEmbedder

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            raise urllib_error.URLError("daemon down")

        embedder = OllamaEmbedder(
            model="nomic-embed-text", dimension=3, timeout_seconds=0.5
        )
        with patch(
            "planmyagents_api.discovery.embeddings.request.urlopen",
            fake_urlopen,
        ):
            with self.assertRaises(EmbedderError) as ctx:
                embedder.encode("hello")

        # Error message should mention "after retry" so the operator
        # can distinguish a one-shot error from a retry-exhaustion.
        self.assertIn("after retry", str(ctx.exception))


class CachedEmbedderTest(unittest.TestCase):
    """The ``CachedEmbedder`` LRU wrapper is the single biggest Ollama
    contention fix: it caches repeated registry-text encode() calls so
    the second-and-subsequent occurrences never hit Ollama at all.
    These tests pin the contract.
    """

    def _make_counting_embedder(self):
        """A tiny embedder that counts how many encode() calls it has
        served. Lets the test assert "the second call hit cache, the
        inner backend was not invoked again".
        """

        class CountingEmbedder:
            dimension = 4
            name = "counting"

            def __init__(self) -> None:
                self.calls = 0

            def encode(self, text: str) -> list[float]:  # noqa: ARG002
                self.calls += 1
                # A unique vector per call so we can detect a stale
                # cache entry returning yesterday's vector.
                base = float(self.calls)
                return [base, base, base, base]

        return CountingEmbedder()

    def test_repeated_encode_with_same_text_hits_cache(self) -> None:
        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=10)

        first = cached.encode("payment processing")
        second = cached.encode("payment processing")
        third = cached.encode("payment processing")

        # Inner was called exactly once — second and third are cache hits.
        self.assertEqual(inner.calls, 1)
        # All three callers got the SAME vector (proves cache returns
        # the original answer, not a stale or fresh one).
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        stats = cached.cache_stats()
        self.assertEqual(stats["hits"], 2)
        self.assertEqual(stats["misses"], 1)

    def test_distinct_text_misses_cache(self) -> None:
        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=10)

        cached.encode("alpha")
        cached.encode("beta")
        cached.encode("gamma")

        # Three distinct inputs → three inner encode calls.
        self.assertEqual(inner.calls, 3)
        stats = cached.cache_stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 3)

    def test_cache_eviction_at_max_size(self) -> None:
        """LRU eviction must drop the oldest entry when capacity is hit.
        Otherwise a long-running process accumulates unbounded memory
        keyed on every unique text it has ever embedded.
        """

        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=2)

        cached.encode("alpha")  # in cache
        cached.encode("beta")   # in cache
        cached.encode("gamma")  # evicts alpha
        # Re-querying alpha must miss (proves it was evicted) and bump
        # the inner count.
        before = inner.calls
        cached.encode("alpha")
        self.assertEqual(inner.calls, before + 1)

    def test_returned_vector_is_a_copy(self) -> None:
        """Mutating a returned vector must not corrupt the cache —
        callers that .extend() or .pop() the result would otherwise
        poison every subsequent hit.
        """

        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=10)

        first = cached.encode("alpha")
        first.append(999.0)  # mutate the returned list

        second = cached.encode("alpha")
        # Cache hit, but the cached vector must still be intact.
        self.assertEqual(len(second), 4)
        self.assertNotIn(999.0, second)

    def test_max_size_zero_disables_caching(self) -> None:
        """Operators set ``PLANMYAGENTS_EMBEDDING_CACHE_SIZE=0`` to
        disable. The wrapper must then be a transparent passthrough.
        """

        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=0)

        cached.encode("alpha")
        cached.encode("alpha")
        cached.encode("alpha")
        # Every call hits the inner — no caching at all.
        self.assertEqual(inner.calls, 3)

    def test_dimension_and_name_proxied_from_inner(self) -> None:
        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=10)

        self.assertEqual(cached.dimension, inner.dimension)
        self.assertIn(inner.name, cached.name)

    def test_clear_resets_cache_and_counters(self) -> None:
        from planmyagents_api.discovery.embeddings import CachedEmbedder

        inner = self._make_counting_embedder()
        cached = CachedEmbedder(inner, max_size=10)

        cached.encode("alpha")
        cached.encode("alpha")  # hit
        cached.clear()
        cached.encode("alpha")  # miss again after clear
        self.assertEqual(inner.calls, 2)
        stats = cached.cache_stats()
        self.assertEqual(stats["size"], 1)
        # ``hits`` and ``misses`` reset to 0 then misses bumped to 1.
        self.assertEqual(stats["misses"], 1)


class EmbedderFromEnvProviderSelectionTest(unittest.TestCase):
    """Verify ``embedder_from_env`` picks the provider per Sprint 2's
    documented priority: openai > ollama > hash.
    """

    def setUp(self) -> None:
        # Capture pre-test env so we can restore.
        import os

        self._saved = {
            k: os.environ.get(k)
            for k in (
                "PLANMYAGENTS_EMBEDDING_PROVIDER",
                "PLANMYAGENTS_EMBEDDING_MODEL",
                "OPENAI_API_KEY",
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        import os

        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_openai_chosen_when_provider_and_key_set(self) -> None:
        import os

        os.environ["PLANMYAGENTS_EMBEDDING_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-x"
        # Ollama model is also set — openai must still win.
        os.environ["PLANMYAGENTS_EMBEDDING_MODEL"] = "nomic-embed-text"
        self.assertIsInstance(embedder_from_env(), OpenAIEmbedder)

    def test_openai_skipped_without_api_key(self) -> None:
        import os

        os.environ["PLANMYAGENTS_EMBEDDING_PROVIDER"] = "openai"
        # Falls through — no key set, so we go to the next priority.
        # No ollama model set either, so it lands on the hash embedder.
        self.assertIsInstance(embedder_from_env(), DeterministicHashEmbedder)

    def test_falls_through_to_hash_when_nothing_set(self) -> None:
        self.assertIsInstance(embedder_from_env(), DeterministicHashEmbedder)


if __name__ == "__main__":
    unittest.main()

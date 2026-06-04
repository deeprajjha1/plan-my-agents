from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

# Disable the discovery run-event audit log for this test module —
# `build_discovery_index` would otherwise write JSONL events into
# `apps/data/discovery_run_events.jsonl` as a side effect. The audit
# log itself is exercised by `test_discovery_run_log.py`.
os.environ.setdefault("PLANMYAGENTS_RUN_LOG_ENABLED", "false")

from planmyagents_api.discovery.freshness import freshness_status
from planmyagents_api.discovery.gaps import build_gap_report
from planmyagents_api.discovery.index import DiscoveryIndex
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.discovery.query import expand_discovery_query
from planmyagents_api.discovery.service import build_discovery_index, search_candidates
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource
from planmyagents_api.discovery.sources.json_source import JsonDiscoverySource
from planmyagents_api.discovery.sources.live import (
    GitHubCodeSearchSource,
    LiveA2AAgentCardSource,
    LiveAgentMarketplaceSource,
    LiveMcpRegistrySource,
    LiveOpenApiSpecSource,
    LiveUrlDirectorySource,
    LiveVendorDocsSource,
    LiveWebSearchSource,
)
from planmyagents_api.discovery.sources.mcp import McpCatalogSource
from planmyagents_api.discovery.sources.research import GitHubResearchSource, UrlResearchSource
from planmyagents_api.discovery.sources.static import StaticDiscoverySource
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource
from planmyagents_api.discovery.store import (
    JsonDiscoveryStore,
    PostgresDiscoveryStore,
    SqliteDiscoveryStore,
    discovery_store_for_path,
)
from planmyagents_api.planner.capability_catalog import (
    build_capability_catalog,
    infer_capabilities_from_catalog,
)
from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask


class DiscoveryIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        # Defensive: clear the capability-index LRU caches so this
        # test class is order-independent. Some other test in the
        # suite (test_capability_index, the extended_capability_index
        # fixture, or anything that constructs a discovery source
        # before our test runs) can otherwise leak a CapabilityIndex
        # built from a stale registry tuple into the lru_cache, at
        # which point our ``inferred_capabilities`` assertions would
        # see an empty list instead of real inferences.
        from planmyagents_api.discovery.capability_index import (
            _build_default_index,
            _load_default_registry_capabilities,
        )

        _load_default_registry_capabilities.cache_clear()
        _build_default_index.cache_clear()

    def test_searches_static_source_by_capability(self) -> None:
        index = build_discovery_index(
            capabilities={"travel_search"},
            task_description="find flights from Bengaluru to San Francisco",
            sources=[StaticDiscoverySource()],
        )

        results = index.search(capabilities={"travel_search"}, task_description="find flights")

        self.assertGreaterEqual(len(results), 2)
        self.assertEqual("api_provider", results[0].candidate.provider_type)
        self.assertTrue(results[0].candidate.will_fail)

    def test_dedupes_candidates_by_domain(self) -> None:
        first = normalize_candidate(
            {
                "id": "duffel",
                "display_name": "Duffel",
                "vendor": "Duffel",
                "vendor_url": "https://duffel.com/docs",
                "provider_type": "api_provider",
                "capabilities": [{"id": "travel_search", "confidence": 0.8}],
                "required_env_vars": ["DUFFEL_API_TOKEN"],
            },
            source="test",
        )
        second = normalize_candidate(
            {
                "id": "duffel-api",
                "display_name": "Duffel API",
                "vendor": "Duffel",
                "vendor_url": "https://www.duffel.com",
                "provider_type": "api_provider",
                "capabilities": [{"id": "booking_execution", "confidence": 0.7}],
                "required_env_vars": ["DUFFEL_API_TOKEN"],
            },
            source="test",
        )
        index = DiscoveryIndex()

        index.ingest([first, second])

        candidates = index.all_candidates()
        self.assertEqual(1, len(candidates))
        self.assertEqual(
            {"travel_search", "booking_execution"}, {cap.id for cap in candidates[0].capabilities}
        )

    def test_keeps_agentic_candidates_distinct_on_shared_catalog_domains(self) -> None:
        first = normalize_candidate(
            {
                "id": "github-mcp",
                "display_name": "GitHub MCP",
                "vendor": "MCP",
                "vendor_url": "https://github.com/modelcontextprotocol/servers",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "code_search", "confidence": 0.7}],
            },
            source="test",
        )
        second = normalize_candidate(
            {
                "id": "slack-mcp",
                "display_name": "Slack MCP",
                "vendor": "MCP",
                "vendor_url": "https://github.com/modelcontextprotocol/servers",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "team_messaging", "confidence": 0.7}],
            },
            source="test",
        )
        index = DiscoveryIndex()

        index.ingest([first, second])

        self.assertEqual(2, len(index.all_candidates()))

    def test_prioritizes_agentic_candidates_before_api_candidates(self) -> None:
        mcp = normalize_candidate(
            {
                "id": "flight-mcp",
                "display_name": "Flight MCP",
                "vendor": "Flight MCP",
                "vendor_url": "https://flight-mcp.example",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "travel_search", "confidence": 0.7}],
            },
            source="test",
        )
        api = normalize_candidate(
            {
                "id": "flight-api",
                "display_name": "Flight API",
                "vendor": "Flight API",
                "vendor_url": "https://flight-api.example",
                "provider_type": "api_provider",
                "capabilities": [{"id": "travel_search", "confidence": 0.95}],
            },
            source="test",
        )
        index = DiscoveryIndex()

        index.ingest([api, mcp])
        results = index.search(capabilities={"travel_search"}, task_description="find flights")

        self.assertEqual("flight-mcp", results[0].candidate.id)

    def test_capability_queries_exclude_text_only_matches(self) -> None:
        matching = normalize_candidate(
            {
                "id": "flight-mcp",
                "display_name": "Flight MCP",
                "vendor": "Flight MCP",
                "vendor_url": "https://flight-mcp.example",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "travel_search", "confidence": 0.7}],
            },
            source="test",
        )
        text_only = normalize_candidate(
            {
                "id": "search-only-mcp",
                "display_name": "Search Only MCP",
                "vendor": "Search Only MCP",
                "vendor_url": "https://search-only.example",
                "provider_type": "mcp_server",
                # Test-only capability slug that is GUARANTEED not in
                # the live registry, so ``expand_discovery_query`` will
                # not infer it from the task text. The test would
                # otherwise be fragile against future registry
                # expansions (e.g. adding a real ``code_search`` slug
                # would silently change semantics here).
                "capabilities": [
                    {"id": "test_only_unrelated_cap", "confidence": 0.7}
                ],
            },
            source="test",
        )
        index = DiscoveryIndex()

        index.ingest([matching, text_only])
        results = index.search(
            capabilities={"travel_search"}, task_description="find flight search agents"
        )

        self.assertEqual(["flight-mcp"], [result.candidate.id for result in results])

    def test_query_expansion_returns_empty_normalizations(self) -> None:
        # ``COMMON_NORMALIZATIONS`` (the typo→correction lookup) was
        # deleted — it was a tiny geographic spell-checker biased toward
        # India/US English that didn't generalise. The ``normalizations``
        # field is preserved on ExpandedDiscoveryQuery for callers that
        # render it in API payloads, but it's now always an empty dict.
        # This test locks that contract so we don't accidentally
        # reintroduce a hardcoded typo table.
        expanded = expand_discovery_query(
            capabilities=set(),
            task_description="Book cheapest flight ticker to san fransciso",
        )
        self.assertEqual({}, expanded.normalizations)

    def test_capability_catalog_infers_lodging_from_source_candidates(self) -> None:
        catalog = build_capability_catalog(
            [
                normalize_candidate(
                    {
                        "id": "test-lodging-booking-agent",
                        "display_name": "Test Lodging Booking Agent",
                        "vendor": "Test A2A Directory",
                        "vendor_url": "https://example.com",
                        "provider_type": "a2a_agent",
                        "capabilities": [
                            {"id": "lodging_search", "notes": "Find hotel stays."},
                            {"id": "lodging_comparison", "notes": "Compare lodging ratings."},
                            {"id": "booking_execution", "notes": "Reserve a room."},
                        ],
                    },
                    source="test",
                ),
                normalize_candidate(
                    {
                        "id": "payment-agent",
                        "display_name": "Payment Agent",
                        "vendor": "Payment Agent",
                        "vendor_url": "https://example.com/pay",
                        "provider_type": "a2a_agent",
                        "capabilities": [{"id": "payment_authorization"}],
                    },
                    source="test",
                ),
            ]
        )

        inferred = infer_capabilities_from_catalog(
            "book me a decent hotel in san fransisco", catalog
        )

        self.assertIn("lodging_search", inferred)
        self.assertIn("lodging_comparison", inferred)
        self.assertIn("booking_execution", inferred)
        # NOTE: ``payment_authorization`` is no longer auto-inferred for
        # any query containing booking/order vocabulary — that was the
        # deleted ``if terms & BOOKING_TERMS: inferred.add("payment_*")``
        # bias. If a goal genuinely needs payment, the planner should
        # say so and the catalog will pick it up via the candidate's own
        # alias bag (which here contains "pay", "payment",
        # "authorization"), not via a hardcoded vertical assumption that
        # "book" implies "pay".
        self.assertNotIn("payment_authorization", inferred)

    def test_query_expansion_does_not_overinfer_generic_search_for_domain_tasks(self) -> None:
        # Regression for the deleted CAPABILITY_SYNONYMS table, which
        # used to fire `semantic_search` for any query containing the
        # word "find" or "search" — including domain queries like
        # "find flight booking agents" where semantic_search isn't the
        # right tag. Under the embedding-based inference, the query
        # "find flight booking agents" has no token overlap with
        # ``semantic search`` (the tokenised form of the registry slug)
        # strong enough to clear the threshold, so semantic_search is
        # correctly absent from the inferred set.
        expanded = expand_discovery_query(
            capabilities=set(),
            task_description="find flight booking agents",
        )

        self.assertNotIn("semantic_search", expanded.inferred_capabilities)

    def test_query_expansion_does_not_add_generic_search_when_capabilities_are_explicit(self) -> None:
        expanded = expand_discovery_query(
            capabilities={"product_search", "price_comparison", "shipping_quote"},
            task_description="find tennis shoes under 100 usd shipped to Bali",
        )

        self.assertNotIn("semantic_search", expanded.inferred_capabilities)

    def test_json_source_ingests_file_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "candidates.json"
            source_path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "id": "travel-agent-api",
                                "display_name": "Travel Agent API",
                                "vendor": "Travel Agent",
                                "vendor_url": "https://travel.example",
                                "provider_type": "api_provider",
                                "capabilities": [{"id": "travel_search", "confidence": 0.8}],
                            }
                        ]
                    }
                )
            )

            source = JsonDiscoverySource([str(source_path)])
            candidates = source.search(
                capabilities={"travel_search"}, task_description="find flights"
            )

        self.assertEqual(["travel-agent-api"], [candidate.id for candidate in candidates])

    def test_mcp_and_a2a_sources_ingest_agentic_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mcp_path = Path(directory) / "mcp.json"
            a2a_path = Path(directory) / "a2a.json"
            mcp_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "flight-mcp",
                                "publisher": "Flight Tools",
                                "homepage": "https://flight-mcp.example",
                                "tools": [{"name": "travel_search"}],
                            }
                        ]
                    }
                )
            )
            a2a_path.write_text(
                json.dumps(
                    {
                        "agent_cards": [
                            {
                                "name": "fare-agent",
                                "provider": {
                                    "name": "Fare Agent",
                                    "url": "https://fare-agent.example",
                                },
                                "skills": [{"name": "fare_comparison"}],
                            }
                        ]
                    }
                )
            )

            candidates = [
                *McpCatalogSource([str(mcp_path)]).search(
                    capabilities={"travel_search"}, task_description="flights"
                ),
                *A2AAgentCardSource([str(a2a_path)]).search(
                    capabilities={"fare_comparison"},
                    task_description="compare fares",
                ),
            ]

        self.assertEqual(
            {"mcp_server", "a2a_agent"}, {candidate.provider_type for candidate in candidates}
        )

    def test_search_candidates_uses_store_and_persists_results(self) -> None:
        # Uses ``email_verification`` (a registry-known capability)
        # rather than ``travel_search`` (which isn't in the registry,
        # and therefore can't be inferred under the new
        # CapabilityIndex-driven inference). The original test was
        # written when the CAPABILITY_SYNONYMS table mapped "flight"
        # → "travel_search" without checking registry membership.
        # Also defensive: re-import + clear caches inside the test
        # in case any module imported during this same test run has
        # captured a stale ``DEFAULT_MATCH_THRESHOLD`` from the
        # production .env (which sets 0.55 — too tight for the hash
        # embedder used in tests). See ``tests/__init__.py``.
        from planmyagents_api.discovery import capability_index as _ci

        _ci.DEFAULT_MATCH_THRESHOLD = 0.30
        _ci._build_default_index.cache_clear()
        _ci._load_default_registry_capabilities.cache_clear()

        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "discovery-store.sqlite"
            stored = normalize_candidate(
                {
                    "id": "stored-email-mcp",
                    "display_name": "Stored Email MCP",
                    "vendor": "Stored Email MCP",
                    "vendor_url": "https://stored-email.example",
                    "provider_type": "mcp_server",
                    "capabilities": [{"id": "email_verification", "confidence": 0.8}],
                },
                source="test",
            )
            SqliteDiscoveryStore(store_path).save([stored])

            payload = search_candidates(
                capabilities=set(),
                task_description="verify email addresses for outreach",
                sources=[StaticDiscoverySource(raw_candidates=[])],
                store_path=store_path,
            )

        provider_ids = [result["provider_id"] for result in payload["results"]]
        self.assertIn("stored-email-mcp", provider_ids)
        self.assertIn("email_verification", payload["inferred_capabilities"])

    def test_metadata_bag_round_trips_through_normalizer(self) -> None:
        """The free-form ``metadata`` bag introduced in Sprint 2 must
        survive a normalize → to_registry_json → re-normalize cycle.
        Sources (Smithery, Marketplace, Moltbook) populate it with
        provenance signals that the judge prompt and the frontend
        depend on; if the round-trip drops them, those downstream
        consumers see ``{}`` and silently degrade.
        """

        candidate = normalize_candidate(
            {
                "id": "metadata-test-mcp",
                "display_name": "Metadata Test MCP",
                "vendor": "MetadataCo",
                "vendor_url": "https://metadata.example",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "email_verification"}],
                "metadata": {
                    "smithery_use_count": 1234,
                    "smithery_is_deployed": True,
                    "marketplace_github_stars": 4567,
                    "marketplace_install_command": "npx -y x",
                    "moltbook_karma": 42,
                },
            },
            source="test",
        )

        # Direct read.
        self.assertEqual(1234, candidate.metadata["smithery_use_count"])
        self.assertTrue(candidate.metadata["smithery_is_deployed"])
        self.assertEqual(4567, candidate.metadata["marketplace_github_stars"])

        # Round-trip through the registry-shaped JSON path.
        round_tripped = normalize_candidate(
            candidate.to_registry_json(), source="test"
        )
        self.assertEqual(candidate.metadata, round_tripped.metadata)

        # Public summary surfaces it for the frontend.
        summary = candidate.to_public_summary()
        self.assertEqual(candidate.metadata, summary["metadata"])

    def test_metadata_bag_drops_non_json_safe_values(self) -> None:
        """Sources sometimes attempt to push raw upstream objects
        (sets, custom dataclasses, bytes) into the metadata bag. The
        normalizer is the chokepoint — non-JSON-safe values must be
        silently dropped so a buggy source can't poison the JSONB
        column.
        """

        candidate = normalize_candidate(
            {
                "id": "metadata-junk-mcp",
                "display_name": "Junk MCP",
                "vendor": "JunkCo",
                "vendor_url": "https://junk.example",
                "provider_type": "mcp_server",
                "capabilities": [{"id": "email_verification"}],
                "metadata": {
                    "valid_int": 1,
                    "valid_str": "ok",
                    "valid_bool": True,
                    "valid_list": [1, "two", False],
                    "valid_nested": {"a": [1, 2]},
                    "junk_set": {1, 2, 3},
                    "junk_bytes": b"binary",
                    "junk_object": object(),
                    "": "blank-key-dropped",
                },
            },
            source="test",
        )

        self.assertEqual(
            {
                "valid_int": 1,
                "valid_str": "ok",
                "valid_bool": True,
                "valid_list": [1, "two", False],
                "valid_nested": {"a": [1, 2]},
            },
            candidate.metadata,
        )

    def test_sqlite_store_round_trips_candidate_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteDiscoveryStore(Path(directory) / "discovery-store.sqlite")
            candidate = normalize_candidate(
                {
                    "id": "verified-flight-mcp",
                    "display_name": "Verified Flight MCP",
                    "vendor": "Verified Flight MCP",
                    "vendor_url": "https://verified-flight.example",
                    "provider_type": "mcp_server",
                    "verification_status": "capability_verified",
                    "evidence_url": "https://verified-flight.example/agent-card.json",
                    "capabilities": [{"id": "travel_search", "confidence": 0.8}],
                },
                source="test",
            )

            store.save([candidate])
            loaded = store.load()

        self.assertEqual("verified-flight-mcp", loaded[0].id)
        self.assertEqual("capability_verified", loaded[0].verification_status)
        self.assertEqual("https://verified-flight.example/agent-card.json", loaded[0].evidence_url)

    def test_store_factory_accepts_postgres_urls(self) -> None:
        store = discovery_store_for_path("postgresql://planmyagents:planmyagents@localhost:5432/planmyagents")

        # Factory now returns a RoutingDiscoveryStore that wraps both the
        # agentic Postgres store and the parallel apis_without_agents
        # Postgres store. Assert on the wrapped agentic store.
        self.assertIsInstance(store.agentic_store, PostgresDiscoveryStore)

    def test_search_candidates_can_exclude_stale_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "discovery-store.json"
            fresh = normalize_candidate(
                {
                    "id": "fresh-flight-mcp",
                    "display_name": "Fresh Flight MCP",
                    "vendor": "Fresh Flight MCP",
                    "vendor_url": "https://fresh-flight.example",
                    "provider_type": "mcp_server",
                    "capabilities": [{"id": "travel_search", "confidence": 0.8}],
                },
                source="test",
            )
            stale = normalize_candidate(
                {
                    "id": "stale-flight-mcp",
                    "display_name": "Stale Flight MCP",
                    "vendor": "Stale Flight MCP",
                    "vendor_url": "https://stale-flight.example",
                    "provider_type": "mcp_server",
                    "capabilities": [{"id": "travel_search", "confidence": 0.8}],
                },
                source="test",
            )
            stale = DiscoveryCandidate(
                **{
                    **stale.__dict__,
                    "first_seen_at": "2020-01-01",
                    "last_seen_at": "2020-01-01",
                }
            )
            JsonDiscoveryStore(store_path).save([fresh, stale])

            payload = search_candidates(
                capabilities={"travel_search"},
                task_description="find flights",
                sources=[StaticDiscoverySource(raw_candidates=[])],
                store_path=store_path,
                include_stale=False,
                stale_after_days=30,
            )

        provider_ids = {result["provider_id"] for result in payload["results"]}
        self.assertIn("fresh-flight-mcp", provider_ids)
        self.assertNotIn("stale-flight-mcp", provider_ids)
        self.assertFalse(payload["results"][0]["freshness"]["is_stale"])

    def test_ai_directory_and_web_doc_sources_ingest_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ai_path = Path(directory) / "ai.json"
            web_path = Path(directory) / "web.json"
            ai_path.write_text(
                json.dumps(
                    {
                        "ai_agents": [
                            {
                                "name": "research-ai-agent",
                                "company": "Research AI",
                                "url": "https://research-ai.example",
                                "capabilities": ["semantic_search"],
                            }
                        ]
                    }
                )
            )
            web_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "name": "shipping-doc-api",
                                "vendor": "Shipping Docs",
                                "docs_url": "https://shipping-docs.example",
                                "provider_type": "api_provider",
                                "capabilities": ["shipping_quote"],
                            }
                        ]
                    }
                )
            )

            candidates = [
                *AiAgentDirectorySource([str(ai_path)]).search(
                    capabilities={"semantic_search"},
                    task_description="research",
                ),
                *WebDocDiscoverySource([str(web_path)]).search(
                    capabilities={"shipping_quote"},
                    task_description="shipping quotes",
                ),
            ]

        self.assertEqual(
            {"ai_agent", "api_provider"}, {candidate.provider_type for candidate in candidates}
        )

    def test_github_research_source_marks_live_candidates_unverified(self) -> None:
        payload = {
            "items": [
                {
                    "name": "flight-mcp",
                    "full_name": "example/flight-mcp",
                    "description": "MCP server for travel search and booking agents",
                    "html_url": "https://github.com/example/flight-mcp",
                    "owner": {"login": "example"},
                }
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        with patch("planmyagents_api.discovery.sources.research.request.urlopen", return_value=FakeResponse()):
            candidates = GitHubResearchSource(max_results=1).search(
                capabilities={"travel_search"}, task_description="find flight booking agents"
            )

        self.assertEqual(1, len(candidates))
        self.assertEqual("mcp_server", candidates[0].provider_type)
        self.assertEqual("unverified", candidates[0].verification_status)
        self.assertEqual("https://github.com/example/flight-mcp", candidates[0].evidence_url)

    def test_url_research_source_infers_candidate_from_evidence_url(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit: int) -> bytes:
                return b"<title>Hotel Agent Card</title>MCP lodging search booking agent"

        with patch("planmyagents_api.discovery.sources.research.request.urlopen", return_value=FakeResponse()):
            candidates = UrlResearchSource(["https://example.com/agent-card"]).search(
                capabilities={"lodging_search"}, task_description="book hotel"
            )

        self.assertEqual("Hotel Agent Card", candidates[0].display_name)
        self.assertEqual("mcp_server", candidates[0].provider_type)
        self.assertIn("lodging_search", {capability.id for capability in candidates[0].capabilities})

    def test_live_web_search_source_requires_api_key(self) -> None:
        called = False

        def fake_search(*_args):
            nonlocal called
            called = True
            return [{"title": "Flight MCP", "url": "https://flight.example", "snippet": "travel_search"}]

        candidates = LiveWebSearchSource(
            provider="brave",
            api_key="",
            search_transport=fake_search,
        ).search(capabilities={"travel_search"}, task_description="find flights")

        self.assertEqual([], candidates)
        self.assertFalse(called)

    def test_live_web_search_source_marks_results_unverified(self) -> None:
        def fake_search(_provider, _api_key, _query, _max_results, _timeout):
            return [
                {
                    "title": "Flight MCP Registry",
                    "url": "https://registry.example/flight-mcp",
                    "snippet": "MCP server for travel_search and fare comparison.",
                }
            ]

        candidates = LiveWebSearchSource(
            provider="brave",
            api_key="key",
            source_id="test_live_web",
            search_transport=fake_search,
        ).search(capabilities={"travel_search"}, task_description="find flight agents")

        self.assertEqual(1, len(candidates))
        self.assertEqual("mcp_server", candidates[0].provider_type)
        self.assertEqual("unverified", candidates[0].verification_status)
        self.assertEqual("https://registry.example/flight-mcp", candidates[0].evidence_url)

    def test_live_specialized_sources_force_expected_provider_types(self) -> None:
        def fake_search(_provider, _api_key, _query, _max_results, _timeout):
            return [
                {
                    "title": "Flight Provider",
                    "url": "https://flight-provider.example/docs",
                    "snippet": "travel_search booking agents",
                }
            ]

        specs = [
            (LiveMcpRegistrySource, "mcp_server"),
            (LiveA2AAgentCardSource, "a2a_agent"),
            (LiveOpenApiSpecSource, "api_provider"),
            (LiveVendorDocsSource, "api_provider"),
            (LiveAgentMarketplaceSource, "ai_agent"),
        ]

        for source_cls, provider_type in specs:
            candidates = source_cls(
                api_key="key",
                search_transport=fake_search,
            ).search(capabilities={"travel_search"}, task_description="find flights")

            self.assertEqual(provider_type, candidates[0].provider_type)
            self.assertEqual("unverified", candidates[0].verification_status)

    def test_github_code_search_source_requires_token(self) -> None:
        called = False

        def fake_search(*_args):
            nonlocal called
            called = True
            return [{"title": "example/flight-mcp", "url": "https://github.com/example/flight-mcp"}]

        candidates = GitHubCodeSearchSource(search_transport=fake_search).search(
            capabilities={"travel_search"},
            task_description="find flight mcp",
        )

        self.assertEqual([], candidates)
        self.assertFalse(called)

    def test_live_url_directory_source_fetches_configured_specs(self) -> None:
        def fake_fetch(url, _timeout):
            self.assertEqual("https://flight.example/.well-known/agent.json", url)
            return "<title>Flight Agent Card</title>A2A agent card for travel_search"

        candidates = LiveUrlDirectorySource(
            ["https://flight.example/.well-known/agent.json"],
            provider_type="a2a_agent",
            source_id="test_live_url",
            fetch_text=fake_fetch,
        ).search(capabilities={"travel_search"}, task_description="find flights")

        self.assertEqual(1, len(candidates))
        self.assertEqual("a2a_agent", candidates[0].provider_type)
        self.assertEqual("Flight Agent Card", candidates[0].display_name)

    def test_curated_sources_exceed_next_sprint_candidate_target(self) -> None:
        sources = [
            McpCatalogSource([str(ROOT / "packages/discovery/sources/curated_mcp_catalog.json")]),
            A2AAgentCardSource([str(ROOT / "packages/discovery/sources/curated_a2a_cards.json")]),
            AiAgentDirectorySource(
                [str(ROOT / "packages/discovery/sources/curated_ai_agents.json")]
            ),
            WebDocDiscoverySource([str(ROOT / "packages/discovery/sources/curated_web_docs.json")]),
        ]
        payload = search_candidates(
            capabilities=set(),
            task_description="refresh discovery candidates",
            sources=sources,
            limit=100,
            persist=False,
        )

        self.assertGreaterEqual(payload["total_candidates"], 50)
        self.assertIn("mcp_server", {result["provider_type"] for result in payload["results"]})
        self.assertIn("a2a_agent", {result["provider_type"] for result in payload["results"]})
        self.assertIn("ai_agent", {result["provider_type"] for result in payload["results"]})
        self.assertIn("promotion_readiness", payload["results"][0])
        self.assertIn("freshness", payload["results"][0])

    def test_public_marketing_query_excludes_internal_database_mcp(self) -> None:
        payload = search_candidates(
            capabilities={"company_data_lookup", "semantic_search", "web_scraping"},
            task_description="how to do marketing for my company reducemyemi.in?",
            sources=[
                StaticDiscoverySource(
                    raw_candidates=[
                        {
                            "id": "mcp-postgres",
                            "display_name": "mcp-postgres",
                            "vendor": "Model Context Protocol",
                            "vendor_url": "https://github.com/modelcontextprotocol/servers",
                            "provider_type": "mcp_server",
                            "capabilities": [
                                {"id": "database_query", "confidence": 0.8},
                                {"id": "company_data_lookup", "confidence": 0.8},
                                {"id": "structured_data_lookup", "confidence": 0.8},
                            ],
                        },
                        {
                            "id": "google-a2a-sales-agent",
                            "display_name": "Google A2A Sales Agent",
                            "vendor": "Google A2A Samples",
                            "vendor_url": "https://github.com/google-a2a",
                            "provider_type": "a2a_agent",
                            "capabilities": [
                                {"id": "company_data_lookup", "confidence": 0.8},
                                {"id": "contact_enrichment", "confidence": 0.8},
                                {"id": "semantic_search", "confidence": 0.8},
                            ],
                        },
                    ]
                )
            ],
            persist=False,
        )

        provider_ids = [result["provider_id"] for result in payload["results"]]
        self.assertIn("google-a2a-sales-agent", provider_ids)
        self.assertNotIn("mcp-postgres", provider_ids)

    def test_gap_report_uses_executable_subtasks_when_execution_is_refused(self) -> None:
        plan = GoalPlan(
            status="executable",
            summary="Marketing research plan",
            sub_tasks=[
                PlannedSubTask(
                    capability="company_data_lookup",
                    description="Find company context",
                    inputs={"domain": "reducemyemi.in"},
                ),
                PlannedSubTask(
                    capability="semantic_search",
                    description="Research market positioning",
                    inputs={"query": "reducemyemi.in marketing"},
                ),
            ],
        )
        discovery = {
            "candidates": [
                {
                    "provider_id": "google-a2a-sales-agent",
                    "display_name": "Google A2A Sales Agent",
                    "provider_type": "a2a_agent",
                    "capabilities": ["company_data_lookup", "semantic_search"],
                    "match_score": 0.9,
                    "will_fail": True,
                    "will_fail_reasons": [
                        "Executable adapter or protocol client is not implemented yet."
                    ],
                    "required_env_vars": [],
                    "verification_status": "capability_verified",
                    "benchmark_status": "not_started",
                }
            ]
        }

        report = build_gap_report(plan=plan, discovery=discovery)

        self.assertEqual(
            ["company_data_lookup", "semantic_search"], report["missing_capabilities"]
        )
        self.assertGreaterEqual(len(report["workflow_options"]), 1)
        selected = {
            step["capability"]: step["candidate"]["provider_id"]
            for step in report["workflow_options"][0]["steps"]
        }
        self.assertEqual("google-a2a-sales-agent", selected["company_data_lookup"])
        self.assertEqual("google-a2a-sales-agent", selected["semantic_search"])

    def test_gap_report_groups_candidates_by_missing_capability(self) -> None:
        plan = GoalPlan(
            status="unsupported",
            summary="Travel booking unavailable",
            missing_capabilities=["travel_search", "payment_authorization"],
        )
        discovery = {
            "candidates": [
                {
                    "provider_id": "flight-mcp",
                    "display_name": "Flight MCP",
                    "provider_type": "mcp_server",
                    "capabilities": ["travel_search"],
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter is not implemented yet."],
                    "required_env_vars": [],
                    "benchmark_status": "not_started",
                },
                {
                    "provider_id": "stripe-payments",
                    "display_name": "Stripe Payments",
                    "provider_type": "payment_provider",
                    "capabilities": ["payment_authorization"],
                    "will_fail": True,
                    "will_fail_reasons": ["API key is required before execution."],
                    "required_env_vars": ["STRIPE_SECRET_KEY"],
                    "benchmark_status": "not_started",
                },
            ]
        }

        report = build_gap_report(plan=plan, discovery=discovery)

        self.assertEqual("blocked", report["status"])
        self.assertEqual(2, len(report["capability_gaps"]))
        payment_gap = next(
            gap for gap in report["capability_gaps"] if gap["capability"] == "payment_authorization"
        )
        self.assertEqual("no_qualified_candidate_found", payment_gap["status"])
        self.assertEqual("stripe-payments", payment_gap["rejected_candidates"][0]["provider_id"])
        self.assertIn(
            "Run live discovery for MCP/A2A/AI-agent candidates before concluding coverage.",
            report["next_steps"],
        )

    def test_gap_report_requires_payment_booking_compatibility(self) -> None:
        plan = GoalPlan(
            status="unsupported",
            summary="Travel booking unavailable",
            missing_capabilities=[
                "travel_search",
                "fare_comparison",
                "booking_execution",
                "payment_authorization",
            ],
        )
        discovery = {
            "candidates": [
                {
                    "provider_id": "flight-booking-agent",
                    "display_name": "Flight Booking Agent",
                    "provider_type": "a2a_agent",
                    "capabilities": ["booking_execution"],
                    "match_score": 0.9,
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter or protocol client is not implemented yet."],
                    "required_env_vars": [],
                    "verification_status": "capability_verified",
                    "benchmark_status": "not_started",
                },
                {
                    "provider_id": "duffel",
                    "display_name": "Duffel",
                    "provider_type": "api_provider",
                    "capabilities": ["travel_search", "fare_comparison", "booking_execution"],
                    "match_score": 0.9,
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter is not implemented yet."],
                    "required_env_vars": ["DUFFEL_API_TOKEN"],
                    "benchmark_status": "not_started",
                },
                {
                    "provider_id": "google-a2a-travel-planner",
                    "display_name": "Google A2A Travel Planner",
                    "provider_type": "a2a_agent",
                    "capabilities": ["travel_search", "fare_comparison"],
                    "match_score": 0.95,
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter or protocol client is not implemented yet."],
                    "required_env_vars": [],
                    "verification_status": "capability_verified",
                    "benchmark_status": "not_started",
                },
                {
                    "provider_id": "google-a2a-payment-agent",
                    "display_name": "Google A2A Payment Agent",
                    "provider_type": "a2a_agent",
                    "capabilities": ["payment_authorization"],
                    "match_score": 0.95,
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter or protocol client is not implemented yet."],
                    "required_env_vars": [],
                    "verification_status": "capability_verified",
                    "benchmark_status": "not_started",
                },
            ]
        }

        report = build_gap_report(plan=plan, discovery=discovery)
        payment_gap = next(
            gap for gap in report["capability_gaps"] if gap["capability"] == "payment_authorization"
        )

        self.assertEqual("candidate_found_but_incompatible", payment_gap["status"])
        self.assertIsNone(payment_gap["best_candidate"])
        self.assertIn(
            "No payment candidate has verified compatibility with `flight-booking-agent`.",
            payment_gap["blockers"],
        )
        self.assertGreaterEqual(len(report["workflow_options"]), 1)
        top_steps = {
            step["capability"]: step for step in report["workflow_options"][0]["steps"]
        }
        self.assertEqual(
            "flight-booking-agent",
            top_steps["booking_execution"]["candidate"]["provider_id"],
        )
        self.assertEqual(
            "candidate_found_but_incompatible",
            top_steps["payment_authorization"]["status"],
        )
        self.assertEqual(
            "duffel",
            top_steps["booking_execution"]["rejected_candidates"][0]["provider_id"],
        )

    def test_gap_report_accepts_payment_candidate_with_booking_compatibility(self) -> None:
        plan = GoalPlan(
            status="unsupported",
            summary="Travel booking unavailable",
            missing_capabilities=["booking_execution", "payment_authorization"],
        )
        discovery = {
            "candidates": [
                {
                    "provider_id": "flight-booking-agent",
                    "display_name": "Flight Booking Agent",
                    "provider_type": "a2a_agent",
                    "capabilities": ["booking_execution"],
                    "match_score": 0.9,
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter or protocol client is not implemented yet."],
                    "required_env_vars": [],
                    "verification_status": "capability_verified",
                    "benchmark_status": "not_started",
                },
                {
                    "provider_id": "flight-payment-agent",
                    "display_name": "Flight Payment Agent",
                    "provider_type": "a2a_agent",
                    "capabilities": ["payment_authorization"],
                    "compatible_provider_ids": ["flight-booking-agent"],
                    "match_score": 0.8,
                    "will_fail": True,
                    "will_fail_reasons": ["Executable adapter or protocol client is not implemented yet."],
                    "required_env_vars": [],
                    "verification_status": "capability_verified",
                    "benchmark_status": "not_started",
                },
            ]
        }

        report = build_gap_report(plan=plan, discovery=discovery)
        payment_gap = next(
            gap for gap in report["capability_gaps"] if gap["capability"] == "payment_authorization"
        )

        self.assertEqual("candidate_found_but_not_routable", payment_gap["status"])
        self.assertEqual("flight-payment-agent", payment_gap["best_candidate"]["provider_id"])

    def test_freshness_status_marks_old_candidates_stale(self) -> None:
        status = freshness_status(
            first_seen_at="2020-01-01", last_seen_at="2020-01-01", stale_after_days=30
        )

        self.assertTrue(status.is_stale)
        self.assertIsNotNone(status.age_days)


if __name__ == "__main__":
    unittest.main()

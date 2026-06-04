#!/usr/bin/env python3
"""Self-contained local demo UI for PlanMyAgents benchmarks.

Run:
    PYTHONPATH=apps/api <PROVIDER_API_KEY>=... python3 scripts/serve_demo.py

Then open:
    http://localhost:8787

The UI intentionally avoids a frontend stack. It is enough for a screen-recorded
demo and for manually testing the goal planner against configured provider keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.agents.router import ProviderRouter  # noqa: E402
from planmyagents_api.discovery.gaps import build_gap_report  # noqa: E402
from planmyagents_api.discovery.service import search_candidates  # noqa: E402
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource  # noqa: E402
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource  # noqa: E402
from planmyagents_api.discovery.sources.live import (  # noqa: E402
    GitHubCodeSearchSource,
    LiveA2AAgentCardSource,
    LiveAgentMarketplaceSource,
    LiveMcpRegistrySource,
    LiveOpenApiSpecSource,
    LiveUrlDirectorySource,
    LiveVendorDocsSource,
    LiveWebSearchSource,
)
from planmyagents_api.discovery.sources.mcp import McpCatalogSource  # noqa: E402
from planmyagents_api.discovery.sources.research import GitHubResearchSource, UrlResearchSource  # noqa: E402
from planmyagents_api.discovery.sources.static import StaticDiscoverySource  # noqa: E402
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource  # noqa: E402
from planmyagents_api.planner.capability_catalog import (  # noqa: E402
    build_capability_catalog,
    infer_capabilities_from_catalog,
)
from planmyagents_api.planner.goal import GoalPlan, plan_goal  # noqa: E402
from planmyagents_api.planner.goal_decomposer import (  # noqa: E402
    GoalDecompositionError,
    decompose_goal,
)
from planmyagents_api.planner.local_qwen import (  # noqa: E402
    DEFAULT_QWEN_MODEL,
    LocalQwenPlannerError,
    plan_goal_with_local_qwen,
)
from planmyagents_api.registry.discovery import enrich_registry_from_unsupported_plan  # noqa: E402
from planmyagents_api.workflows.executor import WorkflowExecutor  # noqa: E402

HOST = "127.0.0.1"
PORT = 8787
DISCOVERY_STORE = os.getenv(
    "PLANMYAGENTS_DISCOVERY_STORE_URL",
    str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
)


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "PlanMyAgentsDemo/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(INDEX_HTML)
            return

        if parsed.path == "/api/goal":
            self._handle_goal(parsed.query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _handle_goal(self, query: str) -> None:
        started_at = time.monotonic()
        trace_id = uuid.uuid4().hex[:12]
        params = parse_qs(query)
        goal = params.get("goal", [""])[0]
        _trace(
            trace_id,
            "goal.received",
            goal_hash=_hash_goal(goal),
            goal_preview=_preview(goal),
        )
        router = ProviderRouter()
        _trace(
            trace_id,
            "registry.loaded",
            registry_path=str(router.registry_path),
            active_providers=len(router.registry.get("agents", [])),
            discovered_candidates=len(router.registry.get("discovered_agents", [])),
            known_capabilities=len(router.known_capabilities()),
        )
        planner_started_at = time.monotonic()
        plan, planner_metadata = _plan_goal(goal, router)
        planner_ms = _elapsed_ms(planner_started_at)
        plan_payload = {
            **plan.to_json(),
            "planner": planner_metadata,
            "trace_id": trace_id,
            "timings_ms": {
                "planner": planner_ms,
            },
        }
        _trace(
            trace_id,
            "planner.completed",
            mode=planner_metadata.get("mode"),
            model=planner_metadata.get("model"),
            status=plan.status,
            executable=plan.executable,
            sub_tasks=len(plan.sub_tasks),
            missing_capabilities=plan.missing_capabilities,
            refusal_reasons=plan.refusal_reasons,
            duration_ms=planner_ms,
        )

        if not plan.executable:
            discovery_started_at = time.monotonic()
            _trace(
                trace_id,
                "execution.skipped",
                reason="plan_not_executable",
                missing_capabilities=plan.missing_capabilities,
            )
            discovery = _run_refusal_discovery(
                goal=goal,
                capabilities=set(plan.missing_capabilities),
                trace_id=trace_id,
            ) or enrich_registry_from_unsupported_plan(
                goal=goal,
                plan=plan,
                registry_path=router.registry_path,
                sources=_demo_discovery_sources(),
                persist_registry=False,
            )
            discovery_ms = _elapsed_ms(discovery_started_at)
            plan_payload["timings_ms"]["discovery"] = discovery_ms
            if discovery:
                plan_payload["discovery"] = discovery
                plan_payload["gap_report"] = build_gap_report(plan=plan, discovery=discovery)
                _trace(
                    trace_id,
                    "discovery.completed",
                    status=discovery.get("status"),
                    missing_capabilities=discovery.get("missing_capabilities", []),
                    added_candidates=discovery.get("added_candidates", []),
                    updated_candidates=discovery.get("updated_candidates", []),
                    candidates=len(discovery.get("candidates", [])),
                    will_fail=discovery.get("will_fail"),
                    duration_ms=discovery_ms,
                )
            else:
                plan_payload["gap_report"] = build_gap_report(plan=plan, discovery=None)
                _trace(
                    trace_id,
                    "discovery.skipped",
                    reason="no_external_missing_capabilities",
                    duration_ms=discovery_ms,
                )
            plan_payload["timings_ms"]["total"] = _elapsed_ms(started_at)
            _trace(
                trace_id,
                "response.sent",
                status="unsupported",
                executed=False,
                http_status=HTTPStatus.OK,
                duration_ms=plan_payload["timings_ms"]["total"],
            )
            self._json({"ok": True, "plan": plan_payload, "executed": False})
            return

        try:
            _trace(trace_id, "execution.started", sub_tasks=len(plan.sub_tasks))
            execution_started_at = time.monotonic()
            execution = _run_async_workflow(WorkflowExecutor(router=router), plan)
            execution_ms = _elapsed_ms(execution_started_at)
            plan_payload["timings_ms"]["execution"] = execution_ms
            execution_payload = execution.to_json()
            if not execution.executed:
                blocked_capabilities = _blocked_capabilities(plan=plan, execution=execution_payload)
                discovery_started_at = time.monotonic()
                discovery = _run_refusal_discovery(
                    goal=goal,
                    capabilities=blocked_capabilities,
                    trace_id=trace_id,
                )
                discovery_ms = _elapsed_ms(discovery_started_at)
                plan_payload["timings_ms"]["discovery"] = discovery_ms
                if discovery:
                    plan_payload["discovery"] = discovery
                    plan_payload["gap_report"] = build_gap_report(plan=plan, discovery=discovery)
                else:
                    plan_payload["gap_report"] = build_gap_report(plan=plan, discovery=None)
                plan_payload["timings_ms"]["total"] = _elapsed_ms(started_at)
                _trace(
                    trace_id,
                    "execution.refused",
                    status=execution.status,
                    sub_task_results=len(execution.sub_task_results),
                    refusal_reasons=execution.refusal_reasons,
                    duration_ms=execution_ms,
                )
                _trace(
                    trace_id,
                    "discovery.completed_after_refusal",
                    searched_capabilities=sorted(blocked_capabilities),
                    candidates=len((discovery or {}).get("candidates", [])),
                    store=str(DISCOVERY_STORE),
                    duration_ms=discovery_ms,
                )
                _trace(
                    trace_id,
                    "response.sent",
                    status="unsupported",
                    executed=False,
                    http_status=HTTPStatus.OK,
                    duration_ms=plan_payload["timings_ms"]["total"],
                )
                self._json(
                    {
                        "ok": True,
                        "plan": {
                            **plan_payload,
                            "status": "unsupported",
                            "summary": execution.summary,
                            "refusal_reasons": execution.refusal_reasons,
                        },
                        "execution": execution_payload,
                        "executed": False,
                    }
                )
                return

            plan_payload["timings_ms"]["total"] = _elapsed_ms(started_at)
            _trace(
                trace_id,
                "execution.completed",
                sub_task_results=len(execution.sub_task_results),
                average_confidence=execution.average_confidence,
                total_cost_usd=execution.total_cost_usd,
                duration_ms=execution_ms,
            )
            _trace(
                trace_id,
                "response.sent",
                status="executable",
                executed=True,
                http_status=HTTPStatus.OK,
                duration_ms=plan_payload["timings_ms"]["total"],
            )
            self._json(
                {
                    "ok": True,
                    "plan": plan_payload,
                    "executed": True,
                    "answer": _answer_from_execution(execution_payload),
                    "summary": _summary_from_execution(execution_payload),
                    "execution": execution_payload,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface demo errors to browser.
            plan_payload["timings_ms"]["total"] = _elapsed_ms(started_at)
            _trace(
                trace_id,
                "response.error",
                error=type(exc).__name__,
                message=str(exc),
                http_status=HTTPStatus.BAD_REQUEST,
                duration_ms=plan_payload["timings_ms"]["total"],
            )
            self._json(
                {"ok": False, "error": str(exc), "plan": plan_payload},
                status=HTTPStatus.BAD_REQUEST,
            )

    def _html(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[demo] {self.address_string()} - {format % args}")


def _run_async_workflow(executor: WorkflowExecutor, plan):
    import asyncio

    return asyncio.run(executor.execute(plan, workflow_id="demo-goal"))


def _plan_goal(goal: str, router: ProviderRouter):
    planner_mode = os.getenv("PLANMYAGENTS_PLANNER", "local_qwen")
    capability_catalog = _demo_capability_catalog(goal)
    catalog_metadata = {
        "capability_catalog": {
            "mode": "source_derived",
            "capabilities": len(capability_catalog.capability_ids),
        }
    }
    if planner_mode == "rules":
        plan = plan_goal(
            goal,
            supported_capabilities=router.known_capabilities(),
            capability_catalog=capability_catalog,
        )
        plan, decomposer_metadata = _apply_decomposer_for_demo(
            plan, goal, capability_catalog, router
        )
        return plan, {
            "mode": "rules",
            **catalog_metadata,
            **decomposer_metadata,
        }

    try:
        plan = plan_goal_with_local_qwen(goal, router.registry)
        plan = _refine_plan_with_capability_catalog(plan, goal, capability_catalog)
        plan, decomposer_metadata = _apply_decomposer_for_demo(
            plan, goal, capability_catalog, router
        )
        return plan, {
            "mode": "local_qwen",
            "model": os.getenv("PLANMYAGENTS_QWEN_MODEL", DEFAULT_QWEN_MODEL),
            **catalog_metadata,
            **decomposer_metadata,
        }
    except LocalQwenPlannerError as exc:
        fallback = plan_goal(
            goal,
            supported_capabilities=router.known_capabilities(),
            capability_catalog=capability_catalog,
        )
        return fallback, {
            "mode": "rules_fallback",
            "preferred_mode": "local_qwen",
            "fallback_reason": str(exc),
            **catalog_metadata,
            "decomposer": {
                "status": "skipped_after_planner_unavailable",
                "reason": (
                    "Skipped the goal decomposer because it shares the local "
                    "LLM that just timed out in the planner."
                ),
            },
            "decomposed_sub_tasks": [],
        }


def _refine_plan_with_capability_catalog(
    plan: GoalPlan, goal: str, capability_catalog
) -> GoalPlan:
    if plan.executable:
        return plan
    inferred = infer_capabilities_from_catalog(goal, capability_catalog)
    missing = sorted(set(plan.missing_capabilities) | set(inferred))
    if missing == plan.missing_capabilities:
        return plan
    return GoalPlan(
        status=plan.status,
        summary=plan.summary,
        sub_tasks=plan.sub_tasks,
        refusal_reasons=plan.refusal_reasons,
        missing_capabilities=missing,
    )


def _apply_decomposer_for_demo(
    plan: GoalPlan, goal: str, capability_catalog, router: ProviderRouter
) -> tuple[GoalPlan, dict]:
    """Demo-server equivalent of planmyagents_api.web.planning._apply_decomposer.

    Kept as a thin wrapper so the demo HTTP server has the same
    behaviour as the FastAPI route: executable plans pass through,
    unsupported plans get their missing_capabilities replaced with
    the decomposer's output, decomposer failures degrade to the
    planner's original list with a structured reason in the
    metadata, and freshly-coined labels go through the slice 2
    reconciliation + persistence pass so the demo's catalog grows
    with usage exactly the way the production path does.

    Slice 2 reconciliation logic is imported verbatim from
    :mod:`planmyagents_api.web.planning` rather than reimplemented here so
    the demo and production paths cannot drift.
    """

    if plan.executable:
        return plan, {
            "decomposer": {"status": "skipped_executable_plan"},
            "decomposed_sub_tasks": [],
        }

    try:
        decomposition = decompose_goal(goal, catalog_hint=capability_catalog)
    except GoalDecompositionError as exc:
        return plan, {
            "decomposer": {
                "status": "unavailable",
                "reason": str(exc),
            },
            "decomposed_sub_tasks": [],
        }

    # Slice 2: post-decomposition reconciliation + persistence. The
    # helper is best-effort — any LLM/store failure leaves the
    # decomposition unchanged and reports the reason in the
    # ``label_reconciler`` metadata block.
    from planmyagents_api.web.planning import _reconcile_and_persist_labels

    decomposition, reconciler_metadata = _reconcile_and_persist_labels(
        decomposition=decomposition,
        capability_catalog=capability_catalog,
        goal=goal,
    )

    supported = router.known_capabilities()
    suggested = decomposition.suggested_capability_ids
    missing = sorted({cap for cap in suggested if cap not in supported})
    metadata = {
        "decomposer": {
            "status": "applied",
            "intent_summary": decomposition.intent_summary,
            "confidence": decomposition.confidence,
            "catalog_reused_capabilities": decomposition.catalog_reused_capabilities,
            "new_capabilities": decomposition.new_capabilities,
            "supported_capabilities_filtered_out": sorted(
                set(suggested) & supported
            ),
        },
        "decomposed_sub_tasks": [sub.to_json() for sub in decomposition.sub_tasks],
        "label_reconciler": reconciler_metadata,
    }
    if missing == sorted(plan.missing_capabilities):
        return plan, metadata
    return (
        GoalPlan(
            status="unsupported",
            summary=(
                decomposition.intent_summary
                or "This goal requires capabilities that are not available in the current provider registry."
            ),
            refusal_reasons=[
                "PlanMyAgents decomposed the goal into sub-tasks and the registry cannot route at least one of them yet.",
                (
                    "Execution is blocked until matching providers are verified, benchmarked, "
                    "configured, and promoted."
                ),
            ],
            missing_capabilities=missing,
        ),
        metadata,
    )


def _summary_from_execution(execution: dict) -> dict:
    sub_tasks = execution.get("sub_task_results", [])
    total = len(sub_tasks)
    succeeded = sum(1 for item in sub_tasks if item.get("succeeded"))
    return {
        "total": total,
        "success_rate": round(succeeded / total, 4) if total else 0.0,
        "average_quality": float(execution.get("average_confidence", 0.0)),
        "average_cost_usd": float(execution.get("total_cost_usd", 0.0)) / total if total else 0.0,
        "p50_latency_ms": 0,
    }


def _blocked_capabilities(*, plan: GoalPlan, execution: dict) -> set[str]:
    capabilities = {
        str(result.get("capability"))
        for result in execution.get("sub_task_results", [])
        if result.get("capability")
    }
    if capabilities:
        return capabilities
    return {task.capability for task in plan.sub_tasks} | set(plan.missing_capabilities)


def _run_refusal_discovery(
    *, goal: str, capabilities: set[str], trace_id: str
) -> dict | None:
    """Run live discovery whenever execution is blocked.

    During the early discovery window this intentionally runs on every refusal
    and persists non-routable candidates to the discovery store. Results remain
    unverified and never become executable from this path.
    """

    searched = {item for item in capabilities if item}
    if not searched:
        return None

    payload = search_candidates(
        capabilities=searched,
        task_description=goal,
        sources=_live_discovery_sources(goal_hash=_hash_goal(goal)),
        store_path=DISCOVERY_STORE,
        limit=50,
        persist=True,
        load_store=True,
        include_stale=True,
    )
    return {
        "status": "live_discovery_ran",
        "persisted_to_registry": False,
        "persisted_to_store": str(DISCOVERY_STORE),
        "source_coverage": {
            "mode": "live_discovery_on_refusal",
            "policy_window": "first_3_months",
            "live_github_search": True,
            "live_url_research": bool(_research_urls()),
            "note": (
                "The blocked request triggered live discovery and persisted "
                "non-routable candidates to the discovery store. Fresh findings "
                "must still be reviewed, adapted, benchmarked, and configured "
                "before execution."
            ),
        },
        "missing_capabilities": sorted(searched),
        "searched_capabilities": payload["searched_capabilities"],
        "normalizations": payload["normalizations"],
        "added_candidates": [],
        "updated_candidates": [],
        "will_fail": True,
        "will_fail_reasons": [
            "Live-discovered candidates are not routable until reviewed, adapted, benchmarked, and configured.",
            "The runtime refuses execution rather than calling unverified web results.",
        ],
        "total_candidates": payload["total_candidates"],
        "candidates": payload["results"],
        "trace_id": trace_id,
    }


def _answer_from_execution(execution: dict) -> dict:
    """Build the product-facing answer shown above debug details."""

    records = execution.get("records", [])
    email_records = [
        record
        for record in records
        if record.get("capability") == "email_verification"
        and isinstance(record.get("data"), dict)
    ]
    if email_records:
        lines = []
        for record in email_records:
            data = record["data"]
            checks = data.get("checks", {}) if isinstance(data.get("checks"), dict) else {}
            result = str(data.get("result", "unknown")).lower()
            email = str(data.get("email", "the email"))
            if result == "deliverable":
                verdict = f"{email} looks deliverable."
            elif result == "undeliverable":
                verdict = f"{email} does not look deliverable."
            elif result == "risky":
                verdict = f"{email} looks risky."
            else:
                verdict = f"{email} could not be verified confidently."
            lines.append(
                {
                    "verdict": verdict,
                    "detail": _email_check_detail(checks),
                    "provider_id": record.get("provider_id"),
                    "confidence": record.get("confidence", 0.0),
                }
            )
        return {
            "title": "Email verification complete",
            "body": (
                "PlanMyAgents routed the task to a configured email verification provider. "
                "If the provider only reports domain-level checks, treat the result as inconclusive."
            ),
            "items": lines,
        }

    return {
        "title": "Workflow completed",
        "body": execution.get("summary", "PlanMyAgents completed the requested workflow."),
        "items": [
            {
                "verdict": f"{record.get('capability', 'task')} completed",
                "detail": f"Provider: {record.get('provider_id', 'unknown')}",
                "provider_id": record.get("provider_id"),
                "confidence": record.get("confidence", 0.0),
            }
            for record in records
        ],
    }


def _email_check_detail(checks: dict) -> str:
    passed = [
        label
        for key, label in [
            ("syntax", "valid email format"),
            ("mx", "mail server found"),
            ("a", "domain resolves"),
        ]
        if checks.get(key)
    ]
    failed = [
        label
        for key, label in [
            ("syntax", "valid email format"),
            ("mx", "mail server found"),
            ("a", "domain resolves"),
        ]
        if key in checks and not checks.get(key)
    ]
    if passed and failed:
        return f"Passed: {', '.join(passed)}. Failed: {', '.join(failed)}."
    if passed:
        return f"Passed: {', '.join(passed)}."
    if failed:
        return f"Failed: {', '.join(failed)}."
    return "No detailed checks were reported."


def _trace(trace_id: str, event: str, **fields) -> None:
    """Emit structured request traces to the demo terminal."""

    payload = {
        "trace_id": trace_id,
        "event": event,
        **fields,
    }
    print(f"[trace] {json.dumps(payload, sort_keys=True, default=str)}", flush=True)


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _demo_discovery_sources():
    source_root = ROOT / "packages" / "discovery" / "sources"
    return [
        StaticDiscoverySource(),
        McpCatalogSource([str(source_root / "curated_mcp_catalog.json")]),
        A2AAgentCardSource([str(source_root / "curated_a2a_cards.json")]),
        AiAgentDirectorySource([str(source_root / "curated_ai_agents.json")]),
        WebDocDiscoverySource([str(source_root / "curated_web_docs.json")]),
    ]


def _live_discovery_sources(*, goal_hash: str):
    live_search_provider = os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_SEARCH_PROVIDER", "brave").lower()
    live_search_key = (
        os.getenv("TAVILY_API_KEY", "")
        if live_search_provider == "tavily"
        else os.getenv("BRAVE_SEARCH_API_KEY", "")
    )
    return [
        *_demo_discovery_sources(),
        GitHubResearchSource(
            token=os.getenv("GITHUB_TOKEN", ""),
            max_results=int(os.getenv("PLANMYAGENTS_LIVE_GITHUB_MAX_RESULTS", "5")),
            timeout_seconds=float(os.getenv("PLANMYAGENTS_LIVE_DISCOVERY_TIMEOUT_SECONDS", "8")),
            goal_hash=goal_hash,
        ),
        *(
            [UrlResearchSource(_research_urls(), goal_hash=goal_hash)]
            if _research_urls()
            else []
        ),
        *(
            [
                LiveWebSearchSource(
                    provider="brave",
                    api_key=os.getenv("BRAVE_SEARCH_API_KEY", ""),
                    source_id="brave_search",
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_BRAVE_SEARCH")
            else []
        ),
        *(
            [
                LiveWebSearchSource(
                    provider="tavily",
                    api_key=os.getenv("TAVILY_API_KEY", ""),
                    source_id="tavily_search",
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_TAVILY_SEARCH")
            else []
        ),
        *(
            [GitHubCodeSearchSource(token=os.getenv("GITHUB_TOKEN", ""), goal_hash=goal_hash)]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_GITHUB_CODE_SEARCH")
            else []
        ),
        *(
            [
                LiveMcpRegistrySource(
                    provider=live_search_provider,
                    api_key=live_search_key,
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_MCP_REGISTRIES")
            else []
        ),
        *(
            [
                LiveA2AAgentCardSource(
                    provider=live_search_provider,
                    api_key=live_search_key,
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_A2A_CARDS")
            else []
        ),
        *(
            [
                LiveOpenApiSpecSource(
                    provider=live_search_provider,
                    api_key=live_search_key,
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_OPENAPI_SPECS")
            else []
        ),
        *(
            [
                LiveVendorDocsSource(
                    provider=live_search_provider,
                    api_key=live_search_key,
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_VENDOR_DOCS")
            else []
        ),
        *(
            [
                LiveAgentMarketplaceSource(
                    provider=live_search_provider,
                    api_key=live_search_key,
                    goal_hash=goal_hash,
                )
            ]
            if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_AGENT_MARKETPLACES")
            else []
        ),
        *_live_url_sources(goal_hash=goal_hash),
    ]


def _research_urls() -> list[str]:
    value = os.getenv("PLANMYAGENTS_DISCOVERY_RESEARCH_URLS", "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _live_url_sources(*, goal_hash: str):
    source_specs = [
        ("PLANMYAGENTS_DISCOVERY_LIVE_MCP_URLS", "mcp_server", "live_mcp_url_directory"),
        ("PLANMYAGENTS_DISCOVERY_LIVE_A2A_URLS", "a2a_agent", "live_a2a_url_directory"),
        ("PLANMYAGENTS_DISCOVERY_LIVE_OPENAPI_URLS", "api_provider", "live_openapi_url_directory"),
        (
            "PLANMYAGENTS_DISCOVERY_LIVE_VENDOR_DOC_URLS",
            "api_provider",
            "live_vendor_doc_url_directory",
        ),
        ("PLANMYAGENTS_DISCOVERY_LIVE_MARKETPLACE_URLS", "ai_agent", "live_marketplace_url_directory"),
    ]
    sources = []
    for env_name, provider_type, source_id in source_specs:
        urls = [item.strip() for item in os.getenv(env_name, "").split(",") if item.strip()]
        if urls:
            sources.append(
                LiveUrlDirectorySource(
                    urls,
                    provider_type=provider_type,
                    source_id=source_id,
                    goal_hash=goal_hash,
                )
            )
    return sources


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _demo_capability_catalog(goal: str):
    candidates = []
    for source in _demo_discovery_sources():
        candidates.extend(source.search(capabilities=set(), task_description=goal))
    return build_capability_catalog(candidates)


def _hash_goal(goal: str) -> str:
    return hashlib.sha256(goal.strip().encode("utf-8")).hexdigest()[:16]


def _preview(value: str, *, limit: int = 160) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PlanMyAgents Demo</title>
  <style>
    :root { color-scheme: light dark; }
    body {
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      background: #0f172a;
      color: #e2e8f0;
    }
    .wrap { max-width: 1040px; margin: 0 auto; padding: 48px 24px; }
    .hero {
      border: 1px solid #334155;
      border-radius: 24px;
      padding: 32px;
      background: linear-gradient(135deg, #111827 0%, #1e293b 100%);
      box-shadow: 0 20px 60px rgba(0,0,0,0.35);
    }
    h1 { font-size: 44px; line-height: 1.05; margin: 0 0 12px; }
    p { color: #94a3b8; line-height: 1.6; }
    label { display: block; margin-bottom: 8px; color: #cbd5e1; font-weight: 650; }
    select, input {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #475569;
      background: #020617;
      color: #f8fafc;
      border-radius: 12px;
      padding: 12px 14px;
      font-size: 15px;
    }
    .grid { display: grid; grid-template-columns: 1fr 160px 160px; gap: 16px; align-items: end; margin-top: 28px; }
    button {
      border: 0;
      border-radius: 12px;
      padding: 13px 16px;
      background: #2563eb;
      color: white;
      font-weight: 800;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 24px 0; }
    .card { background: #020617; border: 1px solid #334155; border-radius: 16px; padding: 18px; }
    .card .label { color: #94a3b8; font-size: 13px; }
    .card .value { color: #f8fafc; font-size: 28px; margin-top: 6px; font-weight: 800; }
    .success {
      margin-top: 18px;
      color: #dcfce7;
      background: #052e16;
      border: 1px solid #15803d;
      padding: 18px;
      border-radius: 16px;
    }
    .answer {
      background: #020617;
      border: 1px solid #334155;
      border-radius: 18px;
      padding: 20px;
      margin-top: 18px;
    }
    .answer h2 { margin: 0 0 8px; color: #f8fafc; }
    .answer-item {
      border-top: 1px solid #1e293b;
      margin-top: 14px;
      padding-top: 14px;
    }
    .answer-item strong { color: #f8fafc; }
    .gap-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 18px 0; }
    .gap { background: #020617; border: 1px solid #334155; border-radius: 16px; padding: 16px; }
    .gap h3 { margin: 0 0 8px; color: #f8fafc; font-size: 17px; }
    .pill { display: inline-block; margin: 4px 6px 4px 0; padding: 4px 8px; border-radius: 999px; background: #1e293b; color: #bfdbfe; font-size: 12px; }
    details { margin-top: 18px; }
    summary { cursor: pointer; color: #bfdbfe; font-weight: 700; }
    pre {
      background: #020617;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 18px;
      overflow: auto;
      white-space: pre-wrap;
      color: #dbeafe;
    }
    .error { color: #fecaca; background: #450a0a; border: 1px solid #991b1b; padding: 14px; border-radius: 12px; }
    .hint { margin-top: 10px; font-size: 13px; color: #94a3b8; }
    @media (max-width: 760px) { .grid, .cards, .gap-grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>PlanMyAgents Goal Router</h1>
      <p>
        Describe a job. PlanMyAgents plans the required capabilities, selects configured providers automatically,
        executes only if the provider universe can support the goal, and otherwise refuses with exact reasons.
      </p>
      <div style="margin-top: 24px;">
        <label for="goal">User goal</label>
        <textarea id="goal" rows="4" style="width:100%;box-sizing:border-box;border:1px solid #475569;background:#020617;color:#f8fafc;border-radius:12px;padding:12px 14px;font-size:15px;">Verify jane.doe@acme.com and missing@invalid.example</textarea>
        <div class="hint">
          Planning tries local Qwen first, then falls back to deterministic rules if Ollama is unavailable.
          Production provider selection is automatic. Configure provider credentials from the registry to enable execution.
          Try unsupported goals too: "Find CTO emails for 100 EU SaaS companies" or "Find the cheapest PS5 globally and ship it to India legally."
        </div>
      </div>
      <div style="margin-top: 14px;">
        <button id="runGoal">Plan Goal</button>
      </div>
    </section>

    <section id="output"></section>
  </main>

  <script>
    const goalButton = document.getElementById('runGoal');
    const output = document.getElementById('output');

    goalButton.addEventListener('click', async () => {
      const goal = document.getElementById('goal').value || '';
      goalButton.disabled = true;
      goalButton.textContent = 'Planning...';
      output.innerHTML = '<p>Planning goal...</p>';
      try {
        const res = await fetch(`/api/goal?goal=${encodeURIComponent(goal)}`);
        const payload = await res.json();
        if (!payload.ok) {
          output.innerHTML = `<div class="error">${escapeHtml(payload.error)}</div><pre>${escapeHtml(JSON.stringify(payload.plan || {}, null, 2))}</pre>`;
          return;
        }
        if (!payload.executed) {
          output.innerHTML = renderUnsupported(payload);
          return;
        }
        output.innerHTML = renderExecuted(payload);
      } catch (err) {
        output.innerHTML = `<div class="error">${escapeHtml(String(err))}</div>`;
      } finally {
        goalButton.disabled = false;
        goalButton.textContent = 'Plan Goal';
      }
    });

    function renderExecuted(payload) {
      const s = payload.summary || {};
      return `
        <div class="success">
          <strong>Executed successfully.</strong>
          PlanMyAgents planned the goal, selected a configured provider, and completed the task.
        </div>
        ${renderAnswer(payload.answer || {}, payload.execution || {})}
        <div class="cards">
          <div class="card"><div class="label">Subtasks</div><div class="value">${escapeHtml(s.total ?? 0)}</div></div>
          <div class="card"><div class="label">Success</div><div class="value">${Math.round((s.success_rate || 0) * 100)}%</div></div>
          <div class="card"><div class="label">Quality</div><div class="value">${Number(s.average_quality || 0).toFixed(2)}</div></div>
          <div class="card"><div class="label">Avg cost</div><div class="value">$${Number(s.average_cost_usd || 0).toFixed(4)}</div></div>
        </div>
        <details>
          <summary>Technical details</summary>
          <h3>Plan</h3>
          <pre>${escapeHtml(JSON.stringify(payload.plan, null, 2))}</pre>
          <h3>Execution</h3>
          <pre>${escapeHtml(JSON.stringify(payload.execution, null, 2))}</pre>
        </details>
      `;
    }

    function renderAnswer(answer, execution) {
      const items = answer.items || [];
      return `
        <div class="answer">
          <h2>${escapeHtml(answer.title || 'Workflow completed')}</h2>
          <p>${escapeHtml(answer.body || execution.summary || '')}</p>
          ${items.map(renderAnswerItem).join('')}
        </div>
      `;
    }

    function renderAnswerItem(item) {
      return `
        <div class="answer-item">
          <strong>${escapeHtml(item.verdict || 'Task completed')}</strong>
          <p>${escapeHtml(item.detail || '')}</p>
          <span class="pill">Provider: ${escapeHtml(item.provider_id || 'unknown')}</span>
          <span class="pill">Confidence: ${Number(item.confidence || 0).toFixed(2)}</span>
        </div>
      `;
    }

    function renderUnsupported(payload) {
      const plan = payload.plan || {};
      const execution = payload.execution || null;
      const report = plan.gap_report || {};
      const gaps = report.capability_gaps || [];
      const workflowOptions = report.workflow_options || [];
      const coverage = plan.discovery?.source_coverage || {};
      const nextSteps = report.next_steps || [];
      const discoveryCandidates = plan.discovery?.candidates || [];
      const executionResults = execution?.sub_task_results || [];
      const identifiedCandidates = executionResults.flatMap(result =>
        (result.route_decision?.candidates || []).map(candidate => ({
          ...candidate,
          capability: result.capability,
          refusal_reason: result.refusal_reason || result.route_decision?.reason || ''
        }))
      );
      return `
        <div class="error">
          <strong>Cannot execute this goal yet.</strong><br/>
          ${escapeHtml(report.summary || plan.summary)}
        </div>
        ${execution ? renderExecutionRefusal(plan, execution, identifiedCandidates) : ''}
        ${coverage.mode ? `
          <div class="hint">
            Discovery scope: ${escapeHtml(coverage.mode)}.
            Live web/GitHub/MCP/A2A search: ${escapeHtml(coverage.live_web_search || 'unknown')}.
            ${escapeHtml(coverage.note || '')}
          </div>
        ` : ''}
        <div class="cards">
          <div class="card"><div class="label">Missing capabilities</div><div class="value">${(report.missing_capabilities || plan.missing_capabilities || []).length}</div></div>
          <div class="card"><div class="label">Discovery candidates</div><div class="value">${(plan.discovery?.candidates || []).length}</div></div>
          <div class="card"><div class="label">Planner</div><div class="value" style="font-size:18px;">${escapeHtml(plan.planner?.mode || 'unknown')}</div></div>
          <div class="card"><div class="label">Trace</div><div class="value" style="font-size:18px;">${escapeHtml(plan.trace_id || 'n/a')}</div></div>
        </div>
        <h2>Ranked Workflow Options</h2>
        <div class="gap-grid">${workflowOptions.map(renderWorkflowOption).join('') || '<p>No workflow options available.</p>'}</div>
        <h2>Capability Gaps</h2>
        <div class="gap-grid">${gaps.map(renderGap).join('') || '<p>No external capability gaps were identified.</p>'}</div>
        ${discoveryCandidates.length ? `
          <h2>Live Discovery Results</h2>
          <p class="hint">
            These are non-routable findings from configured sources and live research.
            They are not executed until reviewed, adapted, benchmarked, and configured.
          </p>
          <div class="gap-grid">${discoveryCandidates.slice(0, 8).map(renderDiscoveryCandidate).join('')}</div>
        ` : ''}
        ${identifiedCandidates.length ? `
          <h2>Identified Providers</h2>
          <div class="gap-grid">${identifiedCandidates.map(renderIdentifiedCandidate).join('')}</div>
        ` : ''}
        <h2>Promotion Steps</h2>
        <div>${renderPromotionSteps(nextSteps, executionResults)}</div>
        <details>
          <summary>Debug JSON</summary>
          <pre>${escapeHtml(JSON.stringify({ plan, execution }, null, 2))}</pre>
        </details>
      `;
    }

    function renderExecutionRefusal(plan, execution, identifiedCandidates) {
      const subTasks = plan.sub_tasks || [];
      const providers = [...new Set(identifiedCandidates.map(candidate => candidate.display_name || candidate.provider_id).filter(Boolean))];
      return `
        <div class="answer">
          <h2>What PlanMyAgents understood</h2>
          <p>${escapeHtml(execution.summary || plan.summary)}</p>
          <div>
            ${subTasks.map(task => `<span class="pill">${escapeHtml(task.capability)}: ${escapeHtml(task.description || 'planned subtask')}</span>`).join('') || '<span class="pill">No executable subtasks planned</span>'}
          </div>
          <h3>What it found</h3>
          <p>
            ${providers.length
              ? `Found ${providers.length} registered provider candidate(s): ${escapeHtml(providers.join(', '))}.`
              : 'No registered provider candidates were found for the planned capability.'}
          </p>
          <h3>Why it did not run</h3>
          <div>${(execution.refusal_reasons || plan.refusal_reasons || []).map(reason => `<span class="pill">${escapeHtml(reason)}</span>`).join('') || '<span class="pill">No configured routable provider.</span>'}</div>
        </div>
      `;
    }

    function renderIdentifiedCandidate(candidate) {
      const issues = providerIssues(candidate);
      return `
        <div class="gap">
          <h3>${escapeHtml(candidate.display_name || candidate.provider_id || 'Unknown provider')}</h3>
          <p><strong>Capability:</strong> ${escapeHtml(candidate.capability || 'unknown')}</p>
          <p><strong>Status:</strong> ${candidate.configured ? 'Configured' : 'Not configured'} · ${candidate.has_executable_adapter ? 'adapter present' : 'no adapter in this runtime'}</p>
          <div>${issues.map(issue => `<span class="pill">${escapeHtml(issue)}</span>`).join('')}</div>
        </div>
      `;
    }

    function renderDiscoveryCandidate(candidate) {
      const providerType = candidate.provider_type || 'unknown';
      const capabilities = (candidate.capabilities || []).map(capability => `<span class="pill">${escapeHtml(capability)}</span>`).join('');
      const evidence = candidate.evidence_url
        ? `<p><a href="${escapeHtml(candidate.evidence_url)}" target="_blank" rel="noreferrer">Evidence</a></p>`
        : '';
      return `
        <div class="gap">
          <h3>${escapeHtml(candidate.display_name || candidate.provider_id || 'Unknown candidate')}</h3>
          <p><strong>Type:</strong> ${escapeHtml(providerType)}</p>
          <p><strong>Verification:</strong> ${escapeHtml(candidate.verification_status || 'unverified')}</p>
          <p><strong>Route status:</strong> non-routable until reviewed and promoted</p>
          <div>${capabilities}</div>
          ${evidence}
        </div>
      `;
    }

    function providerIssues(candidate) {
      const issues = [];
      if (candidate.required_env_var && !candidate.configured) {
        issues.push(`Needs ${candidate.required_env_var}`);
      }
      if (!candidate.has_executable_adapter) {
        issues.push('Executable adapter is not implemented here');
      }
      if (candidate.requires_benchmark_gate && candidate.benchmark_status !== 'passed') {
        issues.push(`Benchmark gate is ${candidate.benchmark_status || 'not passed'}`);
      }
      if (candidate.dev_provider_blocked) {
        issues.push('Dev/test provider blocked from production routing');
      }
      if (!issues.length) {
        issues.push('Candidate exists, but routing still refused this task');
      }
      return issues;
    }

    function renderPromotionSteps(nextSteps, executionResults) {
      if (nextSteps.length) {
        return nextSteps.map(step => `<span class="pill">${escapeHtml(step)}</span>`).join('');
      }
      const steps = [];
      for (const result of executionResults) {
        for (const candidate of (result.route_decision?.candidates || [])) {
          if (candidate.required_env_var && !candidate.configured) {
            steps.push(`Configure ${candidate.required_env_var} for ${candidate.display_name || candidate.provider_id}`);
          }
          if (!candidate.has_executable_adapter) {
            steps.push(`Implement adapter for ${candidate.display_name || candidate.provider_id}`);
          }
        }
      }
      return [...new Set(steps)].map(step => `<span class="pill">${escapeHtml(step)}</span>`).join('') || '<p>No promotion steps available.</p>';
    }

    function renderWorkflowOption(option) {
      const steps = (option.steps || []).map(renderWorkflowStep).join('');
      const reasons = (option.score_reasons || []).map(item => `<span class="pill">${escapeHtml(item)}</span>`).join('');
      const blockers = (option.blockers || []).map(item => `<span class="pill">${escapeHtml(item)}</span>`).join('');
      return `
        <div class="gap">
          <h3>#${escapeHtml(option.rank)} ${escapeHtml(option.title)}</h3>
          <p><strong>Flow score:</strong> ${escapeHtml(option.score)} · <strong>Status:</strong> ${escapeHtml(option.status)}</p>
          <div>${reasons}</div>
          <div style="margin-top:10px;">${steps}</div>
          ${blockers ? `<p><strong>Flow blockers</strong></p><div>${blockers}</div>` : ''}
        </div>
      `;
    }

    function renderWorkflowStep(step) {
      const candidate = step.candidate;
      const candidateText = candidate
        ? `${candidate.display_name || candidate.provider_id} (${candidate.provider_type})`
        : 'No qualified candidate selected';
      const rejected = (step.rejected_candidates || []).map(renderRejectedCandidate).join('');
      return `
        <p>
          <strong>${escapeHtml(step.capability)}:</strong>
          ${escapeHtml(candidateText)}
          <span class="pill">${escapeHtml(step.status)}</span>
        </p>
        ${rejected ? `<div style="margin: -4px 0 12px 0;">${rejected}</div>` : ''}
      `;
    }

    function renderRejectedCandidate(candidate) {
      const name = candidate.display_name || candidate.provider_id || 'unknown';
      const type = candidate.provider_type || 'unknown';
      return `
        <div class="pill">
          Rejected: ${escapeHtml(name)} (${escapeHtml(type)}) · ${escapeHtml(candidate.rejection_reason || 'not qualified')}
        </div>
      `;
    }

    function renderGap(gap) {
      const candidate = gap.best_candidate;
      const candidateText = candidate
        ? `${candidate.display_name || candidate.provider_id} (${candidate.provider_type})`
        : 'No candidate found';
      const blockers = (gap.blockers || []).map(item => `<span class="pill">${escapeHtml(item)}</span>`).join('');
      const rejected = (gap.rejected_candidates || []).map(renderRejectedCandidate).join('');
      return `
        <div class="gap">
          <h3>${escapeHtml(gap.capability)}</h3>
          <p><strong>Best qualified candidate:</strong> ${escapeHtml(candidateText)}</p>
          <p><strong>Status:</strong> ${escapeHtml(gap.status)}</p>
          <div>${blockers}</div>
          ${rejected ? `<p><strong>Rejected candidates</strong></p><div>${rejected}</div>` : ''}
        </div>
      `;
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll(\"'\", '&#039;');
    }
  </script>
</body>
</html>
"""


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"PlanMyAgents demo UI running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

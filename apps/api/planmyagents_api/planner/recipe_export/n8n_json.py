"""n8n workflow JSON renderer.

Only API-shaped recommendations with a concrete base URL become executable
HTTP Request nodes. Everything else becomes a sticky-note node so an import
never looks more runnable than the recipe actually is.
"""

from __future__ import annotations

import json
from typing import Any

from planmyagents_api.planner.recipe_export import (
    RecipeContext,
    RecipeStep,
    RenderedRecipe,
)
from planmyagents_api.planner.recipe_export.registry_lookup import lookup_endpoint


def render(context: RecipeContext) -> RenderedRecipe:
    nodes: list[dict[str, Any]] = []
    executable_node_names: list[str] = []
    unexportable_steps: list[dict[str, Any]] = []

    for step in context.steps:
        node = _render_node(step)
        nodes.append(node)
        if node["type"] == "n8n-nodes-base.httpRequest":
            executable_node_names.append(node["name"])
        else:
            unexportable_steps.append(
                {
                    "ordinal": step.ordinal,
                    "capability": step.capability,
                    "description": step.description,
                    "reason": _unexportable_reason(step),
                }
            )

    payload: dict[str, Any] = {
        "name": _workflow_name(context),
        "nodes": nodes,
        "connections": _linear_connections(executable_node_names),
        "active": False,
        "settings": {},
        "tags": ["planmyagents", "recipe-export"],
        "_planmyagents": {
            "goal_id": context.goal_id,
            "goal_text": context.goal_text,
            "generated_by": "PlanMyAgents recipe export - n8n_json",
            "coverage": context.coverage.to_dict(),
            "unexportable_steps": unexportable_steps,
            "notes": list(context.notes),
        },
    }
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    return RenderedRecipe(
        body=body,
        content_type="application/json",
        filename_suffix=".n8n.json",
    )


def _render_node(step: RecipeStep) -> dict[str, Any]:
    rec = step.recommendation
    position = [200 + (step.ordinal - 1) * 320, 240]
    if rec is None or not _can_emit_http_node(step):
        return {
            "id": f"planmyagents-note-{step.ordinal}",
            "name": f"Step {step.ordinal}: {step.capability} gap",
            "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1,
            "position": position,
            "parameters": {
                "content": _note_content(step),
            },
        }

    assert rec is not None
    endpoint = lookup_endpoint(rec.provider_id, step.capability)
    method, url = _http_method_and_url(step, endpoint)
    node: dict[str, Any] = {
        "id": f"planmyagents-http-{step.ordinal}",
        "name": f"Step {step.ordinal}: {step.capability}",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": position,
        "notes": step.description,
        "parameters": {
            "method": method,
            "url": url,
            "responseFormat": "autodetect",
            "sendHeaders": bool(rec.required_env_vars),
        },
    }
    if rec.required_env_vars:
        node["parameters"]["headerParameters"] = {
            "parameters": _build_header_parameters(rec.required_env_vars, endpoint)
        }
    return node


def _build_header_parameters(
    required_env_vars: tuple[str, ...],
    endpoint,
) -> list[dict[str, str]]:
    """Emit n8n headerParameters honouring registry auth shape when known.

    With a registry entry: emit exactly one header using the registry's
    ``header_name``, prefixing ``Bearer `` only when scheme is ``bearer``.
    Without a registry entry: fall back to the old per-env-var heuristic
    so synthetic test fixtures keep working.
    """
    if endpoint is not None and endpoint.scheme in {"bearer", "header"}:
        primary = endpoint.env_var or required_env_vars[0]
        if endpoint.scheme == "bearer":
            value = "=Bearer {{$env." + primary + "}}"
        else:
            value = "={{$env." + primary + "}}"
        return [{"name": endpoint.header_name, "value": value}]
    # Registry says no auth header (basic auth / query param / no auth)
    if endpoint is not None and endpoint.scheme in {"basic", "query", "none"}:
        return []
    return [
        {
            "name": _header_name_for(env_var),
            "value": "={{$env." + env_var + "}}",
        }
        for env_var in required_env_vars
    ]


def _can_emit_http_node(step: RecipeStep) -> bool:
    rec = step.recommendation
    if rec is None:
        return False
    return rec.provider_type in {"openapi", "api_provider", "payment_provider"} and bool(
        rec.api_base_url.strip()
    )


def _http_method_and_url(step: RecipeStep, endpoint) -> tuple[str, str]:
    """Resolve ``(method, url)`` for an HTTP node.

    Priority:
    1. Registry lookup — authoritative method + path per provider/capability.
    2. Fallback (no registry entry, e.g. discovered candidate or synthetic
       fixture): hard-coded ``GET`` against ``api_base_url + /<capability>``.
       This preserves pre-existing behaviour for unregistered providers.
    """
    rec = step.recommendation
    assert rec is not None
    base = rec.api_base_url.rstrip("/")
    if endpoint is not None and endpoint.path:
        return endpoint.method, base + endpoint.path
    return "GET", base + "/" + step.capability.replace("_", "-")


def _linear_connections(node_names: list[str]) -> dict[str, Any]:
    connections: dict[str, Any] = {}
    for source, target in zip(node_names, node_names[1:], strict=False):
        connections[source] = {
            "main": [[{"node": target, "type": "main", "index": 0}]]
        }
    return connections


def _note_content(step: RecipeStep) -> str:
    rec = step.recommendation
    if rec is None:
        return (
            f"Step {step.ordinal}: {step.capability}\n\n"
            f"{step.description}\n\n"
            "No recommended provider is available yet. This is a gap, not an "
            "executable workflow node."
        )
    return (
        f"Step {step.ordinal}: {step.capability}\n\n"
        f"{step.description}\n\n"
        f"Recommended provider: {rec.display_name or rec.provider_id}\n"
        f"Provider type: {rec.provider_type}\n"
        f"Docs: {rec.docs_url or 'n/a'}\n\n"
        f"Not exported to n8n: {_unexportable_reason(step)}"
    )


def _unexportable_reason(step: RecipeStep) -> str:
    rec = step.recommendation
    if rec is None:
        return "No recommended provider."
    if rec.provider_type not in {"openapi", "api_provider", "payment_provider"}:
        return "n8n export only emits HTTP nodes for API-shaped providers."
    if not rec.api_base_url.strip():
        return "API base URL is missing."
    return "Provider is not exportable to n8n."


def _workflow_name(context: RecipeContext) -> str:
    snippet = context.goal_text.strip().replace("\n", " ")
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return f"PlanMyAgents recipe: {snippet}"


def _header_name_for(env_var: str) -> str:
    upper = env_var.upper()
    if "BEARER" in upper or upper.endswith("_TOKEN") or upper.endswith("_API_KEY"):
        return "Authorization"
    return "X-" + env_var.replace("_", "-").title()

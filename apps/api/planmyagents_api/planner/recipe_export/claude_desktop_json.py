"""Claude Desktop ``mcpServers`` config renderer.

Output drops into the user's
``~/Library/Application Support/Claude/claude_desktop_config.json``
(macOS) / ``%APPDATA%\\Claude\\claude_desktop_config.json`` (Windows)
under the ``mcpServers`` key. The user merges (not replaces) into
their existing config.

Reference: https://modelcontextprotocol.io/docs/clients/claude-desktop

Only MCP-server-style recommendations with a concrete install command
end up in the JSON. Non-MCP recommendations and unknown MCP install
commands are surfaced in metadata because Claude Desktop config should
not contain placeholder executable entries.
"""

from __future__ import annotations

import json
from typing import Any

from planmyagents_api.planner.recipe_export import RecipeContext, RenderedRecipe
from planmyagents_api.planner.recipe_export.install_check import (
    is_check_enabled,
    is_install_resolvable_via_npm,
)


def render(context: RecipeContext) -> RenderedRecipe:
    mcp_servers: dict[str, dict[str, Any]] = {}
    non_mcp_steps: list[dict[str, Any]] = []
    missing_steps: list[dict[str, Any]] = []
    unexportable_steps: list[dict[str, Any]] = []

    for step in context.steps:
        rec = step.recommendation
        if rec is None:
            missing_steps.append(
                {"capability": step.capability, "description": step.description}
            )
            continue
        if rec.provider_type == "mcp_server":
            if not rec.install_command.strip():
                unexportable_steps.append(
                    _unexportable_step(
                        step_capability=step.capability,
                        step_description=step.description,
                        provider_id=rec.provider_id,
                        provider_type=rec.provider_type,
                        docs_url=rec.docs_url,
                        reason="MCP install command is unknown.",
                    )
                )
                continue
            if is_check_enabled() and is_install_resolvable_via_npm(
                rec.install_command
            ) is False:
                unexportable_steps.append(
                    _unexportable_step(
                        step_capability=step.capability,
                        step_description=step.description,
                        provider_id=rec.provider_id,
                        provider_type=rec.provider_type,
                        docs_url=rec.docs_url,
                        reason=(
                            "MCP install command points to an npm package "
                            "that does not resolve (install_unresolvable)."
                        ),
                    )
                )
                continue
            key = _config_key(rec.provider_id)
            mcp_servers[key] = _build_mcp_entry(rec, step.inputs)
        else:
            unexportable_steps.append(
                _unexportable_step(
                    step_capability=step.capability,
                    step_description=step.description,
                    provider_id=rec.provider_id,
                    provider_type=rec.provider_type,
                    docs_url=rec.docs_url,
                    reason="Claude Desktop config only supports MCP server entries.",
                    required_env_vars=rec.required_env_vars,
                )
            )
            non_mcp_steps.append(unexportable_steps[-1])

    payload: dict[str, Any] = {
        "_planmyagents": {
            "goal_id": context.goal_id,
            "goal_text": context.goal_text,
            "generated_by": "PlanMyAgents recipe export — claude_desktop_json",
            "merge_into": (
                "~/Library/Application Support/Claude/claude_desktop_config.json"
                " (macOS) or %APPDATA%\\Claude\\claude_desktop_config.json"
                " (Windows). DO NOT REPLACE — merge the mcpServers map only."
            ),
            "step_count": context.step_count,
            "coverage": context.coverage.to_dict(),
            "mcp_step_count": len(mcp_servers),
            "non_mcp_steps": non_mcp_steps,
            "missing_steps": missing_steps,
            "unexportable_steps": unexportable_steps,
            "notes": list(context.notes),
        },
        "mcpServers": mcp_servers,
    }

    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    return RenderedRecipe(
        body=body,
        content_type="application/json",
        filename_suffix=".json",
    )


def _config_key(provider_id: str) -> str:
    """Claude Desktop keys are short identifiers without spaces."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in provider_id)
    return safe.lower().strip("-") or "provider"


def _build_mcp_entry(rec, _inputs: dict[str, Any]) -> dict[str, Any]:
    """Build one ``mcpServers`` value.

    install_command of the form ``npx -y @vendor/package`` is split
    into ``command`` + ``args``. Free-form install commands fall
    back to a single ``command`` shell. ``env`` lists the required
    env vars with placeholder values so the user knows what to fill.
    """
    parts = rec.install_command.split()
    command = parts[0]
    args = parts[1:]

    env = {name: f"YOUR_{name}_HERE" for name in rec.required_env_vars}
    entry: dict[str, Any] = {"command": command, "args": args}
    if env:
        entry["env"] = env
    if rec.docs_url:
        entry["_docs"] = rec.docs_url
    return entry


def _unexportable_step(
    *,
    step_capability: str,
    step_description: str,
    provider_id: str,
    provider_type: str,
    docs_url: str,
    reason: str,
    required_env_vars: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "capability": step_capability,
        "description": step_description,
        "provider_id": provider_id,
        "provider_type": provider_type,
        "docs_url": docs_url,
        "required_env_vars": list(required_env_vars),
        "reason": reason,
    }

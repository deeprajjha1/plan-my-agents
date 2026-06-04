"""Cursor recipe renderer.

Cursor's MCP config lives at ``~/.cursor/mcp.json`` (global) or
``.cursor/mcp.json`` inside a project. The shape is essentially the
same as Claude Desktop — a top-level ``mcpServers`` map. Cursor
ALSO accepts a single-shot deeplink shape we emit alongside, so
the UI can offer a one-click "Add to Cursor" button.

Reference: https://docs.cursor.com/context/model-context-protocol

The renderer additionally embeds a Cursor system-prompt template
the user can paste into a project rule (``.cursor/rules/<file>.md``)
so the model knows when to reach for each MCP server.
"""

from __future__ import annotations

import json
from typing import Any

from planmyagents_api.planner.recipe_export import (
    RecipeContext,
    RecipeStep,
    RenderedRecipe,
)
from planmyagents_api.planner.recipe_export.install_check import (
    is_check_enabled,
    is_install_resolvable_via_npm,
)


def render(context: RecipeContext) -> RenderedRecipe:
    mcp_servers: dict[str, dict[str, Any]] = {}
    coverage: list[dict[str, Any]] = []
    unexportable_steps: list[dict[str, Any]] = []

    for step in context.steps:
        rec = step.recommendation
        if rec is None:
            coverage.append(
                {
                    "ordinal": step.ordinal,
                    "capability": step.capability,
                    "status": "missing",
                    "description": step.description,
                }
            )
            continue
        status = "recommended" if rec.is_exportable else "unexportable"
        coverage.append(
            {
                "ordinal": step.ordinal,
                "capability": step.capability,
                "status": status,
                "provider_id": rec.provider_id,
                "provider_type": rec.provider_type,
                "description": step.description,
                "docs_url": rec.docs_url,
            }
        )
        if rec.provider_type == "mcp_server":
            if not rec.install_command.strip():
                unexportable_steps.append(
                    {
                        "ordinal": step.ordinal,
                        "capability": step.capability,
                        "provider_id": rec.provider_id,
                        "provider_type": rec.provider_type,
                        "reason": "MCP install command is unknown.",
                        "docs_url": rec.docs_url,
                    }
                )
                continue
            if is_check_enabled() and is_install_resolvable_via_npm(
                rec.install_command
            ) is False:
                unexportable_steps.append(
                    {
                        "ordinal": step.ordinal,
                        "capability": step.capability,
                        "provider_id": rec.provider_id,
                        "provider_type": rec.provider_type,
                        "reason": (
                            "MCP install command points to an npm package "
                            "that does not resolve (install_unresolvable)."
                        ),
                        "docs_url": rec.docs_url,
                    }
                )
                continue
            key = _config_key(rec.provider_id)
            mcp_servers[key] = _build_mcp_entry(rec)
        else:
            unexportable_steps.append(
                {
                    "ordinal": step.ordinal,
                    "capability": step.capability,
                    "provider_id": rec.provider_id,
                    "provider_type": rec.provider_type,
                    "reason": "Cursor MCP config only supports MCP server entries.",
                    "docs_url": rec.docs_url,
                }
            )

    system_prompt_lines = _system_prompt_lines(context)

    payload: dict[str, Any] = {
        "_planmyagents": {
            "goal_id": context.goal_id,
            "goal_text": context.goal_text,
            "merge_into": (
                "~/.cursor/mcp.json (global) or .cursor/mcp.json (project)."
                " DO NOT REPLACE — merge the mcpServers map only."
            ),
            "rule_file_suggestion": ".cursor/rules/planmyagents-recipe.md",
            "system_prompt_template": "\n".join(system_prompt_lines),
            "coverage_summary": context.coverage.to_dict(),
            "coverage": coverage,
            "unexportable_steps": unexportable_steps,
            "notes": list(context.notes),
        },
        "mcpServers": mcp_servers,
    }

    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    return RenderedRecipe(
        body=body,
        content_type="application/json",
        filename_suffix=".cursor.json",
    )


def _config_key(provider_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in provider_id)
    return safe.lower().strip("-") or "provider"


def _build_mcp_entry(rec) -> dict[str, Any]:
    parts = rec.install_command.split()
    command = parts[0]
    args = parts[1:]
    env = {name: f"YOUR_{name}_HERE" for name in rec.required_env_vars}
    entry: dict[str, Any] = {"command": command, "args": args}
    if env:
        entry["env"] = env
    return entry


def _system_prompt_lines(context: RecipeContext) -> list[str]:
    lines = [
        f"# Recipe: {context.goal_text}",
        "",
        "You have the following MCP servers available for this goal:",
        "",
    ]
    for step in context.steps:
        if step.recommendation is None:
            lines.append(
                f"- Step {step.ordinal} ({step.capability}): "
                f"no recommended tool — escalate to user."
            )
            continue
        rec = step.recommendation
        if not rec.is_exportable:
            lines.append(
                f"- Step {step.ordinal} ({step.capability}): "
                f"`{rec.display_name or rec.provider_id}` is a reference only; "
                "it is not installed in Cursor MCP config by this export."
            )
            continue
        lines.append(_step_prompt_line(step, rec))
    lines.append("")
    lines.append(
        "Always use the recommended tool for each step. If a tool fails, "
        "report the failure verbatim — do not silently substitute."
    )
    return lines


def _step_prompt_line(step: RecipeStep, rec) -> str:
    label = f"`{rec.display_name or rec.provider_id}`"
    return (
        f"- Step {step.ordinal} ({step.capability}): use {label} "
        f"({rec.provider_type}). {step.description}"
    )

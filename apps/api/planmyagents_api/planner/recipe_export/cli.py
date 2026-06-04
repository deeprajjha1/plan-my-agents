"""Bash CLI script recipe renderer.

Emits a runnable bash script the user can save and execute to
walk through the recipe step by step. Each step is its own block:

* MCP servers are launched via ``npx`` (or whatever
  ``install_command`` says).
* OpenAPI / api_provider steps emit ``curl`` against the documented
  base URL.
* a2a_agent / ai_agent steps emit a ``# TODO`` block linking to the
  docs URL.

Safety: every block prints a ``read -p "Press ENTER to run step
N..."`` so the user can step through and stop at any point.
"""

from __future__ import annotations

from planmyagents_api.planner.recipe_export import (
    RecipeContext,
    RecipeStep,
    RenderedRecipe,
)
from planmyagents_api.planner.recipe_export.registry_lookup import (
    EndpointInfo,
    lookup_endpoint,
)


def render(context: RecipeContext) -> RenderedRecipe:
    lines: list[str] = []
    lines.append("#!/usr/bin/env bash")
    lines.append("# PlanMyAgents recipe — bash CLI export")
    lines.append(f"# goal_id: {context.goal_id}")
    lines.append(f"# goal:    {_one_line(context.goal_text)}")
    if context.notes:
        for note in context.notes:
            lines.append(f"# note:    {_one_line(note)}")
    lines.append("")
    lines.append("set -euo pipefail")
    lines.append("")
    lines.append(
        f'echo "PlanMyAgents recipe — {context.step_count} step(s)"'
        
    )
    lines.append("")

    if not context.steps:
        lines.append("# The planner produced no sub-tasks.")
        return _emit(lines)

    env_vars = _collect_env_vars(context)
    if env_vars:
        lines.append("# Required environment variables — set these before running:")
        for env_var in env_vars:
            lines.append(f"#   export {env_var}=<your value>")
        lines.append("")
        lines.append("for var in " + " ".join(env_vars) + "; do")
        lines.append('  if [ -z "${!var:-}" ]; then')
        lines.append('    echo "ERROR: $var is required but not set" >&2')
        lines.append("    exit 1")
        lines.append("  fi")
        lines.append("done")
        lines.append("")

    for step in context.steps:
        lines.extend(_render_step(step))
        lines.append("")

    lines.append('echo "Recipe complete."')
    return _emit(lines)


def _render_step(step: RecipeStep) -> list[str]:
    rec = step.recommendation
    header = f"# Step {step.ordinal} — {step.capability}: {_one_line(step.description)}"
    lines: list[str] = [header]
    lines.append(
        f'read -r -p "Press ENTER to run step {step.ordinal} (or Ctrl-C to abort)..." _'
        
    )
    if rec is None:
        lines.append(
            f"# TODO: no recommended provider for `{step.capability}`. "
            "Discover one via PlanMyAgents and update this script."
        )
        return lines
    if rec.provider_type == "mcp_server":
        cmd = rec.install_command or f'echo "TODO: install command for {rec.provider_id} is unknown"'
        lines.append(f"# Launching MCP server: {rec.provider_id}")
        lines.append(f"# Docs: {rec.docs_url or 'n/a'}")
        lines.append(cmd)
        return lines
    if rec.provider_type in {"openapi", "api_provider", "payment_provider"}:
        endpoint = lookup_endpoint(rec.provider_id, step.capability)
        method, url = _api_method_and_url(rec, step.capability, endpoint)
        method_flag = "" if method == "GET" else f" -X {method}"
        auth_flags = _curl_auth_flags(rec.required_env_vars, endpoint)
        lines.append(f"# Calling {rec.provider_id} via {rec.provider_type}")
        lines.append(f"# Docs: {rec.docs_url or 'n/a'}")
        if endpoint is not None and endpoint.scheme == "query":
            lines.append(
                f"# NOTE: {rec.provider_id} authenticates via query parameter "
                f"({endpoint.env_var or 'API key'}); add it to the URL string."
            )
        lines.append(f'curl -sS{method_flag}{auth_flags} "{url}"')
        return lines
    lines.append(
        f"# {rec.provider_type} step — see docs and adapt manually: "
        f"{rec.docs_url or 'n/a'}"
    )
    return lines


def _api_method_and_url(
    rec, capability: str, endpoint: EndpointInfo | None
) -> tuple[str, str]:
    """Resolve ``(method, url)`` for an API step's curl line.

    Priority is identical to ``n8n_json._http_method_and_url``: registry
    first, capability-slug fallback when the provider/capability pair
    is not in the registry (e.g. discovered candidates, synthetic
    fixtures). Keeps existing tests green.
    """
    base = (rec.api_base_url or "https://example.com").rstrip("/")
    if endpoint is not None and endpoint.path:
        return endpoint.method, base + endpoint.path
    return "GET", f"{base}/{capability.replace('_', '-')}"


def _curl_auth_flags(
    required_env_vars: tuple[str, ...], endpoint: EndpointInfo | None
) -> str:
    """Render the auth portion of a curl line.

    Behaviour matrix:
    * ``bearer`` (Resend / Firecrawl): ``-H "Authorization: Bearer $ENV"``
    * ``header`` (Apollo): ``-H "<header_name>: $ENV"`` — NO Bearer prefix
    * ``basic``  (Razorpay): ``-u "$KEY_ID:$KEY_SECRET"``
    * ``query``  (Hunter): nothing on the curl line; the caller emits a
      ``# NOTE`` above the curl explaining the query-param shape.
    * ``none`` / no registry entry: fall back to the pre-existing
      ``Authorization: Bearer`` heuristic so synthetic fixtures still
      work.
    """
    if endpoint is not None:
        if endpoint.scheme == "bearer":
            primary = endpoint.env_var or (required_env_vars[0] if required_env_vars else "")
            if not primary:
                return ""
            return f' -H "Authorization: Bearer ${{{primary}}}"'
        if endpoint.scheme == "header":
            primary = endpoint.env_var or (required_env_vars[0] if required_env_vars else "")
            if not primary or not endpoint.header_name:
                return ""
            return f' -H "{endpoint.header_name}: ${{{primary}}}"'
        if endpoint.scheme == "basic":
            if len(required_env_vars) >= 2:
                return f' -u "${{{required_env_vars[0]}}}:${{{required_env_vars[1]}}}"'
            if required_env_vars:
                return f' -u "${{{required_env_vars[0]}}}"'
            return ""
        if endpoint.scheme in {"query", "none"}:
            return ""
    if required_env_vars:
        primary = required_env_vars[0]
        return f' -H "Authorization: Bearer ${{{primary}}}"'
    return ""


def _collect_env_vars(context: RecipeContext) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for step in context.steps:
        if step.recommendation is None:
            continue
        for env_var in step.recommendation.required_env_vars:
            if env_var not in seen_set:
                seen.append(env_var)
                seen_set.add(env_var)
    return seen


def _one_line(value: str) -> str:
    return value.replace("\n", " ").strip()


def _emit(lines: list[str]) -> RenderedRecipe:
    body = ("\n".join(lines) + "\n").encode("utf-8")
    return RenderedRecipe(
        body=body,
        content_type="text/x-shellscript; charset=utf-8",
        filename_suffix=".sh",
    )

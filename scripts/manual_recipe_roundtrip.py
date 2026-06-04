"""Manual round-trip validator for recipe exports.

Renders all five recipe formats against synthetic-but-real
recommendations (real npm-published MCP servers, real public API base
URLs), then writes them to disk so an operator can copy them into
Claude Desktop / Cursor / n8n verbatim.

Used by docs/manual-test-log.md to produce the first end-to-end
"would-this-actually-run?" evidence for the deck claim that "one
config edit validates a recipe in Claude Desktop / Cursor".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.recipe_export import (  # noqa: E402
    RECIPE_FORMATS,
    RecipeContext,
    RecipeStep,
    RecommendedProvider,
    render_recipe,
)


OUTPUT_DIR = ROOT / ".planmyagents_runs" / "manual-roundtrip" / "synthetic"


def real_mcp_filesystem() -> RecommendedProvider:
    """Real, npm-published MCP server (filesystem)."""

    return RecommendedProvider(
        provider_id="modelcontextprotocol-server-filesystem",
        display_name="MCP Server: Filesystem",
        provider_type="mcp_server",
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        install_command="npx -y @modelcontextprotocol/server-filesystem /tmp",
        api_base_url="",
        required_env_vars=(),
        verification_status="known_provider",
        benchmark_status="not_started",
    )


def real_mcp_memory() -> RecommendedProvider:
    """Real, npm-published MCP server (in-memory knowledge graph)."""

    return RecommendedProvider(
        provider_id="modelcontextprotocol-server-memory",
        display_name="MCP Server: Memory",
        provider_type="mcp_server",
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        install_command="npx -y @modelcontextprotocol/server-memory",
        api_base_url="",
        required_env_vars=(),
        verification_status="known_provider",
        benchmark_status="not_started",
    )


def real_http_apollo() -> RecommendedProvider:
    """Real public API (Apollo) — for n8n HTTP-node export."""

    return RecommendedProvider(
        provider_id="apollo",
        display_name="Apollo",
        provider_type="api_provider",
        docs_url="https://apolloio.github.io/apollo-api-docs/",
        install_command="",
        api_base_url="https://api.apollo.io/v1",
        required_env_vars=("APOLLO_API_KEY",),
        verification_status="known_provider",
        benchmark_status="not_started",
    )


def build_context() -> RecipeContext:
    return RecipeContext(
        goal_id="g_manual_roundtrip_synth",
        goal_text=(
            "Manual round-trip: read a local file, fetch a URL, and enrich a "
            "contact via Apollo."
        ),
        steps=(
            RecipeStep(
                ordinal=1,
                capability="file_read",
                description="Read /tmp/notes.txt via MCP filesystem.",
                inputs={"path": "/tmp/notes.txt"},
                recommendation=real_mcp_filesystem(),
            ),
            RecipeStep(
                ordinal=2,
                capability="workflow_memory",
                description="Remember intermediate state via MCP memory server.",
                inputs={"key": "last_contact", "value": "deepraj"},
                recommendation=real_mcp_memory(),
            ),
            RecipeStep(
                ordinal=3,
                capability="contact_enrichment",
                description="Enrich a contact via Apollo HTTP API.",
                inputs={"email": "jha.deepraj@gmail.com"},
                recommendation=real_http_apollo(),
            ),
        ),
        planner_tier="manual:synthetic",
        cost_estimate_usd=0.0,
        notes=(
            "Manual round-trip test fixture; recommendations bypass /goal "
            "qualification gates intentionally so the exporter is exercised "
            "against real packages and real public APIs.",
        ),
    )


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    context = build_context()
    for format_id in RECIPE_FORMATS:
        rendered = render_recipe(context, format_id)
        out_path = OUTPUT_DIR / f"recipe_{format_id}{rendered.filename_suffix}"
        out_path.write_bytes(rendered.body)
        print(f"  {format_id:22s} -> {out_path} ({len(rendered.body)} bytes)")
    summary = {
        "goal_id": context.goal_id,
        "goal_text": context.goal_text,
        "coverage": context.coverage.to_dict(),
        "steps": [
            {
                "ordinal": s.ordinal,
                "capability": s.capability,
                "provider": s.recommendation.provider_id if s.recommendation else None,
                "type": s.recommendation.provider_type if s.recommendation else None,
                "install": s.recommendation.install_command if s.recommendation else "",
                "url": s.recommendation.api_base_url if s.recommendation else "",
            }
            for s in context.steps
        ],
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

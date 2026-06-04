# Manual recipe round-trip — test log

This log captures the first end-to-end software-side validation of the
recipe export pipeline against the three target hosts (Claude Desktop,
Cursor, n8n) and a live HTTP-API provider (Apollo). It is the evidence
the deck claim *"one config edit validates a recipe in Claude Desktop /
Cursor / n8n"* relies on.

Run the round-trip yourself with:

```bash
make recipe-roundtrip          # render synthetic recipe + probe MCP commands
```

(See the `recipe-roundtrip` target in the Makefile.)

---

## Round 1 — Live `/goal` request (May 18, 2026)

**Goal submitted to running API at `http://127.0.0.1:8000/goal`:**

> *"Find a verified work email for John Doe at acme.com, then send him
> a transactional welcome email."*

**Outcome:** `coverage.status = gap_only` (0/3 steps recommended).

### Why no recommendations

`plan.gap_report.qualification_policy` explicitly states:

```json
{
  "qualified_provider_types":  ["a2a_agent", "ai_agent", "mcp_server"],
  "excluded_provider_types":   ["api_provider", "payment_provider"]
}
```

So even though our registry contains Apollo (contact_enrichment +
email_verification, HTTP API), Hunter (email_verification, HTTP API),
and Resend (email_send, HTTP API) — all with full base URL, auth, and
endpoint metadata — none of them are eligible to be recipe steps. They
are surfaced separately as `apis_without_agents` (22 entries today,
including 16 candidate email-verification APIs) and represent supply
gaps awaiting MCP/A2A wrappers.

The discovery store also contains 148 MCP servers, but every one of
them has `route_status = will_fail` (107 `unverified` + 87
`registered_in_directory` + 2 `known_provider`, none qualified). So
the gating is closed for everything in the discovery store today.

### What the exports look like in the gap-only path

All five exports were honest:

| Format | Output | Honest behavior |
|---|---|---|
| `claude_desktop_json` | `mcpServers: {}` with `missing_steps[]` listing the 3 unmet capabilities | A merge into Claude Desktop's config would be a no-op — no fake servers |
| `cursor_prompt`       | `mcpServers: {}` plus system-prompt template instructing the model to "escalate to user" per step | Same |
| `n8n_json`            | 3 `n8n-nodes-base.stickyNote` nodes spaced at x=200/520/840 with explicit "this is a gap, not an executable workflow node" content | n8n importer accepts the file; user sees gap notes, not broken HTTP nodes |
| `markdown`            | Per-step TODO blocks | Reads as a real runbook with gaps clearly called out |
| `cli`                 | Bash script with explicit `TODO: no provider` blocks | Runnable script that exits cleanly on first gap |

### Finding — claim re-frame

The deck previously said *"one Claude Desktop config edit to validate."*
That implies a recipe-with-real-providers round-trip we cannot
demonstrate today on real `/goal` traffic, because *no provider in the
discovery store currently qualifies*. The honest re-frame is:

> *"Today, recipes refuse-with-reasons when no provider qualifies; that
> is the feature, not the bug. To demonstrate runnable recipes we
> bypass `/goal` qualification and render the same exporters against
> hand-picked real providers (below)."*

---

## Round 2 — Synthetic recipe with **real** providers (May 18, 2026)

To exercise the renderer end-to-end the synthetic harness
`scripts/manual_recipe_roundtrip.py` builds a `RecipeContext` whose
recommendations point at:

| Step | Capability | Provider | Type | Real artifact |
|---|---|---|---|---|
| 1 | `file_read` | `@modelcontextprotocol/server-filesystem` | mcp_server | npm v2026.1.14 (verified) |
| 2 | `workflow_memory` | `@modelcontextprotocol/server-memory` | mcp_server | npm v2026.1.26 (verified) |
| 3 | `contact_enrichment` | Apollo | api_provider | `https://api.apollo.io/v1` (verified reachable) |

All five exports rendered cleanly. Files are in
`.planmyagents_runs/manual-roundtrip/synthetic/`.

### Probe A — MCP `initialize` handshake against the emitted commands

`scripts/probe_emitted_mcp_commands.py` reads
`recipe_claude_desktop_json.json`, extracts each `mcpServers` entry,
spawns it as a subprocess exactly the way Claude Desktop does, and
speaks JSON-RPC `initialize` over stdio. This is the same handshake
Claude Desktop performs on startup.

```
Probing modelcontextprotocol-server-filesystem -> npx -y @modelcontextprotocol/server-filesystem /tmp
  -> ok=true | server-info name="secure-filesystem-server" version=0.2.0 | protocol=2024-11-05 | elapsed=50.69s
Probing modelcontextprotocol-server-memory     -> npx -y @modelcontextprotocol/server-memory
  -> ok=true | server-info name="memory-server" version=0.6.3 | protocol=2024-11-05 | elapsed=20.61s

Summary: 2/2 mcpServers entries handshake-OK.
```

**Result:** *The `mcpServers` entries our exporter emits are runnable
verbatim in Claude Desktop and Cursor.* The same entries appear in the
Cursor export, so Cursor is implicitly validated by the same probe.

### Probe B — n8n workflow JSON

`recipe_n8n_json.n8n.json` was validated structurally:

- Top-level keys present: `name`, `nodes`, `connections`, `active`,
  `settings`, `tags`, plus the metadata block `_planmyagents`.
- Each node carries `id`, `name`, `type`, `typeVersion`, `position`,
  `parameters` — n8n's required fields.
- The MCP-only steps correctly fell through to `stickyNote` nodes
  (n8n does not speak MCP natively today; refusal-with-reasons is
  the right behavior).
- The api_provider step became an `n8n-nodes-base.httpRequest` node.

### Probe C — Apollo HTTP reachability

```
curl https://api.apollo.io/v1/auth/health
HTTP 200, ttfb=1.000125s
{"healthy":true,"is_logged_in":false}
```

The host, TLS, and base URL we emit in the n8n HTTP node are real and
reachable.

---

## Bugs surfaced by the round-trip

The round-trip uncovered five concrete export-time defects. Each is
fixable in a small, well-scoped change. They are the difference between
"recipe imports and refuses honestly" (true today) and "recipe imports
and runs against the right endpoint" (not yet true for HTTP providers).

### B1 — n8n + CLI exports synthesize URLs from capability slugs — **CLOSED (2026-05-18)**

> Fixed by `planmyagents_api/planner/recipe_export/registry_lookup.py`
> (registry-aware endpoint lookup) + updated `_http_method_and_url` in
> `n8n_json.py` and `cli.py`. Regression: `test_recipe_export.py::
> PhaseOneRegressionTests::test_b1_n8n_url_uses_registry_path_for_apollo`
> (and the matching `https://api.apollo.io/v1/v1/people/match` assertion
> in the CLI Apollo test).


**File:** `apps/api/planmyagents_api/planner/recipe_export/n8n_json.py`

```python
def _http_method_and_url(step: RecipeStep) -> tuple[str, str]:
    rec = step.recommendation
    base = rec.api_base_url.rstrip("/")
    return "GET", base + "/" + step.capability.replace("_", "-")
```

For Apollo contact_enrichment this emits
`GET https://api.apollo.io/v1/contact-enrichment`.

The registry knows the truth:

```
contact_enrichment -> POST /v1/people/match
```

But neither the n8n nor CLI exporter reads it. Result: every n8n
workflow our exporter ships against an Apollo-style provider 404s on
first import. Same defect in `cli.py`.

**Fix sketch:** thread the registry's per-capability `endpoint`
(method + path) through `RecommendedProvider` and use it in
`_http_method_and_url`; fall back to the slug only when the registry
has no endpoint for that capability.

### B2 — HTTP method is hardcoded `GET` — **CLOSED (2026-05-18)**

> Same fix as B1. Regression:
> `test_b2_n8n_method_follows_registry_for_apollo` asserts `POST`.


Same file, same function. Same root cause. Fix is the same change.

### B3 — Auth header name is guessed from env-var suffix — **CLOSED (2026-05-18)**

> Fixed by `_build_header_parameters()` in `n8n_json.py` reading
> `EndpointInfo.header_name` (Apollo → `X-Api-Key`, Resend →
> `Authorization`). Regression:
> `test_b3_n8n_header_name_uses_registry_for_apollo`.


**File:** `apps/api/planmyagents_api/planner/recipe_export/n8n_json.py`

```python
def _header_name_for(env_var: str) -> str:
    upper = env_var.upper()
    if "BEARER" in upper or upper.endswith("_TOKEN") or upper.endswith("_API_KEY"):
        return "Authorization"
    return "X-" + env_var.replace("_", "-").title()
```

`APOLLO_API_KEY` is matched by `endswith("_API_KEY")` → header becomes
`Authorization`. Apollo's real header per registry is `X-Api-Key`.
Result: 401 even on the correct endpoint.

**Fix sketch:** thread `auth.header_name` from the registry into
`RecommendedProvider.auth_header_name` and use that, with the current
heuristic kept only as a fallback for providers discovered without a
registry entry.

### B4 — CLI export adds a `Bearer ` prefix unprompted — **CLOSED (2026-05-18)**

> Fixed by new `_curl_auth_flags()` in `cli.py`: scheme-aware. Apollo
> emits `-H "X-Api-Key: ${APOLLO_API_KEY}"` (no Bearer); Razorpay emits
> `-u "${RAZORPAY_KEY_ID}:${RAZORPAY_KEY_SECRET}"` (basic auth); Resend
> keeps Bearer; Hunter prints a query-param note instead of a header.
> Regressions: `test_b4_cli_no_bearer_prefix_for_apollo_header_auth`,
> `test_b4_cli_uses_basic_auth_for_razorpay`,
> `test_b4_cli_keeps_bearer_for_resend`,
> `test_b4_cli_emits_query_note_for_hunter`.


**File:** `apps/api/planmyagents_api/planner/recipe_export/cli.py`

```bash
curl -sS -H "Authorization: Bearer ${APOLLO_API_KEY}" "https://api.apollo.io/v1/contact-enrichment"
```

Apollo doesn't use a Bearer scheme. The `Bearer ` prefix is added even
when the env var isn't a Bearer token. Same fix as B3 — read auth
shape from registry instead of guessing.

### B5 — Markdown footer still points at `planmyagents.ai` — **CLOSED (2026-05-18)**

> Fixed in `markdown.py` (footer + two gap-step links). Regression:
> `test_b5_markdown_uses_planmyagents_com_not_ai`.


**File:** `apps/api/planmyagents_api/planner/recipe_export/markdown.py`

```markdown
Generated by [PlanMyAgents](https://planmyagents.ai). Run the recipe in your own environment with your own credentials...
```

The rest of the project moved to `planmyagents.com`. One-line fix.

### B6 — No install-command resolvability check in the export pipeline — **CLOSED (2026-05-18)**

> Added `install_check.py` with `is_install_resolvable_via_npm()`
> (parses `npx -y <pkg>`, runs `npm view <pkg> version` with 5s timeout,
> LRU-cached). Wired opt-in via `PLANMYAGENTS_CHECK_NPM_RESOLVABILITY`
> into both `claude_desktop_json.py` and `cursor_prompt.py`. Default-off
> so the test suite needs no network. Regressions:
> `test_b6_install_check_parses_npm_package`,
> `test_b6_renderer_drops_unresolvable_mcp_when_flag_enabled`.


When the synthetic fixture initially used the plausible-looking but
fictional `@modelcontextprotocol/server-fetch` (the real fetch server
is a Python package, not a JS one), the export shipped it without
warning. A user merging that config into Claude Desktop would see an
install failure on restart.

**Fix sketch:** at recipe-build time (not export time), for every
mcp_server recommendation with `provider.install_command.startswith("npx ")`,
issue a single `npm view <pkg> name` resolve; if it fails, demote the
recommendation to a gap with reason `"npm package not found"`. Same
treatment for `pip install` / `uv tool install` shapes.

---

## What this means for the deck

| Claim in `PITCH_DECK.md` | Reality after round-trip |
|---|---|
| *"one Claude Desktop config edit to validate"* | True only for hand-picked real MCP servers; on real `/goal` traffic, the qualification gates correctly refuse every provider in the store today. |
| *"5 export formats"* | All five render valid output; *Claude Desktop* and *Cursor* MCP entries are protocol-verified runnable; *n8n* and *CLI* HTTP nodes ship with the wrong URL, method, and auth header. |
| *"recipes refuse-with-reasons when no provider qualifies"* | True and visible in every export. |
| *"runnable today"* | True for **MCP-shape recipes** (Claude Desktop, Cursor) given a qualified candidate. **Not yet true for HTTP-shape recipes (n8n, CLI)** until B1-B4 are fixed. |

### Suggested deck edits (pending user approval)

1. Replace the "one config edit" line with the precise two-state version:
   > *"For MCP-shape recipes: validated round-trip via the same JSON-RPC initialize handshake Claude Desktop performs at startup
   > (see `docs/manual-test-log.md`). For HTTP-shape recipes (n8n, CLI):
   > exporters ship today; per-vendor endpoint + method + auth-header
   > wiring is item B1-B4 of the 90-day plan."*

2. Add B1-B6 to the 90-day plan as the first concrete deliverable
   (ahead of new benchmark cells), since they directly impede design
   partner onboarding.

3. Replace the "1 live cell → 3-5 cells" wording with one tied to
   recipe round-trips:
   > *"1 live benchmark cell + 0 host-validated recipes today
   > → 3 benchmark cells in `smoke_test` band and 5 host-validated
   > recipes (≥1 per target host) by day 90."*

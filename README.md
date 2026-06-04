# PlanMyAgents

> **The trust, recipe, and marketplace platform for the AI agent economy.**

PlanMyAgents is the planning, discovery, and trust layer for the world's exploding supply of AI agents, MCP servers, and A2A agents. We help **users** find known, tested, and runnable providers for each sub-task, then return honest recipes or explicit refusal reasons when the system cannot safely route. We give **vendors** a future vendor-neutral marketplace to be discovered, benchmarked, and surfaced to high-intent buyers. We **partner with** workflow tools (n8n, Zapier), LLM hosts (Claude Desktop, Cursor, Cline), and protocol authorities (Anthropic, Google) to power their agent recommendations.

We are **G2 + Hugging Face + Gartner Digital Markets** for the agent economy.

---

## One‑line positioning

> *"Tell us what you want done. We tell you which AI agents are known, tested, and runnable for each sub-task, hand you an honest recipe, and refuse with reasons when the evidence is not good enough."*

---

## What this is

The AI agent supply is exploding — 1,000+ MCP servers, hundreds of A2A agents, thousands of AI‑native services per month — but it is **fragmented, unranked, unverified, and unbenchmarked**. Buyers waste hours hand‑evaluating. Vendors have no neutral place to be discovered. LLM hosts want to recommend agents but can't be the trust authority themselves. Workflow tools (n8n, Zapier) need a vendor‑neutral discovery layer they don't have to build.

PlanMyAgents fills that void as a three‑phase product:

| Phase | What we do | Who it's for | Status |
|---|---|---|---|
| **1 — Plan + Discover + Recipe** | Decompose goals into sub‑tasks, discover candidate providers, rank with evidence, export recipes when steps are actually exportable, and refuse honestly when gates fail | Developers, operators, anyone using LLM hosts | Shipping now; execution is gated |
| **2 — Marketplace** | Vendors claim their listings, get tested benchmarks, run sponsored category placements (with full disclosure), pay for demand‑data API | Agent vendors (Apollo, Hunter, Perplexity, Linkup, Apify, etc.) | Year 1 unlock |
| **3 — Partnership Platform** | OEM into n8n / Zapier / Claude Desktop / Cursor / Cline; enterprise private registries; standards influence | Workflow tools, LLM hosts, cloud platforms, enterprise teams | 🟡 Year 2 unlock |

We are deliberately **not** Zapier, **not** an executor with stored credentials, **not** a hand‑coded API wrapper farm. Our moat is the index + benchmark + recipe + marketplace flywheel — not connector count.

---

## What this is not (anti‑positioning)

| Not | Why we refuse |
|---|---|
| **A Zapier / n8n / Make competitor** | They run workflows; we recommend agents to those workflows. We're a layer above; we partner, we don't compete |
| **An executor with stored credentials** | We never hold customer keys. Optional execution is BYO‑credentials, in user's session, protocol‑only (MCP / A2A / OpenAPI) |
| **A workflow builder UI** | Users execute recipes in their LLM host (Claude Desktop, Cursor, Cline) or workflow tool (n8n, Zapier) of choice |
| **A pay‑to‑rank marketplace** | Rankings are vendor‑neutral; vendors pay for visibility (sponsored, claimed profile) — never for ranking position. Even the optional vendor‑side execution fee is capped at 2% and decoupled from ranking. G2's firewall, ours too. |
| **A hand‑coded API wrapper farm** | We support agents through open protocols (MCP, A2A, OpenAPI) only. Vendors that want in expose a protocol‑compliant interface |
| **A frontier‑model competitor** | We are vendor‑neutral and multi‑host — Claude, GPT, Gemini, Cursor, Cline, your own LLM app all get the same recipes |

---

## The flywheel — why this compounds

```
                       ┌──────────────────────────────┐
                       │  USERS                       │
                       │  (devs + operators)          │
                       └──────────┬───────────────────┘
                                  │ free Discovery + Recipes
                                  ▼
       ┌────────────────────────────────────────────────────────────┐
       │  PLANMYAGENTS                                              │
       │  Planning + Discovery engine                               │
       │  ↑ pulls from agent supply (17+ scouts, public sources)    │
       │  ↓ pushes recipes into workflow tools + LLM hosts          │
       └──────────┬─────────────────────────────────────┬───────────┘
                  │ recipe handoff                      │ benchmark + trust signals
                  ▼                                     ▼
       ┌────────────────────────────┐     ┌────────────────────────────┐
       │  WORKFLOW HOSTS / LLM HOSTS│     │  AGENT VENDORS             │
       │  (n8n, Zapier, Claude      │     │  (claimed profiles,        │
       │   Desktop, Cursor, Cline)  │     │   verified benchmarks,     │
       │                            │     │   sponsored placement)     │
       │  → distribution + co-mkt   │     │  → marketplace ARR         │
       └─────────────┬──────────────┘     └─────────────┬──────────────┘
                     │                                  │
                     └──────────────┬───────────────────┘
                                    ▼
              ┌──────────────────────────────────────────┐
              │  PROTOCOL AUTHORITIES                    │
              │  (Anthropic-MCP, Google-A2A, OpenAPI)    │
              │  → joint methodology, brand validity     │
              └──────────────────────────────────────────┘
```

Every arrow is a flywheel:
- **Users → vendors:** more goal traffic → more vendor demand to be listed → vendor revenue
- **Vendors → users:** more vendor benchmarks published → more credible recipes → more user trust → more goal traffic
- **Hosts → users:** more LLM/workflow hosts integrating recipes → more reasons to plan with us
- **Users → hosts:** more high‑quality recipes → more reasons for hosts to integrate
- **Authorities → all:** joint methodology with Anthropic / Google legitimizes the whole loop

---

## Architecture at a glance

```
USER GOAL
   │
   ▼
[ PLANNING — LLM goal decomposer + label reconciler ]
   │
   ▼
[ DISCOVERY — 17+ scouts → pgvector index → trust ladder → judge ]
   │
   ▼
[ PACKAGING — provider partition → ranker → recipe generator ]
   │
   ▼
[ HANDOFF — Claude Desktop / n8n / Cursor / Cline / markdown ]
   │
   ▼
USER EXECUTES IN OWN ENVIRONMENT (BYO credentials, never ours)
```

Plus, in Phase 2, the **vendor portal** for claimed profiles + benchmark certification + sponsored placement. In Phase 3, the **partner API** that lets n8n / Zapier / Claude Desktop / Cursor pull our recommendations directly.

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/HLD.md](docs/HLD.md), [docs/LLD.md](docs/LLD.md), [docs/marketplace-design.md](docs/marketplace-design.md), [docs/partnership-strategy.md](docs/partnership-strategy.md).

---

## Status (May 2026)

Working prototype with 1,200+ backend tests passing. Pre‑revenue, pre‑incorporation; raising seed.

| Layer | Status |
|---|---|
| Discovery scouts (Smithery, MCP Marketplace, Glama, official MCP Registry, npm registry, GitHub code search, GitHub recently-pushed, GitHub awesome-lists, APIs.guru, Brave/Tavily, A2A directories, vendor docs, Hacker News, Moltbook, AI directories, curated JSON) | ✅ Implemented |
| Discovery store (Postgres + pgvector, 4 physically separate tables) | ✅ Implemented |
| Goal decomposer + capability descriptions catalog | ✅ Implemented |
| Embedding pipeline (OpenAI / Ollama / deterministic‑hash) | ✅ Implemented |
| Capability index + label reconciler | ✅ Implemented |
| Verification tier ladder + qualification gate | ✅ Implemented |
| LLM candidate judge | ✅ Implemented |
| Per‑capability provider partition | ✅ Implemented |
| Pre‑plan discovery + post‑goal refresh + MCP tool probe | ✅ Implemented |
| Generic protocol adapters (BYO‑credentials gated) | ✅ Implemented |
| Cost cap + spend ledger | ✅ Implemented |
| Public leaderboards UI + `/open-mcp-opportunities` | ✅ Implemented |
| Recipe export endpoint (Claude / n8n / Cursor / markdown) | 🟡 Q1 sprint |
| Pro tier (saved recipes, teams) | 🟡 Q2 sprint |
| **Vendor portal (claimed profiles, sponsored placement)** | 🟡 Year 1 end |
| **Verified Benchmark certification** | 🟡 Year 1 end |
| **Partner API (n8n / Cursor / Anthropic integrations)** | 🟡 Year 2 |
| Test suite | ✅ 1,200+ backend tests passing (`make test-fast` for the 1,190 deterministic subset, `make test` for the full 1,219 incl. live-LLM `/goal` integration tests) |

---

## Quick start

PlanMyAgents runs as two processes (FastAPI on `:8000`, Next.js on `:3000`) backed by Postgres + pgvector and an LLM (local Ollama by default, or hosted Groq).

**Prerequisites:** Python 3.12+, Node 20+, Docker Desktop, Ollama (or a `GROQ_API_KEY`). [Full prereq + version-check matrix in the Get Started guide.](docs/GET_STARTED.md#1--prerequisites)

```bash
# One-time
make install                  # Python venv + backend deps
make web-install              # Next.js deps
ollama pull qwen3.5:35b       # ~22 GB; skip if you'll use Groq instead
cp .env.example .env          # paste GROQ_API_KEY / GITHUB_TOKEN if you have them

# Bring everything up
make stack-up                 # docker Postgres + migrations + seed
make api                      # terminal A → http://127.0.0.1:8000
make web                      # terminal B → http://127.0.0.1:3000
```

Open <http://localhost:3000>. Submit a goal (e.g. `"buy cheapest glenlivet in tamil nadu"`) — expect a 60–120 s wait with the local Qwen planner, then an honest "Plan outline only" outcome card with sub-tasks rendered. That refusal is the success signal.

**For the full walkthrough — prerequisites with version checks, first-run smoke test, planner-tier selection, troubleshooting matrix, repo map — read [`docs/GET_STARTED.md`](docs/GET_STARTED.md).**

### Frontend pages

| Path | What it shows |
|---|---|
| `/` | Live evidence strip + goal input |
| `/goal` | Submitted-goal outcome (sub-tasks, recipe, planner reasoning) |
| `/categories/{cluster}` | Per‑candidate cards with verification / benchmark / routability badges |
| `/agents/{provider_id}` | Vendor info, capabilities, ranking history (Phase 2: vendor‑claimed profile) |
| `/search` | Keyword + embedding search (pgvector cosine when Postgres is wired) |
| `/leaderboards/{capability}` | Per‑capability rankings with credibility banners |
| `/open-mcp-opportunities` | Capabilities where APIs exist but no MCP/A2A wraps them |
| `/vendor` (planned, Phase 2) | Vendor portal — claim profile, run benchmark, sponsor category |

### Useful commands

```bash
make test-fast                       # 1,190 deterministic tests (~45 s) — inner loop
make test                            # full 1,219 tests incl. live-LLM /goal cases (~10 min)
make lint                            # ruff check
make quality                         # test + lint + smoke pipelines
make discovery-refresh               # refresh candidates from configured sources
make growth-pass                     # live-research + full index pass (needs paid scout keys)
make env-check                       # show wired vs missing connector keys
make rebuild-rankings                # rebuild agent_rankings from raw benchmark_runs
```

---

## Strategic & engineering documents

| Document | Audience | What it covers |
|---|---|---|
| [docs/GET_STARTED.md](docs/GET_STARTED.md) | New contributors | Fresh-laptop setup — prereqs, 5-min happy path, first-`/goal` smoke, planner selection, test loops, troubleshooting, repo map |
| [PITCH_DECK.md](PITCH_DECK.md) | VCs, partners, hires | 25‑slide Marp deck — problem, why now, three‑phase product, marketplace, partnership flywheel, ask |
| [BUSINESS_PLAN.md](BUSINESS_PLAN.md) | VCs, board, advisors | Full company narrative — market, ICP, three‑phase product ladder, 9‑line revenue ladder (incl. optional execution fee), marketplace economics, partnership ecosystem, financials, risks |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Engineering leads | Cross‑cutting architecture, design principles, trust model, scalability, three‑phase architectural evolution |
| [docs/HLD.md](docs/HLD.md) | Senior engineers | Subsystem responsibilities + contracts + sequence diagrams + data model (incl. vendor portal + partner API subsystems) |
| [docs/LLD.md](docs/LLD.md) | Engineers shipping | Module APIs, database schemas (incl. vendor + sponsorship + benchmark certification + partner integration tables), algorithms, error handling |
| [docs/marketplace-design.md](docs/marketplace-design.md) | Product + engineering | Vendor portal flow, claimed profile mechanics, benchmark certification process, sponsorship disclosure rules, vendor‑neutrality firewall |
| [docs/partnership-strategy.md](docs/partnership-strategy.md) | Founder + BD + execs | Tier 1/2/3 partnership map, deal shapes, revenue share, risk register, sequencing |
| [docs/operations.md](docs/operations.md) | On‑call, DevOps | Hosted deployment, runbooks |
| [docs/benchmark-methodology.md](docs/benchmark-methodology.md) | Buyers, external analysts | How we score, what "publishable" / "verified" mean |
| [docs/agent-discovery-index.md](docs/agent-discovery-index.md) | Engineers | Discovery sub‑system internals |
| [docs/competitor-redteam.md](docs/competitor-redteam.md) | Anyone challenging the wedge | Honest competitor list and answers to objections |

Historical context (kept for provenance, superseded by the docs above): [docs/technical-architecture.md](docs/technical-architecture.md) (v0 architecture, May 2026 pre‑pivot).

---

## Strategic decisions (the spine)

These choices anchor every doc and every code decision. Full rationale in [BUSINESS_PLAN.md](BUSINESS_PLAN.md).

| Decision | Choice |
|---|---|
| **Core product engine** | Planning + Discovery — the proprietary IP. Everything else (recipe, benchmark, marketplace) is monetization on top. |
| **Year 1 ICP** | Developer / agent builder using Claude Desktop, Cursor, Cline, or own LLM apps |
| **First proof category** | Developer agent workflows — MCP/A2A supply is densest there |
| **Connector strategy** | We index public agents; we never write proprietary connector code; protocol‑only adapters |
| **Execution stance** | None by default. Optional BYO‑credentials sandbox via MCP / A2A / OpenAPI in user session |
| **Marketplace model** | G2‑strict for ranking (visibility yes, ranking position never) **+ optional 1–2% vendor‑opt‑in execution fee** (vendor pays out of their own margin on sandboxed executions through our BYO‑creds runtime; user price never changes; fully disclosed) |
| **Tier‑1 partnerships** | **n8n + Cursor + Anthropic** — fastest distribution + dev mindshare + protocol authority. Founding vendor cohort is a parallel marketplace workstream, not a partnership tier. |
| **Monetization (in unlock order)** | Free discovery → Pro subscription → Benchmark API → Enterprise registry → Claimed Profile → Verified Badge → Sponsored Placement → Demand‑Data API → **Optional Execution Fee** |
| **Moat** | Discovery freshness × verification evidence × benchmark history × refusal demand data × marketplace network effects × partnership distribution |

---

## License

Source code is currently private. Documentation in this repo is also private until first public release. © 2026 PlanMyAgents.

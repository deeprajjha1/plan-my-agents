# PlanMyAgents Competitor & Red-Team Memo

> Purpose: make the strongest case **against** PlanMyAgents, then define what must be true for the company to be worth building.

This memo should be read before any fundraising conversation. If a VC asks a hard question, the answer should already be here.

---

## 1. The Most Dangerous Objection

> "Isn't this just Clay, Apollo waterfall enrichment, or a workflow wrapper around existing APIs?"

This is the central risk.

If PlanMyAgents is perceived as a better enrichment UI, it will lose. Clay is too strong, Apollo has too much distribution, and ZoomInfo/Cognism own enterprise data trust.

PlanMyAgents must instead be positioned as:

> The independent benchmark and routing layer across specialist tools/agents, starting with GTM data because GTM data is measurable.

The first product may look like enrichment execution, but the company must be sold as **trusted provider selection + execution infrastructure**.

---

## 2. Clay

### Why Clay is dangerous

Clay is the hardest beachhead competitor:

- It owns AI-powered GTM workflow mindshare.
- It raised major capital and has a multibillion-dollar valuation.
- It already offers waterfall enrichment across many providers.
- It has an AI research agent, CRM enrichment, inbound enrichment, and outbound workflow automation.
- It has community energy and practitioner love.

### How Clay can kill PlanMyAgents

Clay can add:

- public or private provider benchmarks
- routing recommendations
- cost optimization dashboards
- better provider-comparison UX
- "best source for this field" logic

If PlanMyAgents's product is only "route enrichment providers and output CSV," Clay can copy it.

### PlanMyAgents's response

Do not fight Clay as a workflow builder. Fight for a different mental category:

| Clay | PlanMyAgents |
|---|---|
| GTM workflow builder | independent provider benchmark + router |
| UI-first GTM automation | infrastructure + API-first routing |
| mostly customer workflow execution | public methodology + continuous evaluation |
| product value | trust/data value |

Clay users are actually a good first audience. The right discovery question is:

> "Where does Clay's waterfall or credit model still feel opaque, expensive, or hard to trust?"

If Clay users cannot name repeated pains, PlanMyAgents should not start in GTM data.

---

## 3. Apollo, ZoomInfo, Cognism, and Data Vendors

### Why they are dangerous

These companies own the underlying data. PlanMyAgents does not.

Apollo and ZoomInfo can bundle enrichment, verification, routing, sequencing, CRM sync, and workflow automation. Cognism owns GDPR-first positioning in Europe. If data quality is the only value, PlanMyAgents is structurally weak.

### PlanMyAgents's response

PlanMyAgents should not claim to own data. It should claim to know:

- which provider performs best for a specific task
- when to use multiple providers
- when not to spend credits
- which outputs are low confidence
- when a provider is degrading

That is a routing and observability problem, not a data-ownership problem.

---

## 4. Sapiom

### Why Sapiom is dangerous

Sapiom raised ~$15M from Accel to build a financial layer for AI agents to buy software, APIs, data, compute, and tools through one API. It reportedly abstracts identity, wallets, budgets, and billing across hundreds of services.

This overlaps PlanMyAgents's "unified access to providers" idea.

### PlanMyAgents's response

Do not compete on spend API. Sapiom is better capitalized and more directly aligned with agent payments.

PlanMyAgents should focus on:

- agent/provider discovery quality
- benchmark quality
- routing decisions
- task execution outcomes
- refusal logic

Sapiom may become a payment/access layer PlanMyAgents integrates later.

---

## 5. Lio

### Why Lio matters

Lio raised ~$30M from a16z for enterprise procurement agents. It proves VCs believe in autonomous purchasing workflows. It also shows that agentic procurement is already a funded category.

### Why Lio is not the direct near-term threat

Lio is enterprise procurement. PlanMyAgents starts in measurable GTM/data workflows for mid-market or technical operators. Different buyer, workflow, and ACV.

### Risk

If PlanMyAgents moves into procurement/vendor research later, Lio becomes a real competitor.

---

## 6. AgentBench, AgentSearchBench, Steel.dev, and Benchmark Ecosystem

### Why they matter

PlanMyAgents's benchmark story is not uncontested. There are already benchmarks for:

- general LLM agents
- tool use
- search agents
- web navigation
- coding agents
- agent discovery

AgentSearchBench specifically studies searching among real-world agents, which is conceptually close to PlanMyAgents's longer-term vision.

### PlanMyAgents's differentiation

PlanMyAgents must emphasize **commercial discovery + workflow execution**, not general agent research:

| Research benchmarks | PlanMyAgents |
|---|---|
| search/evaluate agents/models in test environments | discover, gate, and evaluate providers in live workflows |
| academic/research audience | operator + developer + vendor audience |
| one-time benchmark suites | continuous execution + real-task feedback |
| not tied to routing revenue | directly improves customer execution |

If PlanMyAgents only publishes benchmarks and never routes work, it may become a useful but non-VC-scale content/data project.

---

## 7. Frontier Models

### Why frontier models are dangerous

ChatGPT, Gemini, Claude, Perplexity, and similar systems are getting better at:

- browsing
- tool use
- multi-step planning
- structured outputs
- scheduled tasks
- spreadsheet-style workflows

Many tasks that look complex today may become one-shot prompts tomorrow.

### PlanMyAgents's defensible territory

PlanMyAgents should only handle workflows with at least two of these:

- proprietary data behind paid APIs
- high-volume/bulk execution
- deterministic cost ceilings
- source metadata
- repeatability
- provider choice
- confidence scoring
- compliance/privacy requirements
- scheduled execution
- cross-tool reconciliation

If a task can be solved by a frontier model in one prompt with acceptable quality, PlanMyAgents should not build for it.

---

## 8. The "Public Agent Web" Assumption

### Risk

The original founder vision assumes a large public marketplace of paid agents. That market is not mature today.

There are early signs:

- MCP servers
- A2A Agent Cards
- x402 payment endpoints
- AP2/ACP protocols
- agent registries

But supply is fragmented and uneven. Many "agents" are wrappers, demos, or platform-locked skills.

### PlanMyAgents's adjustment

Start with paid APIs and tools that already work. Design the architecture so MCP/A2A/x402 agents can be added later.

Do not wait for the agent web. Build on today's supply while positioning for tomorrow's.

---

## 9. Legal and Privacy Risks

GTM data workflows touch PII:

- names
- job titles
- LinkedIn URLs
- work emails
- sometimes phone numbers

Risks:

- GDPR/CCPA compliance
- scraping terms of service
- email deliverability harm
- misuse by spammy customers
- vendor sub-processor obligations

Mitigations:

- work-email only in v0
- no personal emails or phone numbers by default
- explicit customer attestation of lawful basis
- public sub-processor list
- data deletion API
- no LinkedIn scraping by default unless legal review approves
- avoid selling to obvious spam operators

---

## 10. Why PlanMyAgents Might Still Be Worth Building

The bull case survives if these become true:

1. Public benchmark creates trust and traffic.
2. Provider quality/cost variance is large enough to matter.
3. Buyers are frustrated with opaque credit burn and provider choice.
4. PlanMyAgents's execution output is materially better than using one platform.
5. Real-task data improves routing over time.
6. The benchmark/routing layer generalizes to more workflow categories.

This is not a guaranteed company. It is a venture-scale bet on trusted orchestration becoming a durable layer in the agentic software stack.

---

## 11. What Would Kill The Idea Quickly

Kill or radically reposition if:

- Clay users say they are happy and do not care about provider transparency.
- Benchmark tests show little variance between providers.
- Users care only about UI/workflow builder features, not routing quality.
- Provider API costs make margins unattractive.
- Users do not repeat tasks; everything is one-off.
- The public leaderboard gets no organic attention.
- Compliance risk makes the beachhead too constrained.

The most disciplined version of PlanMyAgents is willing to stop after 30-60 days if the benchmark does not reveal a real wedge.

---

## 12. Questions To Ask Every Potential Customer

1. Which enrichment/research tools do you use today?
2. Where do you waste credits?
3. Which outputs do you distrust?
4. How do you decide which provider to use for which field?
5. Have you compared provider accuracy yourself?
6. What task do you repeat weekly that still requires manual checking?
7. If PlanMyAgents showed you the provider choice, confidence, sources, and cost, would that change your workflow?
8. What would make you choose PlanMyAgents over just using Clay?

The answer to question 8 determines whether the company has a wedge.

---

## 13. Final Red-Team Verdict

PlanMyAgents is weak if framed as:

- a universal public-agent marketplace
- a Clay competitor
- a payment layer for agents
- a generic multi-agent orchestrator

PlanMyAgents is strongest if framed as:

- trusted benchmarks for specialist tools/agents
- routing infrastructure for measurable workflows
- cost-controlled execution with refusal logic
- a data moat built from real workflow performance

The company is fundable only after the founder proves the wedge with public benchmark credibility and real repeated workflow usage.

# PlanMyAgents Agent Benchmark — Methodology v0

> The benchmark is the **data moat**. Treat its design like product design — opinionated, transparent, evolving.

> **Core principle:** *Open methodology, open test cases, transparent scoring. The community can audit our work and contribute test cases. We win on execution at scale, not on opaque magic.*

---

## 1. Why we benchmark (and why nobody else does it well)

Today, every claim about an AI agent's quality is self-asserted. Vendors say "best email accuracy in the industry"; nobody verifies. Buyers waste money. Good agents lose to louder ones.

Existing benchmark efforts have specific gaps:
- **Vendor-published benchmarks** = self-graded; biased by design
- **Academic benchmarks (HELM, BIG-bench, etc.)** = focus on foundation LLMs, not specialist agents
- **G2 / Gartner reviews** = subjective user reviews, not capability tests
- **One-off comparison blog posts** = stale within 90 days

**PlanMyAgents's benchmark fills the gap:** automated, continuous, capability-specific tests across the actual public agent ecosystem, with results computed weekly and published for free.

---

## 2. Core principles

1. **Capability-specific, not general.** "Best agent for email verification" is a meaningful claim. "Best agent overall" is not.
2. **Reproducible.** Every test case is versioned. Every run is logged with timestamp + agent version + cost + raw response.
3. **Cost-aware.** Quality without cost context is useless. We always report quality *per dollar*.
4. **Transparent.** Test cases are public (in `packages/benchmarks/`). Methodology is public (this doc). Scoring formula is public.
5. **Bias-mitigated.** Use multiple LLM judges. Diverse test case authors. Adversarial test cases included.
6. **Updated continuously.** Weekly re-runs catch regressions in agents AND in our methodology.
7. **Auditable.** Anyone can re-run any test case and verify our reported score.

---

## 3. Capability taxonomy (v0)

We benchmark **5 capabilities** in v0. Each capability has **50 test cases**.

| Capability ID | Definition | Verifiability |
|---|---|---|
| `contact_enrichment` | Given (name, company), return (verified email, verified LinkedIn URL, current title) | Strong (we can verify emails via SMTP, verify LinkedIn URLs via lookup) |
| `email_verification` | Given an email address, return is_deliverable + confidence | Strong (cross-check against ground truth set) |
| `web_scraping` | Given a URL + extraction schema, return structured data matching the schema | Strong (compare against curated ground-truth extracts) |
| `semantic_search` | Given a natural-language query, return top-N most relevant URLs | Medium (LLM-as-judge for relevance scoring; multi-judge consensus) |
| `company_data_lookup` | Given a company domain, return (employee count, industry, HQ, founded year, recent funding) | Strong (cross-check against Crunchbase + LinkedIn ground truth) |

---

## 4. Test case design

### 4.1 Each capability has exactly 50 test cases organized as:

| Bucket | Count | Purpose |
|---|---|---|
| **Easy** (well-known companies, common scenarios) | 15 | Catches basic regressions; all good agents should ace these |
| **Medium** (mid-tier companies, some ambiguity) | 25 | The differentiation zone |
| **Hard** (small/private companies, edge cases, recently changed data) | 7 | Separates great from good |
| **Adversarial** (trick cases, deliberate ambiguity, "no good answer") | 3 | Tests honest failure — does the agent say "I don't know" or hallucinate? |

### 4.2 Test case structure (file format)

Each test case is a YAML file under `packages/benchmarks/<capability>/<id>.yaml`:

```yaml
# packages/benchmarks/contact_enrichment/0001-stripe-cto.yaml
id: 0001-stripe-cto
capability: contact_enrichment
difficulty: easy
created_at: 2026-05-04
created_by: founder
inputs:
  first_name: Patrick
  last_name: Collison
  company_domain: stripe.com
expected:
  title:
    accept: ["CEO", "Chief Executive Officer", "Co-founder", "Co-founder & CEO"]
    weight: 0.3
  linkedin_url:
    accept: ["https://www.linkedin.com/in/patrickcollison/"]
    weight: 0.3
  email:
    # We don't ground-truth personal emails for privacy; just check format
    format_check: "^.+@stripe\\.com$"
    weight: 0.4
notes: |
  Patrick Collison is well-known and unambiguous. Any agent that fails this
  is broken. Title is straightforward. LinkedIn is canonical.
```

### 4.3 Adversarial test case example

```yaml
# packages/benchmarks/contact_enrichment/0050-fictional-person.yaml
id: 0050-fictional-person
capability: contact_enrichment
difficulty: adversarial
created_at: 2026-05-04
created_by: founder
inputs:
  first_name: Bartholomew
  last_name: Quinglesnoth
  company_domain: nonexistent-fake-company-xyz.com
expected:
  must_indicate_unknown: true
  forbidden_outputs:
    - "Bartholomew Quinglesnoth"  # if returned at all, agent hallucinated
notes: |
  Designed to catch hallucination. Correct behavior: return null/empty
  with high confidence "no record found." Incorrect: return a fake
  email like b.quinglesnoth@nonexistent-fake-company-xyz.com.
```

### 4.4 Test case versioning

- Test cases are immutable once published. **Never edit existing test cases.**
- If a test case becomes invalid (e.g., the person changed jobs), retire it: set `retired: true` in the YAML.
- New test cases get new IDs. Always append.
- Quarterly: review retired-rate per capability. If >20%, rebuild the suite.

---

## 5. The scoring formula

### 5.1 Per-call score (0.0 – 1.0)

For each agent call against a test case, compute:

```
quality_score = weighted_field_match_score(actual, expected)
```

For numeric / categorical fields: exact match or in `accept` list = full weight; else 0.
For free-text fields: cosine similarity against `accept` (with threshold 0.85).
For "must_indicate_unknown" cases: 1.0 if agent returned null/empty/explicit "not found"; 0.0 otherwise.

### 5.2 Per-agent-per-capability composite score (computed nightly)

For each (agent, capability) pair over the last **7 days** of benchmark runs:

```
success_rate     = count(succeeded=true) / count(*)
avg_quality      = avg(quality_score) over succeeded runs
p50_latency_ms   = median(latency_ms)
p95_latency_ms   = 95th percentile(latency_ms)
avg_cost_usd     = avg(cost_usd) over succeeded runs

# Normalize each metric to [0, 1] across all agents in this capability
norm_quality     = avg_quality
norm_latency     = 1.0 - clamp(p95_latency_ms / 5000, 0, 1)   # 5s = floor
norm_cost        = 1.0 - clamp(avg_cost_usd / max_cost_in_capability, 0, 1)
norm_success     = success_rate

# Weighted composite (weights are PUBLIC and tunable)
composite_score = (
    0.40 * norm_success
  + 0.35 * norm_quality
  + 0.15 * norm_cost
  + 0.10 * norm_latency
)
```

### 5.3 Weight defaults and rationale

| Weight | Default | Rationale |
|---|---|---|
| `success_rate` | 0.40 | Reliability is table stakes. An unreliable agent is useless regardless of quality. |
| `quality` | 0.35 | When it works, how good is the output? |
| `cost` | 0.15 | Material but not dominant. Customers will pay more for better. |
| `latency` | 0.10 | Important but most tasks are async; sub-5s is fine. |

**Customers in higher-tier plans can supply their own weights** via the routing API:
```http
POST /api/v1/route?capability=contact_enrichment
&weight.quality=0.6&weight.cost=0.05&weight.success=0.3&weight.latency=0.05
```

---

## 6. LLM-as-judge methodology (for unverifiable outputs)

Some outputs (e.g., free-text summaries, semantic search relevance) cannot be ground-truth-matched. We use LLM-as-judge.

### 6.1 Multi-judge consensus

Each soft-graded output gets scored by a configurable panel:
1. current best Claude Sonnet-class model
2. current best OpenAI reasoning/tool-use model
3. current best Gemini Pro-class model

Final quality score = median of the three. If max - min > 0.3, the case is flagged for human review.

### 6.2 Judge prompt template

```
You are scoring an AI agent's response to a task.

TASK: {test_case.task_description}
EXPECTED CHARACTERISTICS: {test_case.expected_characteristics}
AGENT RESPONSE: {agent_response}

Score the response from 0.0 to 1.0 on these dimensions:
- Accuracy (matches expected characteristics)
- Completeness (covers all aspects of the task)
- Honesty (admits when uncertain; doesn't hallucinate)

Return JSON:
{
  "accuracy": <float 0-1>,
  "completeness": <float 0-1>,
  "honesty": <float 0-1>,
  "overall": <weighted avg>,
  "reasoning": "<2-3 sentences>"
}
```

### 6.3 Bias mitigations

- **Don't tell the judge which agent produced the response** (responses are anonymized in judging)
- **Randomize order** when judging multiple responses to same test case
- **Re-judge a 5% sample monthly with human reviewers** to detect judge drift
- **Open-source the judge prompts** so anyone can replicate

---

## 7. Continuous benchmarking schedule

| Frequency | Action |
|---|---|
| **Hourly** | Health-check ping to each agent (track uptime) |
| **Daily** | Run 5 randomly-selected test cases per agent per capability (smoke test for regressions) |
| **Weekly** (Sundays 00:00 UTC) | Run **full** 50-case suite per agent per capability. Recompute composite scores. Publish to leaderboard. |
| **Monthly** | Add 5 new test cases per capability based on real customer task patterns (from `agent_calls` table). Bring total per capability to 55, 60, etc. over time. |
| **Quarterly** | Methodology review; possibly tune weights; publish "State of the Agent Web" report. |

### 7.1 Cost of running benchmarks

Per weekly full run:
- 50 cases × 10 agents × ~$0.02 avg cost = **~$10/week** in vendor fees
- Plus ~$5/week in LLM judge calls
- **Total: ~$15/week in v0; ~$50/week as we add agents**

This is a **legitimate operating cost** of the business. Budget $250–$1,000/month for it. Don't cut it; it's the moat.

---

## 8. Real-task benchmark augmentation

Synthetic test cases are great but limited. Real customer tasks generate **far more diverse signal** at zero marginal cost.

For every paid customer task that runs through PlanMyAgents:
- Each `agent_call` is automatically logged into `benchmark_runs` with `source='real_task'`
- If the customer **accepts** the result without rerun → strong positive signal
- If the customer **requests rerun** or **flags an issue** → negative signal
- If the customer **picks a different agent next time** for the same capability → comparative signal

After 6 months of paid traffic, the `real_task` signal **dominates** synthetic signal because volume is 100x higher. **This is the data moat compounding.**

### 8.1 Privacy guarantee for real-task signals

- Real-task benchmark rows store **only metrics** (latency, cost, success, quality_score), never raw inputs/outputs
- Customer-identifying data is never included in published rankings
- Per customer DPA, we explicitly disclose this aggregate use

---

## 9. The leaderboard (public-facing)

URL: `https://planmyagents.dev/leaderboard` (or `/agents/<capability>` for category page)

### 9.1 What's shown publicly
- Ranking per capability with composite score
- Sub-scores: success rate, avg quality, p95 latency, avg cost
- Sample size (always > 100 calls in last 7 days for inclusion)
- Trend arrow (↑ ↓ → vs. last week)
- Last updated timestamp

### 9.2 What's NOT shown publicly
- Specific test case results (these are pulled from a versioned set; vendors can't game them by inspecting which they're failing)
- Customer-identifying real-task data
- The exact composite scoring formula's tuning constants from one tier (transparency at L0; Pro tier sees the deeper data)

### 9.3 Vendor response process
- Vendors get **email notification** when their score drops by >10% week-over-week
- Vendors can **submit a re-run request** if they believe a test case is invalid (we adjudicate based on YAML spec)
- Vendors can **submit new test cases** for review, but we evaluate fairness independently

---

## 10. Methodology governance

### 10.1 Editorial backbone (Gartner-style)
We will receive vendor pressure. Defenses:
- **Methodology is open-source.** Anyone can review, fork, propose changes.
- **No "pay for placement"** in rankings. Verified Agent program (paid certification) is **separate** from rankings — Verified status is a badge, not a score boost.
- **Rankings are not for sale.** Period.
- **Disclosed conflicts:** if PlanMyAgents ever invests in or is invested by an agent vendor, this is disclosed publicly.

### 10.2 Methodology change process
1. Proposed change opened as a GitHub issue with rationale
2. 30-day public comment period
3. Vendor comments published verbatim
4. Decision made by founder (and eventually a small advisory board)
5. Change announced in monthly digest with full rationale

### 10.3 Disputes & appeals
- Vendor believes a test case is unfair → submit dispute via dedicated form
- Adjudication: founder reviews against YAML spec + judge consensus
- If upheld, test case retired and replaced with a new one
- All dispute decisions published (anonymized) in a public registry

---

## 11. Open questions for v0 → v1

These are deferred decisions to revisit at quarterly methodology review:

- [ ] Should we weight cost differently for high-stakes capabilities (e.g., compliance) vs. cheap ones (e.g., search)?
- [ ] Should real-task signal weight increase over time vs. synthetic?
- [ ] How do we handle agents that change pricing mid-quarter?
- [ ] Should we publish per-region rankings (e.g., agents may perform differently for EU vs. US data)?
- [ ] Should there be a "trust tier" (verified human review of top-3 agents in each category)?
- [ ] How do we handle agents that go down for >24hr — auto-suspend from rankings, or zero-score?
- [ ] Should we publish historical leaderboard data (last 12 months) at launch or wait?

---

## 12. Initial test-case checklist (Day 1–14 work)

Before code: build the test case library. Solo-feasible in 2 weekends.

For each of the 5 capabilities:
- [ ] 15 easy cases (well-known companies/scenarios)
- [ ] 25 medium cases (mid-tier; real diversity)
- [ ] 7 hard cases (edge / rare / recently-changed)
- [ ] 3 adversarial cases (hallucination traps)
- [ ] Total: 50 cases × 5 capabilities = **250 test cases**

Format: YAML, committed to `packages/benchmarks/<capability>/`.

This 250-case library, even before any agent integration, is **a defensible artifact in itself.** Open-source it under CC-BY-SA. Becomes the canonical reference set the community converges on.

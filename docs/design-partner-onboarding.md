# Design-partner onboarding playbook

> **Audience:** the founder running a 30-min onboarding call with one
> of the first 5 design partners from `PITCH_DECK.md` slide 13
> ("Founder + ask"). Each partner is a developer or operator who already
> uses Claude Desktop, Cursor, or n8n with MCP and is willing to test
> the planner against a real goal.
>
> **Outcome of a good call:** the partner leaves with (1) one routed
> plan, (2) one refusal-with-reasons, (3) one recipe export they can
> import into their host, and (4) a row in `capability_demand_events`
> the founder can refer to in the next conversation.
>
> **Time budget:** 30 minutes for the call + 10 minutes of prep + 10
> minutes of post-call follow-up. Anything over that means the playbook
> needs to get sharper, not the call longer.

Last updated: 2026-05-19 — sprint-6 / A.

---

## 0. Pre-call prep (10 min, day-of)

1. **Refresh the cron**

   ```sh
   make evidence-cron               # verify + benchmark cron in one shot
   make backfill-phase5-cells       # idempotent — ensures 3 routable cells
   ```

   Verify the homepage strip reads **"3 cells routable (30d)"** at
   `http://localhost:3000` (or the deployed URL).

2. **Snapshot demand for the partner-specific window**

   ```sh
   LOOKBACK_DAYS=14 make demand-snapshot
   open data/demand_snapshot.csv
   ```

   Skim the top 5 rows. If anything there overlaps with the partner's
   day-to-day (e.g. partner is a sales-eng → look for `contact_enrichment`,
   `email_verification`, `email_send`), highlight those rows during the
   call.

3. **Open the internal cockpit in a separate tab**

   `http://localhost:3000/dev/demand` — keep it open. The founder uses
   it as live commentary during the call ("look — your goal just landed
   as a row").

4. **Open `/partners` in a third tab.** That's the page the partner
   sees if they Google PlanMyAgents during the call. It must match what
   the founder is saying.

5. **Choose ONE goal in advance.** Don't ask the partner "what would
   you test it on" cold — that wastes the first 5 minutes. Pick a goal
   from the partner's last week of work (LinkedIn, blog, or a previous
   email exchange) and propose it in the calendar invite.

---

## 1. Call structure (30 min, kept honest by a timer)

### 1.1 First 5 minutes — context, not pitch

- "PlanMyAgents is a trust + ranking + benchmark layer for AI agents
  across MCP, A2A, and AI-native services. We don't execute on your
  behalf — we recommend providers, then export a recipe for you to run
  on your host."
- "The point of this call is for you to break our planner with a real
  goal, not for me to demo a slide deck."

Don't show the deck. Show `/goal`.

### 1.2 Next 10 minutes — routed plan + refusal

- Type the pre-picked goal into `/goal`.
- One of two things happens:
  - **Routed** → walk through the plan, the cost estimate, the recipe
    download buttons (Cursor / Claude / n8n / CLI). Note the source of
    each step — the partner should be able to point at every row and
    ask "where did that come from?" and the founder should answer with
    a registry path or a benchmark cell.
  - **Refused** → walk through the refusal card. Why the refusal? Which
    sub-task was missing? What would unblock it? Open
    `/dev/demand` in the second tab and show the new row appearing in
    real time.

Either outcome is a good outcome. The refusal path is often *better*
because it shows the demand-recording moat working.

### 1.3 Next 10 minutes — recipe export

- Hand the partner the exported recipe in their preferred host.
  - n8n → import the JSON, walk through the visual graph
  - Cursor → drop the markdown into a Cursor command
  - Claude Desktop → paste the MCP-tools list into the user's
    `claude_desktop_config.json`
  - CLI → run the bash script in a sandbox
- The recipe MUST work on the partner's machine in their own session
  with their own keys. If it breaks, that's the most valuable bug
  report we can get this week — write it down verbatim and open an
  issue before the call ends.

### 1.4 Last 5 minutes — close the loop

- Ask: "When would you naturally use this again? Tomorrow? Next sprint?
  Never?" The answer is the signal we care about; the partner's polite
  feedback ("nice prototype") is not.
- Promise ONE specific follow-up by ONE specific date. Examples:
  - "I'll add a benchmark cell for `<the capability you needed>` and
    ping you when it's routable. Target: 2 weeks."
  - "I'll wire your preferred host's recipe-export format more sharply
    and send you a link. Target: this Friday."
- Add the partner's preferred capability to the top of
  `sprint-pitch-align.md §15` ("After this sprint") so the next sprint
  is driven by their data, not founder intuition.

---

## 2. Post-call follow-up (10 min, same day)

1. **Send a thank-you with the routed plan / refusal link.** Paste the
   exact `/goal` request id and a link to `/dev/demand` filtered to
   their capability. Reinforce that their goal is now visible in the
   public demand signal at `/demand`.

2. **Snapshot the post-call demand state.**

   ```sh
   LOOKBACK_DAYS=1 make demand-snapshot OUTPUT=data/partner-<partner-handle>-<YYYY-MM-DD>.csv
   ```

   Commit the CSV under `data/partner-debriefs/`. The next call with
   the same partner starts with "here's what happened in the 24h after
   our last call".

3. **File one GitHub issue per bug.** Always. Even if the partner says
   "it's fine, no rush". The bug log is the moat — it becomes the
   sprint-7 list.

4. **Update `sprint-pitch-align.md §15`.** Add the partner's name +
   their preferred capability to the next-sprint section. If multiple
   partners ask for the same capability, that capability jumps to top
   of the wrapper investment list (see `scripts/pick_top_capabilities_for_wrappers.py`).

---

## 3. Anti-patterns to avoid

| Don't | Why |
|---|---|
| Show the deck before they've broken `/goal` | Selling the abstract before showing the concrete loses every time. |
| Promise a feature you don't already have a sprint plan for | We are a 1-engineer shop. Promised-not-shipped is the fastest way to lose credibility. |
| Ask the partner to use a real production key during the call | We don't execute workflows. Live keys are out of scope for PlanMyAgents and you'll spook them. |
| Skip the post-call CSV snapshot | The 24h "what happened next" is the leading indicator we use to decide whether to keep investing in this partner. Skipping it makes every later conversation guesswork. |
| Treat the refusal flow as a failure | It is the highest-signal thing PlanMyAgents does. A vendor reading the public `/demand` page later sees exactly that demand and decides to ship. |

---

## 4. After 5 partners — what to do with the data

When `data/partner-debriefs/` has 5 CSVs:

1. Run `scripts/pick_top_capabilities_for_wrappers.py` and rank by
   demand × distinct-partner-count. The top 3 capabilities are the
   sprint-7 wrapper batch (`sprint-pitch-align.md §15.4`).

2. Send one outreach email per vendor in `PITCH_DECK.md` slide 13's
   vendor list, hyperlinking the exact `/demand` row that justifies
   their inclusion. Use the templates in `docs/outreach-templates.md`
   (TBD — file the next sprint).

3. Re-run `make deck-pdf` after editing slide 13's status row to
   reflect the partners' aggregate demand picture. Send the refreshed
   deck to investors only AFTER this step — pre-partner numbers are
   founder-fiction and pattern-match badly.

---

## 5. Operator commands cheat-sheet

```sh
# Daily during onboarding sprint
make evidence-cron                 # verify + benchmark cron
LOOKBACK_DAYS=1 make demand-snapshot

# Per-partner (after each call)
LOOKBACK_DAYS=1 OUTPUT=data/partner-debriefs/<handle>-$(date +%F).csv make demand-snapshot

# Health
make health-evidence               # CLI version of /health/evidence
make check-evidence-health         # CI guard, exits non-zero if cron dropped

# Wider audit
LOOKBACK_DAYS=30 OUTPUT=data/demand-sprint-debrief.csv make demand-snapshot
```

URLs to bookmark during the sprint:

| URL | Use |
|---|---|
| `/goal` | The shared screen during a call. |
| `/dev/demand` | Internal cockpit — live demand + gaps + verification. |
| `/demand` | Public version — safe to share with vendors after call. |
| `/partners` | The page the partner sees if they Google PlanMyAgents. |
| `/discovery-gaps` | Per-capability zero-yield rollup. |
| `/open-mcp-opportunities` | "API exists, no MCP wraps it" — vendor build list. |
| `/health/evidence` | JSON. Use to prove the cron is alive. |

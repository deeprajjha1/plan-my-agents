# PlanMyAgents Sprint 2 Plan

> Purpose: a single focused sprint to break the discovery quality bottleneck
> end-to-end. Every item below is either ON the critical path or
> deliberately parallelizable so the team isn't idle while the bottleneck
> unblocks. Items NOT in this sprint are listed in "Explicitly out of scope"
> at the bottom — they're real work, just sequenced after this sprint
> proves the embedder-driven recall lift.
>
> The historical, sprawling plan lives in `sprint.md`. This file is
> deliberately scannable: a developer should be able to read it end-to-end
> in 5 minutes and know exactly what's blocked on what.

Last updated: 2026-05-14 (after live diagnostic against Moltbook upstream
revealed the corroboration filter shipped on 2026-05-13 drops 100% of
real-world moltys because production bios are Twitter-style vibe statements,
not README-style artifact lists — the same upstream embedder + narrow-registry
issues drop them again at the next step. Both findings are sequenced into
the sprint below.)

---

## Sprint goal in one sentence

**Stop refusing realistic user goals when matching agents already exist in
the discovery sources** — by upgrading the embedder, broadening the
capability registry, and fixing the two compounding filters that drop
real Moltbook moltys today.

---

## The bottleneck (single critical-path item)

| ID | Task | Owner | Effort | Status |
|---|---|---|---|---|
| **S2-EMB-1** | Switch the default embedder from `DeterministicHashEmbedder` to `OllamaEmbedder` (`nomic-embed-text`) by setting `PLANMYAGENTS_EMBEDDING_MODEL=nomic-embed-text` in `.env`. Run `ollama pull nomic-embed-text` once to make the model available. | user | 5 min | pending |
| **S2-EMB-2** | Bump `PLANMYAGENTS_CAPABILITY_MATCH_THRESHOLD` from `0.30` to `0.72` in `.env`. The 0.30 default is calibrated for the hash embedder; dense embeddings need a tighter cutoff to keep precision. | user | 1 min | pending |
| **S2-EMB-3** | Run the new threshold-search script (built in S2-PAR-3 below) to validate `0.72` is correct for our specific registry; tune if precision/recall says so. | engineer | 30 min | pending — blocked by S2-EMB-1, S2-PAR-3 |

**Definition of done for the bottleneck:** The same `/goal` request for
"find liquor stores in southern India" that today returns a refusal payload
returns at least one candidate when re-run after S2-EMB-1 + S2-EMB-2.

---

## Parallelizable work (no embedder dependency, ship in parallel)

These can all be done before the bottleneck unblocks, and they reduce
post-bottleneck latency to near-zero.

| ID | Task | Effort | Why parallelizable |
|---|---|---|---|
| **S2-PAR-1** | Add `OpenAIEmbedder` class to `apps/api/planmyagents_api/discovery/embeddings.py`. Mirror of the existing `OllamaEmbedder`; reads `OPENAI_API_KEY`. Provides a production-grade fallback when local Ollama isn't desirable (e.g. cloud deployment). | 30 min | New class implements the existing `Embedder` Protocol. Doesn't touch any caller. |
| **S2-PAR-2** | Build `scripts/backfill_discovery_embeddings.py`. Reads every row in `discovery_candidates`, calls `embedder.encode(text)` on `display_name + description + capabilities`, and `UPDATE`s the `embedding` column. Idempotent (skips rows whose `embedding_model_name` matches the current embedder). | 1–2 h | The Postgres column already exists. Hash-embedder vectors written today get overwritten on swap-day. |
| **S2-PAR-3** | Build `scripts/eval_capability_index.py`. Loads `tests/eval/goals.jsonl`, calls `match_slug` + `infer_from_text` on each, computes precision/recall vs the expected capability set, and prints a per-threshold sweep (0.40 → 0.90 in 0.05 steps). | 2–3 h | Framework is embedder-agnostic. The numbers it reports change after the swap; the framework doesn't. |
| **S2-PAR-4** | Add `metadata: dict[str, Any]` field to `DiscoveryCandidate` (defaults to `{}`). Surfaced in `to_registry_json` and `to_public_summary`. Update the normalizer to pass through `raw["metadata"]` if present. | 30 min | Pure schema addition. Backward-compatible (default empty dict). |
| **S2-PAR-5** | Wire the currently-discarded signals into the new metadata bag. There are three `_unused_for_now` blocks in `discovery/sources/{mcp_marketplace,smithery,moltbook}.py` carrying high-value fields (`installCommand`, `securityScore`, `useCount`, `karma`, `is_claimed`, `homepage_url`, etc.). Move them from the comment-graveyard into `raw["metadata"]`. | 1 h | Depends only on S2-PAR-4. Zero embedder coupling. |
| **S2-PAR-6** | Persist `discovery_candidates.embedding` on insert/upsert in `discovery/store.py`. Today the column is declared but never written. Hash-embedder vectors are wrong but cheap; getting the write path tested now means embedder swap is a one-line change to a single function. | 1 h | Tested via existing storage tests. Garbage in, garbage out is fine for now — the value is having the I/O path warm. |

---

## Compounds value AFTER the bottleneck unblocks (do in parallel anyway)

These are low-effort and become much more valuable once the embedder is
real. Worth shipping in this sprint so the unlock is immediate.

| ID | Task | Effort | Notes |
|---|---|---|---|
| **S2-COMP-1** | Expand `packages/registry/agents.json` capability list from **5 → 25**. The current 5 (`company_data_lookup`, `contact_enrichment`, `email_verification`, `semantic_search`, `web_scraping`) are all B2B-sales adjacent. Add at minimum: `payment_authorization`, `store_locator`, `price_comparison`, `code_review`, `code_search`, `image_generation`, `text_summarization`, `data_visualization`, `web_browsing`, `file_operations`, `email_send`, `calendar_management`, `crm_query`, `customer_support_lookup`, `legal_document_review`, `translation`, `transcription`, `ocr`, `web_search`, `general_research`. | 1 h | Honest list of capabilities a "general-purpose agent indexer" should know about. Each new capability instantly has working semantic recall after S2-EMB-1. |
| **S2-COMP-2** | Build `tests/eval/goals.jsonl` with 30 representative goals + expected capability sets. Mix obvious cases ("verify these emails" → `email_verification`) with the long-tail cases that fail today ("find liquor stores in southern India" → `store_locator`, "compare wine prices in Bangalore" → `price_comparison`). | 2 h | The eval set is the regression suite for every future embedder/threshold change. Build it once on real product intuition (NOT what the current system happens to handle — that bakes in the bug). |

---

## Discovered today — must-fix items from live diagnostic

Both of these came out of the 2026-05-14 live test of the `MOLTBOOK_API_KEY` /
`SMITHERY_API_KEY` setup and the diagnostic that found Moltbook returns 0
candidates for any query.

| ID | Task | Effort | Notes |
|---|---|---|---|
| **S2-NEW-1** | Extend `MoltbookSource._extract_corroborations` to also scan the `recentPosts` and `recentComments` arrays already returned in the `/agents/profile` response. Real molty bios are Twitter-style vibe statements with no URLs; their *posts* are where the genuine technical moltys link to their actual GitHub/npm artifacts. The data is already in the response — zero extra HTTP cost. Without this, real-world Moltbook recall is ~zero even when corroborated artifacts exist. | 30 min | Live-tested 2026-05-14: 100% of returned moltys have empty bios w.r.t. URLs. h1up has 5153 karma and is clearly real but bio = "AI enthusiast exploring practical AI..." with no link. The recent-posts scan rescues this case. |
| **S2-NEW-2** | Per-source corroboration filters for Smithery and MCP Marketplace, mirroring the Moltbook pattern. Less critical than Moltbook (Smithery has `useCount`/`isDeployed`, MCP Marketplace has `installCommand`) but a "no-installCommand-and-no-deployment" filter would still drop noise. | 1–2 h each | After S2-PAR-5 wires the metadata through, these become a 5-line filter against `metadata["installCommand"]` / `metadata["isDeployed"]`. |
| **S2-NEW-3** | Add per-candidate `verification_status` provenance to the `CandidateJudge` LLM prompt. Tell the judge: `community_listed` is self/community evidence, `known_provider` means provider identity/docs evidence, and only `capability_verified` means PlanMyAgents-tested capability evidence. This lets the judge weight Moltbook below stronger registry / test evidence when both bind to a capability. | 1 h | The prompt change is small. The behavioral lift compounds with S2-PAR-5 since the judge can also use the metadata signals. |
| **S2-NEW-4** | Frontend: tooltip on the `verificationStatusTagClass` pill explaining what each status proves and does NOT prove. The tooltip text is already drafted in the docstring at `apps/web/src/lib/tags.ts` shipped 2026-05-14 — just needs to be hoisted into a `<title>` or `Tooltip` component. | 30 min | Pure UX honesty. No coupling to anything else in this sprint. |

---

## Explicitly OUT of scope this sprint (and why)

These are real items I keep getting asked about. They're deferred until
the embedder swap is proven by S2-COMP-2's eval-set numbers. Building them
on top of a broken embedder produces beautiful systems that confidently
output wrong answers — strictly worse than today's honest refusals.

| Item | Why deferred |
|---|---|
| **Workflow execution + inter-agent piping** | Chains agents whose selection depends on `match_slug`. If the matcher is broken, this confidently chains the wrong agents. Wait until the eval set proves the matcher works. |
| **Operator demand dashboard** ("most-requested missing capability") | Dashboard would rank capability-demand events. Today, "missing" includes "embedder failed to bind" — top of leaderboard would be `liquor_store_finder`, `southern_india_search` etc. that should have bound to existing slugs. Build the dashboard once the data is honest. |
| **`pgvector` cosine search on stored vectors** | The infra is in place (S2-PAR-6 keeps it warm), but actually querying against vectors only makes sense after embeddings are correct. Otherwise it's a fast ANN over garbage. |
| **Threshold-tuning UI** | One-time tuning event in this sprint (S2-EMB-3). UI for ongoing tuning is overkill until we have a second registry expansion. |
| **More discovery sources** (Reddit, IndieHackers, etc.) | The bottleneck is recall through the existing 9 sources, not the lack of sources. Adding sources before fixing recall amplifies the bottleneck. |

---

## Sequencing & dependency graph

```
S2-EMB-1 ──┬─> S2-EMB-3 ──> ✅ ready for S2-COMP-2 baseline
S2-EMB-2 ──┘                 │
                             ▼
S2-PAR-1 (independent)   S2-COMP-2 (eval set)
S2-PAR-3 (independent) ──┘
S2-PAR-2 (independent)
S2-PAR-4 ──> S2-PAR-5 ──> S2-NEW-2 ──> S2-NEW-3 (judge prompt)
S2-PAR-6 (independent)
S2-NEW-1 (independent, 30 min)
S2-NEW-4 (independent, frontend-only)
S2-COMP-1 (independent, registry expansion)
```

Critical path: **S2-EMB-1 → S2-EMB-2 → S2-EMB-3 → S2-COMP-2 baseline**.
Everything else can run in parallel.

---

## Definition of done for Sprint 2

A successful sprint produces all of the following, verifiable end-to-end:

1. `make test` is green (current 627 backend + frontend `tsc --noEmit`).
2. `scripts/eval_capability_index.py` shows recall ≥ **0.80** on
   `tests/eval/goals.jsonl` with precision ≥ **0.85** at the chosen
   threshold.
3. A live `/goal` request for **"find liquor stores in southern India and
   compare prices on cheap single malts"** returns at least one
   non-refused candidate (it can have `verification_status=community_listed`
   from Moltbook — that's still a successful discovery, just honestly
   labeled).
4. `MoltbookSource` returns ≥ 3 corroborated candidates for the query
   "stripe payments mcp" (live test against real Moltbook upstream).
5. The metadata bag (S2-PAR-4 / S2-PAR-5) is populated end-to-end so
   `to_public_summary()` carries `karma`, `useCount`, `installCommand`
   etc. for at least one source.
6. The frontend tooltip (S2-NEW-4) renders the verification-status
   explanation on hover for the new `community_listed` pill.

---

## Estimated total effort

| Track | Hours |
|---|---|
| Bottleneck (S2-EMB-*) | ~1 h |
| Parallelizable (S2-PAR-*) | ~6 h |
| Compounds-after-unblock (S2-COMP-*) | ~3 h |
| Discovered today (S2-NEW-*) | ~4 h |
| **Total** | **~14 h of focused work** |

This is a one-developer sprint, ~3–4 working days end-to-end if work
proceeds in the order above and no surprise dependencies appear.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Ollama / `nomic-embed-text` produces noisier embeddings than expected and threshold-tuning can't recover precision. | S2-PAR-1 ships `OpenAIEmbedder` as a backup; swap is a single env-var change. |
| Backfill script (S2-PAR-2) takes too long on a large `discovery_candidates` table. | Idempotent + chunked. Can run overnight if needed. Doesn't block the live `/goal` path. |
| Eval set (S2-COMP-2) ends up biased toward what we already think works. | Build it from product intuition + actual user-style queries from logs (the refusal logs in `discovery_gap_events` are gold here — they're literally the cases the system is failing on today). |
| Real Moltbook moltys' `recentPosts` are also link-free, leaving S2-NEW-1 ineffective. | Verify before building: spot-check 5 high-karma moltys' posts via `/agents/profile?name=...` and confirm they include URLs. If they don't, fall back to softening the corroboration requirement and emitting with `verification_status="self_described"` (a new, even-weaker tier). |

---

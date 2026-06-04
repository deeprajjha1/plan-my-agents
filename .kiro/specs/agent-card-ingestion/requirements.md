# Requirements Document

## Introduction

PlanMyAgents discovers agents through registry scouts (Smithery, npm, the official MCP registry, etc.). That model cannot find an agent that exists only at its own domain — for example an A2A agent published at `https://policycheck.tools/.well-known/agent.json`. There is no enumerable index of "all domains hosting an agent card," so no scout can generalize to "find unknown agents on unknown domains."

This feature adds the missing, generalizable path: **user/vendor-submitted agent-card ingestion**. A user supplies a card URL for any domain; the platform fetches it, parses the declared skills/capabilities, runs existence-and-claim verification, and indexes the agent at an honest trust tier. Because the URL is supplied by the submitter, this works for **any** domain without crawling — it is a registration/resolution mechanism, not an open-web discovery mechanism.

The feature is deliberately scoped to the two trust levels the agent card can actually support today:

- **Existence verification** — the card is reachable and advertises the declared skills.
- **Claim extraction** — the declared skills are parsed into normalized capabilities.

It explicitly does **not** deliver **quality attestation** (proving the agent does the capability *well*). For A2A agents that requires live invocation, which is not implemented because the A2A wire format is still in flux. Quality attestation for A2A is therefore tracked as an explicit, separate, blocked-on-invocation requirement so no surface ever implies a self-asserted card claim is a verified quality score.

A hard constraint on this work: it must **reuse existing components** — `A2AAgentCardSource`, `verify_candidate`, `VerificationRecord` + the verification stores, `RoutingDiscoveryStore`, `DescriptorReader`, and `DiscoveryRunLogger` — and must not reimplement card parsing, verification, persistence, or normalization.

## Glossary

- **Card_Ingestion**: THE system specified by this document. It resolves a submitted agent-card URL into a verified, indexed discovery candidate.
- **Agent_Card**: A machine-readable self-description an agent publishes (e.g. an A2A Agent Card at `/.well-known/agent.json`) containing identity, skills, and optional auth/provider metadata.
- **Card_Resolver**: THE component that fetches and parses a submitted Agent_Card URL into a normalized `DiscoveryCandidate`, reusing the existing `A2AAgentCardSource` parsing path.
- **Existence_Verification**: Confirmation that an Agent_Card is reachable and advertises the declared skills, producing a verification status from the canonical ladder. Reuses the existing `verify_candidate`.
- **Claim_Extraction**: Parsing the Agent_Card's declared skills into normalized capabilities, reusing the existing `DescriptorReader` operation model.
- **Quality_Attestation**: A scored benchmark that proves an agent performs a capability well. Requires live invocation; for A2A it is blocked (see the Blocked requirement).
- **Trust ladder**: The canonical `verification_status` values in `discovery/normalizer.py`: `capability_verified` > `registered_in_directory` > `known_provider` > `community_listed` > `unverified`.
- **Self-asserted claim**: A capability an Agent_Card declares about itself. Reading it verifies the claim *was made*, not that it is *true*.
- **Routing facade**: The existing `RoutingDiscoveryStore` that partitions agentic candidates from `apis_without_agents`.
- **Submission**: A user/vendor-provided Agent_Card URL plus optional metadata, the input to Card_Ingestion.

## Requirements

### Requirement 1: Submit an agent card by URL (any domain)

**User Story:** As a developer or vendor, I want to submit an agent's card URL for any domain, so that an agent that exists only at its own domain can be indexed without waiting for a registry to list it.

#### Acceptance Criteria

1. WHEN a user submits an Agent_Card URL, THE Card_Ingestion SHALL accept the submission and attempt to resolve the card.
2. THE Card_Ingestion SHALL accept a card URL on any domain without requiring the domain to be pre-registered in any curated manifest or registry.
3. IF the submitted value is not an `https://` URL, THEN THE Card_Ingestion SHALL reject the submission and SHALL return a descriptive validation error.
4. WHEN a submitted URL omits a path and names only a domain, THE Card_Ingestion SHALL attempt the conventional `/.well-known/agent.json` path for that domain.
5. THE Card_Ingestion SHALL resolve the submitted card by reusing the existing `A2AAgentCardSource` parsing path and SHALL NOT reimplement agent-card parsing.

### Requirement 2: Resolve and normalize the card into a candidate

**User Story:** As a platform engineer, I want a submitted card normalized into the same candidate shape every other source produces, so that an ingested agent flows through the existing index, stores, and surfaces unchanged.

#### Acceptance Criteria

1. WHEN the Card_Resolver fetches a reachable Agent_Card, THE Card_Ingestion SHALL produce a normalized `DiscoveryCandidate` of an agentic provider type.
2. IF the Agent_Card cannot be fetched or parsed, THEN THE Card_Ingestion SHALL return a structured resolution error and SHALL NOT create a candidate.
3. THE Card_Ingestion SHALL derive the candidate's capabilities from the card's declared skills by reusing the existing `DescriptorReader` operation model.
4. WHERE a declared skill maps to no known registry capability, THE Card_Ingestion SHALL record the unmapped skill and SHALL NOT fabricate a registry capability for it.
5. THE Card_Ingestion SHALL record the submitted card URL as the candidate's evidence URL.

### Requirement 3: Existence and claim verification

**User Story:** As a credibility-conscious operator, I want a submitted agent verified for existence and claims, so that the index reflects that the agent is reachable and advertises what it claims — without implying its quality is proven.

#### Acceptance Criteria

1. WHEN a candidate is resolved from a submitted card, THE Card_Ingestion SHALL run Existence_Verification by reusing the existing `verify_candidate`.
2. WHEN the card is reachable and advertises the declared skills, THE Card_Ingestion SHALL set the candidate's verification status to a canonical trust-ladder value no higher than `capability_verified`.
3. THE Card_Ingestion SHALL NOT assign a verification status that the verification evidence does not support.
4. WHEN Existence_Verification completes, THE Card_Ingestion SHALL persist a `VerificationRecord` by reusing the existing verification store, and SHALL NOT reimplement verification persistence.
5. IF Existence_Verification fails, THEN THE Card_Ingestion SHALL index the candidate at the `unverified` tier and SHALL record the verification blockers.

### Requirement 4: Honest trust ceiling (no quality overclaim)

**User Story:** As the platform owner bound by the honesty hard rules, I want card-derived trust capped at claim-level, so that a self-asserted card claim is never presented as a verified quality score.

#### Acceptance Criteria

1. THE Card_Ingestion SHALL NOT assign a benchmark/quality status to a candidate based on its Agent_Card alone.
2. THE Card_Ingestion SHALL set the ingested candidate's benchmark status to the not-started value used by the discovery model.
3. THE Card_Ingestion SHALL keep the ingested candidate non-routable until the existing promotion gate (verified evidence + adapter + benchmark + ready-for-promotion) is satisfied.
4. WHEN a surface displays an ingested agent, THE Card_Ingestion SHALL ensure the displayed verification tier reflects only existence/claim verification and not quality attestation.
5. THE Card_Ingestion SHALL distinguish a verified-existence claim from a quality attestation in every persisted record.

### Requirement 5: Persistence and integration with existing stores

**User Story:** As a platform engineer, I want an ingested agent stored through the existing discovery store, so that it appears on the existing surfaces with no parallel storage path.

#### Acceptance Criteria

1. WHEN Card_Ingestion produces a verified candidate, THE Card_Ingestion SHALL persist it through the existing Routing facade (`RoutingDiscoveryStore`).
2. THE Card_Ingestion SHALL persist an agentic candidate through the agentic store and SHALL NOT write it to `apis_without_agents`.
3. WHEN the same card URL is submitted more than once, THE Card_Ingestion SHALL merge with the existing candidate using the existing dedupe/merge path rather than creating a duplicate.
4. THE Card_Ingestion SHALL record the ingestion attempt in the existing discovery run audit log (`DiscoveryRunLogger`).
5. THE Card_Ingestion SHALL NOT introduce a parallel candidate store, verification store, or run log.

### Requirement 6: Idempotency, safety, and failure isolation

**User Story:** As an operator, I want submission to be safe and bounded, so that a malicious or unreachable URL cannot hang, leak, or corrupt the index.

#### Acceptance Criteria

1. THE Card_Ingestion SHALL enforce a fetch timeout on card resolution.
2. IF a submitted URL is unreachable or times out, THEN THE Card_Ingestion SHALL return a structured error and SHALL NOT raise an unhandled exception.
3. THE Card_Ingestion SHALL only fetch the submitted URL (and its conventional well-known path) and SHALL NOT follow arbitrary off-domain redirects to fetch unrelated resources.
4. THE Card_Ingestion SHALL bound the size of a fetched card response.
5. THE Card_Ingestion SHALL NOT persist any credential value supplied during submission to any store, log, or run-log entry.

### Requirement 7: A2A quality attestation — blocked on invocation (tracked, not built)

**User Story:** As the platform owner, I want A2A quality attestation explicitly tracked as blocked on invocation, so that the gap is visible and no surface implies we can score an A2A agent's quality today.

#### Acceptance Criteria

1. THE Card_Ingestion SHALL NOT perform Quality_Attestation for an A2A agent.
2. WHEN a user requests quality attestation for an ingested A2A agent, THE Card_Ingestion SHALL return a structured blocked result whose reason states that A2A invocation is not yet implemented.
3. THE Card_Ingestion SHALL record the blocked reason in a form the existing credibility surface treats as non-real, so the agent never appears as quality-scored.
4. WHERE the A2A invocation surface becomes available in the future, THE Card_Ingestion SHALL route quality attestation through the existing Eval_Framework rather than a parallel mechanism.
5. THE Card_Ingestion SHALL document the blocked-on-invocation status in the project's tracking documents so the limitation is discoverable outside the code.

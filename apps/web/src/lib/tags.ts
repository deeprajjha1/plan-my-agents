/**
 * Helpers for honest status pills used across the UI.
 *
 * Each candidate carries a verification status, a benchmark status, and a
 * routability flag. These helpers map the API enum values to consistent visual
 * tags so the colour story is the same on every page.
 */

/**
 * Maps the candidate's `verification_status` to a short user-facing
 * label. Avoid the word "verified" unless PlanMyAgents actually ran a
 * capability check; otherwise users reasonably assume we executed it.
 *
 * - `capability_verified` — we ran the capability and it returned a
 *   real result. Strongest signal.
 * - `known_provider` — known provider identity or docs signal; not a
 *   capability test.
 * - `community_listed` — discovered on a community network (e.g.
 *   Moltbook) AND cross-referenced against an external artifact
 *   (GitHub repo / npm package / homepage). The platform itself does
 *   no capability validation; the artifact link is what makes this
 *   marginally trustworthy. See ``apps/api/planmyagents_api/discovery/sources/moltbook.py``
 *   for the source-side rationale.
 * - `unverified_example` — illustrative content only, not a real
 *   provider claim.
 * - default / `unverified` — no signal at all; treat as raw lead.
 */
export const verificationStatusLabel = (status: string): string => {
  switch (status) {
    case "capability_verified":
      return "Tested by PlanMyAgents";
    case "registered_in_directory":
      return "Registry-listed";
    case "known_provider":
      return "Known provider";
    case "community_listed":
      return "Community-listed";
    case "unverified_example":
      return "Example only";
    default:
      return "Raw lead";
  }
};

export const verificationStatusTagClass = (status: string): string => {
  switch (status) {
    case "capability_verified":
      return "tag-success";
    case "registered_in_directory":
      return "tag-info";
    case "known_provider":
      return "tag-info";
    case "community_listed":
      return "tag-warn";
    case "unverified_example":
      return "tag-warn";
    default:
      return "tag-neutral";
  }
};

/**
 * Tooltip body for the verification-status pill.
 *
 * Reviewers want to know what a status DOES and DOES NOT prove
 * before clicking through. A short pill ("Self-listed (community)")
 * is the right top-level UI but the nuance — "the platform did no
 * capability validation; the artifact link is what makes this
 * marginally trustworthy" — needs a place to live too. This is
 * that place. Wire the return value into a `title` attribute or a
 * proper tooltip component on every status pill render site so
 * users can hover to find out.
 *
 * Returned strings are kept under ~280 chars so they fit in a
 * native browser title tooltip without truncation on most desktops.
 */
export const verificationStatusTooltip = (status: string): string => {
  switch (status) {
    case "capability_verified":
      return (
        "PlanMyAgents invoked this capability against the provider and got " +
        "back a real result. Strongest signal we surface."
      );
    case "known_provider":
      return (
        "Known provider identity or docs signal. This does not mean " +
        "PlanMyAgents called the API, benchmarked the capability, or can run " +
        "it today."
      );
    case "registered_in_directory":
      return (
        "Listed in a vendor-curated directory (e.g. official MCP registry). " +
        "Directory maintainers reviewed metadata; PlanMyAgents did not test " +
        "the capability."
      );
    case "community_listed":
      return (
        "Discovered on a community network (e.g. Moltbook) and " +
        "cross-referenced against an external artifact (GitHub repo / npm " +
        "package / homepage). Neither the platform nor we have run any " +
        "executable check — treat as a lead, not a proof."
      );
    case "unverified_example":
      return (
        "Illustrative content only. Not a real provider claim — used in " +
        "demos and documentation."
      );
    default:
      return (
        "No verification signal. Treat as a raw lead pending review."
      );
  }
};

export const benchmarkStatusLabel = (status: string): string => {
  switch (status) {
    case "passed":
      return "tested: passed";
    case "failed":
      return "tested: failed";
    default:
      return "not tested";
  }
};

export const benchmarkStatusTagClass = (status: string): string => {
  switch (status) {
    case "passed":
      return "tag-success";
    case "failed":
      return "tag-danger";
    default:
      return "tag-neutral";
  }
};

export const routableTagLabel = (willFail: boolean): string =>
  willFail ? "Cannot run yet" : "Runnable today";

export const routableTagClass = (willFail: boolean): string =>
  willFail ? "tag-warn" : "tag-success";

export const hasProviderEvidence = (status: string | undefined): boolean =>
  Boolean(
    status &&
      !["unverified", "unverified_example"].includes(status),
  );

export const credibilityLabel = (status: string): string => {
  switch (status) {
    case "publishable":
      return "Publishable";
    case "developing":
      return "Developing";
    case "smoke_test":
      return "Smoke test";
    case "synthetic_only":
      return "Synthetic only";
    default:
      return "Unknown";
  }
};

export const credibilityTagClass = (status: string): string => {
  switch (status) {
    case "publishable":
      return "tag-success";
    case "developing":
      return "tag-info";
    case "smoke_test":
      return "tag-warn";
    case "synthetic_only":
      return "tag-danger";
    default:
      return "tag-neutral";
  }
};

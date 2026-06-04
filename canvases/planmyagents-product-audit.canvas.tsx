import {
  Callout,
  Code,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

const resolved = [
  [
    "Trust-tier naming",
    <Text key="r1" size="small">
      <Code>provider_verified</Code> is no longer canonical. The repo now uses
      <Code>known_provider</Code> and migrations rewrite old rows.
    </Text>,
  ],
  [
    "Misleading outcome card",
    "Goal results now use recipe coverage so gap-only plans are shown as outline-only instead of executable.",
  ],
  [
    "Discovery recall bug",
    "Request-time scouts now include local curated sources, not only remote registries and noisy long-tail sources.",
  ],
  [
    "User-facing labels",
    "Known/listed, tested, benchmarked, and runnable are now separate labels instead of one vague verified bucket.",
  ],
];

const openRisks = [
  [
    "High",
    "Generic discovered-provider execution",
    <Text key="o1" size="small">
      <Code>GenericProtocolAdapter</Code> still needs real MCP/A2A/OpenAPI
      invocation before discovered providers can be executed safely.
    </Text>,
    "Do not route fresh candidates directly; keep fail-closed behavior.",
  ],
  [
    "High",
    "Benchmark credibility loop",
    "Benchmarks and gates exist, but production needs repeatable sandbox runs, persisted results, and promotion policy around failures.",
    "Routability must remain tied to benchmark and adapter evidence.",
  ],
  [
    "Medium",
    "Scenario regression coverage",
    "The Conveo-style irrelevant shortlist failure should become a regression suite across gift, hiring, GTM, travel, finance, and impossible-goal prompts.",
    "Prevents demo-driven prompt patches from replacing product quality.",
  ],
  [
    "Medium",
    "Hosted operations",
    "The app now needs AWS/RDS migrations, recurring upkeep, logs, health checks, and secrets management before public launch.",
    "Without this, discovery freshness and trust tiers drift in production.",
  ],
];

const nextOrder = [
  ["1", "Production deploy base", "RDS + pgvector, migration step, API/web systemd or container services, CloudFront/Route 53."],
  ["2", "Quality regression suite", "Golden prompts for relevance, honest refusal, candidate assignment, and recipe exportability."],
  ["3", "Benchmark promotion loop", "Run sandbox benchmarks, persist results, and only promote after evidence."],
  ["4", "Generic protocol execution", "Implement MCP tools/call, A2A invocation, OpenAPI request building, and normalized provider errors."],
];

export default function PlanMyAgentsProductAudit() {
  return (
    <Stack gap={20}>
      <H1>PlanMyAgents Product Audit</H1>
      <Text tone="secondary">
        Current product-quality read after the discovery, label, migration, and
        browser-validation fixes. This canvas is intentionally updated from the
        earlier audit rather than preserving stale findings as current truth.
      </Text>

      <Grid columns={4} gap={12}>
        <Stat value="4" label="Major issues resolved" tone="success" />
        <Stat value="4" label="Open risk areas" tone="warning" />
        <Stat value="1" label="Public hosting guide" tone="info" />
        <Stat value="0" label="Claims to overstate" tone="success" />
      </Grid>

      <Callout tone="warning" title="Executive Read">
        The product direction is credible only if PlanMyAgents keeps separating
        discovery from execution. Discovery can be broad; recommendations and
        exports must stay honest unless a provider is tested, configured, and
        runnable.
      </Callout>

      <Divider />

      <H2>Resolved Since The Earlier Audit</H2>
      <Table
        headers={["Area", "Current state"]}
        rows={resolved}
        rowTone={["success", "success", "success", "success"]}
      />

      <Divider />

      <H2>Open Product Risks</H2>
      <Table
        headers={["Severity", "Risk", "Evidence", "Product impact"]}
        rows={openRisks}
        rowTone={["danger", "danger", "warning", "warning"]}
      />

      <Divider />

      <H2>Recommended Fix Order</H2>
      <Table
        headers={["Order", "Workstream", "Expected outcome"]}
        rows={nextOrder}
        rowTone={["info", "warning", "warning", "danger"]}
      />

      <Divider />

      <H3>Product Rule</H3>
      <Text>
        A provider may be discovered or known without being recommended. It may
        be recommended only when the recipe can be exported honestly, and it may
        be executed only when adapter, credentials, benchmark, and cost gates all
        pass.
      </Text>
      <Pill tone="success">Honesty is the core product feature</Pill>
    </Stack>
  );
}

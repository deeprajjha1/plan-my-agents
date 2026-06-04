import {
  Callout,
  Card,
  CardBody,
  CardHeader,
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

const pillars = [
  [
    <Text key="p1" weight="semibold">Federated parallel scouts</Text>,
    <Pill key="s1" tone="success" active>Live</Pill>,
    <Text key="w1" size="small">
      <Code>discovery/scouts.py</Code> dispatches curated sources, MCP registries,
      Smithery, APIs.guru, GitHub, HN, RSS, and optional social/web search with
      per-scout budgets.
    </Text>,
    <Text key="m1" size="small" tone="secondary">
      Keep adding sources declaratively; do not route raw candidates directly.
    </Text>,
  ],
  [
    <Text key="p2" weight="semibold">LLM query expansion and candidate judging</Text>,
    <Pill key="s2" tone="success" active>Live</Pill>,
    <Text key="w2" size="small">
      <Code>query_expansion.py</Code> and <Code>candidate_judge.py</Code> rewrite
      each search per source and reject noisy matches against the user's goal.
    </Text>,
    <Text key="m2" size="small" tone="secondary">
      Continue measuring false positives with scenario tests.
    </Text>,
  ],
  [
    <Text key="p3" weight="semibold">Postgres + pgvector discovery store</Text>,
    <Pill key="s3" tone="success" active>Live</Pill>,
    <Text key="w3" size="small">
      <Code>store.py</Code> persists discovery candidates, embeddings, benchmark
      status, freshness, and verification status in Postgres.
    </Text>,
    <Text key="m3" size="small" tone="secondary">
      Production hosting should use RDS/Postgres with <Code>vector</Code> enabled.
    </Text>,
  ],
  [
    <Text key="p4" weight="semibold">Trust-tier honesty</Text>,
    <Pill key="s4" tone="success" active>Fixed</Pill>,
    <Text key="w4" size="small">
      The old <Code>provider_verified</Code> tier has been renamed to
      <Code>known_provider</Code>, with a one-time migration in
      <Code>scripts/apply_migrations.py</Code>.
    </Text>,
    <Text key="m4" size="small" tone="secondary">
      UI copy now distinguishes known/listed providers from PlanMyAgents-tested
      providers.
    </Text>,
  ],
  [
    <Text key="p5" weight="semibold">Tool-level enrichment</Text>,
    <Pill key="s5" tone="warning" active>Partly wired</Pill>,
    <Text key="w5" size="small">
      MCP tool probing and OpenAPI/docs enrichment exist and can feed richer
      matching.
    </Text>,
    <Text key="m5" size="small">
      Keep pushing tool names, tool descriptions, schemas, auth shape, and
      endpoint evidence into ranking features.
    </Text>,
  ],
  [
    <Text key="p6" weight="semibold">Pre-plan discovery</Text>,
    <Pill key="s6" tone="warning" active>Needs hardening</Pill>,
    <Text key="w6" size="small">
      The goal route can discover, judge, explain gaps, and refresh candidates,
      but freshness and first-pass planner vocabulary still need product-level
      regression coverage.
    </Text>,
    <Text key="m6" size="small">
      The invariant: discovery improves recall, while execution stays fail-closed
      until a candidate is tested and runnable.
    </Text>,
  ],
  [
    <Text key="p7" weight="semibold">Generic execution for discovered providers</Text>,
    <Pill key="s7" tone="danger" active>Real gap</Pill>,
    <Text key="w7" size="small">
      <Code>GenericProtocolAdapter</Code> exists, but generic MCP/A2A/OpenAPI
      execution remains the largest true engineering gap.
    </Text>,
    <Text key="m7" size="small">
      Build protocol clients only after ranking and benchmark gates are good
      enough to avoid confidently calling the wrong tool.
    </Text>,
  ],
];

export default function HonestScopeExistingVsNew() {
  return (
    <Stack gap={20}>
      <H1>Honest Scope: Existing Vs New</H1>
      <Text tone="secondary">
        Current view of what PlanMyAgents already has versus what genuinely needs
        building next. This is a repo-owned version of the planning canvas.
      </Text>

      <Grid columns={4} gap={12}>
        <Stat value="4" label="Pillars live or fixed" tone="success" />
        <Stat value="2" label="Partly wired" tone="warning" />
        <Stat value="1" label="True new build" tone="danger" />
        <Stat value="1" label="Core invariant" tone="info" />
      </Grid>

      <Callout tone="success" title="Headline">
        The codebase is not missing the whole discovery layer. The reliable path
        is to extend the existing discovery, verification, benchmark, and refusal
        machinery, while treating generic execution as the one major new module.
      </Callout>

      <Divider />

      <H2>Seven Pillars</H2>
      <Table
        headers={["Pillar", "Status", "Where it lives", "What remains"]}
        rows={pillars}
        rowTone={[
          "success",
          "success",
          "success",
          "success",
          "warning",
          "warning",
          "danger",
        ]}
      />

      <Divider />

      <Grid columns="1fr 1fr" gap={16}>
        <Stack gap={10}>
          <H2>Extend</H2>
          <Card>
            <CardHeader>Discovery Quality</CardHeader>
            <CardBody>
              <Text size="small">
                Reuse scouts, query expansion, candidate judging, local curated
                sources, freshness, and embeddings. Add scenario tests around
                irrelevant shortlist failures.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>Trust And Labels</CardHeader>
            <CardBody>
              <Text size="small">
                Keep the <Code>known_provider</Code> model and promote only to
                tested/runnable states after benchmark and adapter evidence.
              </Text>
            </CardBody>
          </Card>
        </Stack>

        <Stack gap={10}>
          <H2>Build</H2>
          <Card>
            <CardHeader>Generic Protocol Execution</CardHeader>
            <CardBody>
              <Text size="small">
                MCP <Code>tools/call</Code>, A2A invocation, OpenAPI request
                generation, normalized provider errors, and sandboxed execution
                are still real product work.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>Production Ops</CardHeader>
            <CardBody>
              <Text size="small">
                AWS hosting needs RDS/Postgres, migrations on deploy, recurring
                upkeep, and CloudWatch alarms before a public domain is attached.
              </Text>
            </CardBody>
          </Card>
        </Stack>
      </Grid>

      <Divider />
      <H3>Decision</H3>
      <Text>
        Keep the architecture. Tighten relevance, verification, and benchmark
        evidence. Do not claim execution until a provider is tested, configured,
        and runnable.
      </Text>
    </Stack>
  );
}

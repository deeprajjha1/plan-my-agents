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
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

const keep = [
  ["Discovery index", "Postgres-backed candidates, embeddings, freshness, verification status, and benchmark fields are the right shared asset."],
  ["Fail-closed routing", "The router should continue refusing when credentials, benchmarks, cost caps, or adapter support are missing."],
  ["Recipe coverage", "Exportable, partial, and gap-only plans give users truthful outcomes instead of false executable plans."],
  ["Local curated sources", "Curated manifests are useful seed data and should remain part of request-time discovery."],
];

const change = [
  [
    "Ranking features",
    "Move beyond capability overlap into intent class, source trust, endpoint shape, evidence quality, benchmark score, freshness, and cost.",
  ],
  [
    "Promotion lifecycle",
    "Make candidate states explicit: discovered, judged relevant, known/listed, benchmarked, routable, exportable, and gap.",
  ],
  [
    "Operational boundary",
    "Separate public web/API uptime from upkeep jobs; recurring discovery and benchmarking should not depend on user requests.",
  ],
  [
    "Docs as product truth",
    "Architecture docs and hosting docs must not claim features that are only designed or gated.",
  ],
];

const roadmap = [
  ["Now", "AWS v0 deploy", "EC2 + RDS + CloudFront + Route 53, migrations on deploy, hourly upkeep."],
  ["Next", "Relevance regressions", "Golden prompt suite for noisy candidates, impossible goals, and provider assignment."],
  ["Next", "Benchmark promotion", "Persist sandbox results and promote only after measurable evidence."],
  ["Later", "Generic execution", "MCP/A2A/OpenAPI runtime clients behind benchmark and cost gates."],
  ["Later", "Vendor portal", "Separate deployment and schema once marketplace features are live."],
];

export default function ProductArchitectureAudit() {
  return (
    <Stack gap={22}>
      <Stack gap={8}>
        <H1>Product Architecture Audit</H1>
        <Text tone="secondary">
          Current architecture judgment for PlanMyAgents: keep the discovery
          index and honesty gates, harden promotion and benchmarks, then build
          generic execution carefully.
        </Text>
        <Row gap={8} wrap>
          <Pill tone="success">Keep the index</Pill>
          <Pill tone="warning">Harden ranking</Pill>
          <Pill tone="danger">Do not over-claim execution</Pill>
        </Row>
      </Stack>

      <Grid columns={4} gap={12}>
        <Stat value="1" label="Shared moat" tone="success" />
        <Stat value="4" label="Keep decisions" tone="success" />
        <Stat value="4" label="Change areas" tone="warning" />
        <Stat value="5" label="Roadmap steps" tone="info" />
      </Grid>

      <Callout tone="info" title="Architecture Verdict">
        PlanMyAgents should become the trusted index, evaluator, and router for
        callable agent providers. The architecture should optimize for truthful
        recommendation quality before broad automated execution.
      </Callout>

      <Grid columns="1fr 1fr" gap={16}>
        <Stack gap={10}>
          <H2>Keep</H2>
          <Table
            headers={["Area", "Why"]}
            rows={keep}
            rowTone={["success", "success", "success", "success"]}
          />
        </Stack>

        <Stack gap={10}>
          <H2>Change</H2>
          <Table
            headers={["Area", "Required shift"]}
            rows={change}
            rowTone={["warning", "warning", "warning", "info"]}
          />
        </Stack>
      </Grid>

      <Divider />

      <H2>Execution Roadmap</H2>
      <Table
        headers={["Stage", "Work", "Outcome"]}
        rows={roadmap}
        rowTone={["info", "warning", "warning", "danger", undefined]}
      />

      <Grid columns="1.15fr 0.85fr" gap={16}>
        <Stack gap={10}>
          <H2>Do Not Build Yet</H2>
          <Card>
            <CardHeader>Mass Per-Agent Wrappers</CardHeader>
            <CardBody>
              <Text size="small">
                Writing one adapter per public provider does not scale. Use
                registry wrappers only for proof workflows and benchmark anchors.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>Raw Candidate Execution</CardHeader>
            <CardBody>
              <Text size="small">
                A newly discovered provider must never become runnable just
                because it looks relevant. Keep <Code>known_provider</Code>,
                benchmark, credential, and adapter gates separate.
              </Text>
            </CardBody>
          </Card>
        </Stack>

        <Stack gap={10}>
          <H2>Next Proof</H2>
          <Card>
            <CardHeader>GTM Workflow</CardHeader>
            <CardBody>
              <H3>Company research to contact enrichment</H3>
              <Text size="small">
                This remains the strongest beachhead because quality, freshness,
                source links, cost, and provider variance are measurable.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>Hosting Proof</CardHeader>
            <CardBody>
              <Text size="small">
                Put the current app behind <Code>www.planmyagents.com</Code> with
                RDS migrations and a real upkeep loop before expanding surface
                area.
              </Text>
            </CardBody>
          </Card>
        </Stack>
      </Grid>
    </Stack>
  );
}

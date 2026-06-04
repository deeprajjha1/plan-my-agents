/**
 * /submit-agent — explains the real agent-card ingestion pipeline.
 *
 * Honesty note (important): card ingestion is a CLI / operator pipeline
 * today (`make card-ingest CARD_URL=...`), NOT a self-serve HTTP submit
 * form. Rendering a form that POSTed nowhere would be exactly the kind
 * of "claim more than the system does" surface AGENTS.md hard rule #1
 * forbids. So this page documents the real mechanism, the trust ceiling
 * it produces, and how to run it — rather than faking a submit button.
 *
 * When a public POST /agents/ingest endpoint lands (tracked in the
 * agent-card-ingestion spec), this page gains the form. Until then it is
 * a truthful explainer with a copy-paste command.
 */

import Link from "next/link";

const STEPS: { title: string; body: string }[] = [
  {
    title: "Resolve & parse the card",
    body: "We fetch the submitted .well-known/agent.json from any domain and parse it with the existing A2A agent-card source — no per-domain scraper, no enumeration. The submitter supplies the URL, so it generalises to any host.",
  },
  {
    title: "Extract declared claims",
    body: "The descriptor reader pulls the skills/capabilities the card declares and maps them onto our capability index. Unmapped skills are recorded so the gap is visible, not dropped.",
  },
  {
    title: "Verify existence, not quality",
    body: "We verify the claim was made and the endpoint resolves — never that the claim is true. A self-asserted card is attested as existing, then indexed as a discovery candidate.",
  },
  {
    title: "Block quality on invocation",
    body: "A2A has no stable invocation surface yet, so quality attestation is blocked-on-invocation. The candidate stays benchmark status not-started and is never made routable until the Eval_Framework can run it.",
  },
];

export default function SubmitAgentPage() {
  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <p className="text-xs uppercase tracking-wide text-ink-400">
          Index · Submit an agent
        </p>
        <h1 className="text-3xl font-semibold tracking-tight text-ink-900">
          Add any published agent card
        </h1>
        <p className="max-w-3xl text-sm text-ink-600">
          Have an agent with a published{" "}
          <code>/.well-known/agent.json</code> card? It can be resolved,
          verified, and indexed regardless of the domain it lives on.
          Here&apos;s exactly what happens to it &mdash; and what we can and
          cannot attest.
        </p>
      </header>

      <section className="surface border-accent-200 bg-accent-50 p-5">
        <h2 className="text-sm font-semibold text-ink-900">
          Today this runs as an operator command
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-700">
          Card ingestion is a reviewed CLI pipeline, not a self-serve form
          yet. Run it against any card URL:
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-ink-900 px-4 py-3 text-xs text-ink-50">
          make card-ingest CARD_URL=https://your-domain.example/.well-known/agent.json
        </pre>
        <p className="mt-3 text-xs text-ink-500">
          A public submission endpoint is tracked in the{" "}
          <code>agent-card-ingestion</code> spec. We&apos;d rather ship the
          honest command than a button that quietly does nothing.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-ink-900">
          What happens to a submitted card
        </h2>
        <ol className="space-y-3">
          {STEPS.map((step, idx) => (
            <li key={step.title} className="surface flex gap-4 p-5">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink-900 text-sm font-semibold text-white">
                {idx + 1}
              </div>
              <div>
                <h3 className="text-base font-semibold text-ink-900">
                  {step.title}
                </h3>
                <p className="mt-1 text-sm text-ink-600">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="surface-muted space-y-2 p-5 text-sm text-ink-600">
        <h2 className="text-sm font-semibold text-ink-900">The trust ceiling</h2>
        <p>
          Indexing a card proves it <em>exists</em> and <em>claims</em> a
          capability. It does not prove the capability <em>works</em>. To see
          how a claim becomes a benchmarked, publishable number, read{" "}
          <Link href="/trust" className="text-accent-600 hover:underline">
            how we evaluate agents
          </Link>
          . Until A2A invocation ships, A2A agents stop at verification-only.
        </p>
      </section>
    </div>
  );
}

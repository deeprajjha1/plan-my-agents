/**
 * Sprint-6 / A — public design-partner + vendor landing page.
 *
 * The destination link for outreach emails — anchored to the
 * "Founder + ask" slide of PITCH_DECK.md so the deck and the website
 * stay in lock-step. Three sections map directly to the three asks on
 * that slide:
 *
 *   1. Design partners — for devs / operators on Claude Desktop,
 *      Cursor, or n8n who want a refusal-with-reasons workflow
 *      backed by recipe exports.
 *   2. Vendors — for product / RevOps at the ten capability vendors
 *      named in the deck. Each has a one-sentence reason +
 *      capability-specific evidence link.
 *   3. Workflow / LLM hosts — for BD at n8n, Cursor, Anthropic.
 *
 * The page does NOT collect emails or run JS. The "contact" CTA is a
 * plain mailto so anyone replying lands directly in the founder
 * inbox without any third-party form-handler / CRM dependency.
 */

import Link from "next/link";

const FOUNDER_EMAIL = "jha.deepraj@gmail.com";

type VendorCard = {
  name: string;
  capability: string;
  reason: string;
  /** Capability-id from agents.json or a routable evidence URL on the site. */
  evidenceLink: string;
  /** Optional second link to a public demand or gap row. */
  evidenceLabel?: string;
};

// Anchored to PITCH_DECK.md "Founder + ask" — keep this list in sync
// with that slide. If the deck adds a vendor, add it here in the same
// PR so the website and the deck stay aligned.
const VENDOR_TARGETS: VendorCard[] = [
  {
    name: "Apollo",
    capability: "contact_enrichment",
    reason:
      "Apollo's B2B identity graph is the cheapest way to enrich a refusal's missing 'find this person's email' sub-step.",
    evidenceLink: "/categories",
    evidenceLabel: "Discovery categories",
  },
  {
    name: "Hunter",
    capability: "email_verification",
    reason:
      "Already in the static registry at capability_verified. We want to upgrade the cell from synthetic-baseline to a 5/5 live-run; the per-agent page lands on /agents/hunter once we seed the live run.",
    // /agents/hunter is gated behind a live discovery_candidates row
    // which only lands once we have a real sandbox key from Hunter.
    // Until then, /categories surfaces the email_verification
    // taxonomy + the static-registry entry that names Hunter, so the
    // link still resolves and is honest about state.
    evidenceLink: "/categories",
    evidenceLabel: "Discovery categories (email_verification)",
  },
  {
    name: "Perplexity",
    capability: "web_search",
    reason:
      "The /demand top-list almost always has web_search in the top 3. A Perplexity-grade search cell would route a huge slice of refusals.",
    evidenceLink: "/demand",
    evidenceLabel: "Public demand-signal",
  },
  {
    name: "Linkup",
    capability: "web_search",
    reason:
      "Same web_search slot — Linkup's deterministic citation contract is easier to grade than the open-web alternatives.",
    evidenceLink: "/demand",
    evidenceLabel: "Public demand-signal",
  },
  {
    name: "Tavily",
    capability: "web_search",
    reason:
      "Two of the three web-search baselines on our shortlist. A Tavily benchmark cell would be the second live cell after Razorpay.",
    evidenceLink: "/demand",
    evidenceLabel: "Public demand-signal",
  },
  {
    name: "Exa",
    capability: "semantic_search",
    reason:
      "Exa's embedding-based search is a different shape than keyword search and would justify its own capability cell.",
    evidenceLink: "/categories",
    evidenceLabel: "Discovery categories",
  },
  {
    name: "Apify",
    capability: "web_scraping",
    reason:
      "Already in the routable shortlist with Firecrawl. An Apify cell would give us a second-vendor comparison.",
    evidenceLink: "/agents/firecrawl",
    evidenceLabel: "Firecrawl peer agent",
  },
  {
    name: "Firecrawl",
    capability: "web_scraping",
    reason:
      "Shipped as a response-fixture cell today; the partnership is about flipping it to a true live run on the cron.",
    evidenceLink: "/agents/firecrawl",
    evidenceLabel: "/agents/firecrawl",
  },
  {
    name: "Browserbase",
    capability: "web_scraping",
    reason:
      "For the subset of web_scraping that needs full headless-browser fidelity (login walls, JS-heavy pages). Distinct enough to be its own cell.",
    evidenceLink: "/demand",
    evidenceLabel: "Public demand-signal",
  },
  {
    name: "Resend",
    capability: "email_send",
    reason:
      "Shipped as a response-fixture cell today; partnership is about flipping the wrapper from fixture to live API calls on the cron.",
    evidenceLink: "/agents/resend-emails",
    evidenceLabel: "/agents/resend-emails",
  },
];

type PartnerCard = {
  name: string;
  role: string;
  reason: string;
  evidenceLink: string;
};

const HOST_PARTNERS: PartnerCard[] = [
  {
    name: "n8n",
    role: "Workflow host",
    reason:
      "Our recipe export already emits n8n-importable JSON. n8n marketplace placement turns every n8n user into a potential PlanMyAgents user.",
    evidenceLink: "/goal",
  },
  {
    name: "Cursor",
    role: "Coding host",
    reason:
      "Cursor IDE users live in MCP. A PlanMyAgents recipe → Cursor command-palette plug is a 10-line integration with deep distribution upside.",
    evidenceLink: "/goal",
  },
  {
    name: "Anthropic",
    role: "LLM host",
    reason:
      "Claude Desktop already shows MCP servers. A reranking + benchmark layer that says 'use these in this order' is exactly the unsolved trust gap on top.",
    evidenceLink: "/goal",
  },
];

export default function PartnersPage() {
  return (
    <div
      className="space-y-10"
      data-testid="partners-page"
      data-state="ok"
    >
      <section className="surface-muted p-6">
        <h1 className="text-2xl font-semibold text-ink-900">
          Build PlanMyAgents with us
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-ink-700">
          PlanMyAgents is the trust + ranking + benchmark layer for the
          AI-agent supply across MCP, A2A, and AI-native services. We do
          not execute customer workflows or hold customer credentials —
          we surface a refusal with reasons, hand the user an
          exportable recipe for their own host, and continuously score
          discovered providers against hand-authored benchmark cells.
        </p>
        <p className="mt-3 max-w-3xl text-sm text-ink-700">
          We are building the prototype solo today and looking for three
          kinds of help. Every &quot;ask&quot; on this page is anchored
          to a live URL on this site — nothing here is hypothetical.
        </p>
      </section>

      <section data-testid="partners-design-partners">
        <h2 className="text-lg font-semibold text-ink-900">
          1 · Design partners
        </h2>
        <p className="mt-2 max-w-3xl text-sm text-ink-600">
          Developers or operators (humans, not agents) who already use
          Claude Desktop, Cursor, or n8n with MCP and want better
          discovery, recipes, and benchmarks. Concretely:
        </p>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-ink-700">
          <li>
            Stress-test the planner →{" "}
            <Link href="/goal" className="text-accent-600 hover:underline">
              /goal
            </Link>
            . Any goal works. The output is either a routed answer or a
            refusal-with-reasons. Every refusal lands in the public{" "}
            <a
              href="/demand"
              className="text-accent-600 hover:underline"
            >
              demand signal
            </a>{" "}
            within minutes.
          </li>
          <li>
            Run the recipe export against your usual host (n8n / Cursor
            / Claude Desktop / CLI). Tell us where it breaks.
          </li>
          <li>
            Join a 30-min onboarding call. We walk you through the
            checklist in{" "}
            <code>docs/design-partner-onboarding.md</code> and you walk
            away with one routed plan and one refusal-with-reasons in
            your own workspace.
          </li>
        </ul>
        <p className="mt-3 text-sm">
          <a
            href={`mailto:${FOUNDER_EMAIL}?subject=PlanMyAgents%20design%20partner`}
            className="text-accent-600 hover:underline"
          >
            Email the founder
          </a>{" "}
          with the goal you would test PlanMyAgents on first.
        </p>
      </section>

      <section data-testid="partners-vendors">
        <h2 className="text-lg font-semibold text-ink-900">
          2 · Vendor introductions
        </h2>
        <p className="mt-2 max-w-3xl text-sm text-ink-600">
          Each row maps to a vendor named in our pitch deck plus the
          specific capability we want to wrap and the evidence URL on
          this site that anchors the ask.
        </p>
        <div className="mt-3 overflow-x-auto rounded-md border border-ink-200">
          <table className="min-w-full divide-y divide-ink-200 text-sm">
            <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
              <tr>
                <th className="px-3 py-2 text-left">Vendor</th>
                <th className="px-3 py-2 text-left">Capability</th>
                <th className="px-3 py-2 text-left">Why this vendor</th>
                <th className="px-3 py-2 text-left">Evidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {VENDOR_TARGETS.map((vendor) => (
                <tr
                  key={vendor.name}
                  className="bg-white"
                  data-testid={`vendor-row-${vendor.name.toLowerCase()}`}
                >
                  <td className="px-3 py-2 align-top font-medium text-ink-900">
                    {vendor.name}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <code className="text-xs text-ink-700">
                      {vendor.capability}
                    </code>
                  </td>
                  <td className="px-3 py-2 align-top text-sm text-ink-700">
                    {vendor.reason}
                  </td>
                  <td className="px-3 py-2 align-top text-xs">
                    <a
                      href={vendor.evidenceLink}
                      className="text-accent-600 hover:underline"
                    >
                      {vendor.evidenceLabel ?? vendor.evidenceLink}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-sm">
          <a
            href={`mailto:${FOUNDER_EMAIL}?subject=PlanMyAgents%20vendor%20introduction`}
            className="text-accent-600 hover:underline"
          >
            Email the founder
          </a>{" "}
          if you can connect to product / RevOps at any of the vendors
          above.
        </p>
      </section>

      <section data-testid="partners-hosts">
        <h2 className="text-lg font-semibold text-ink-900">
          3 · Workflow + LLM host BD
        </h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {HOST_PARTNERS.map((partner) => (
            <div
              key={partner.name}
              className="surface p-4"
              data-testid={`host-card-${partner.name.toLowerCase()}`}
            >
              <h3 className="text-sm font-semibold text-ink-900">
                {partner.name}{" "}
                <span className="text-ink-400">· {partner.role}</span>
              </h3>
              <p className="mt-2 text-sm text-ink-700">{partner.reason}</p>
              <a
                href={partner.evidenceLink}
                className="mt-3 inline-block text-xs text-accent-600 hover:underline"
              >
                Live evidence ↗
              </a>
            </div>
          ))}
        </div>
      </section>

      <section className="surface-muted p-5 text-xs text-ink-600">
        <h2 className="text-sm font-semibold text-ink-900">
          What we will NOT ask you for
        </h2>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            Your API keys. PlanMyAgents does not execute customer
            workflows — we recommend providers and hand you an
            exportable recipe to run on your own host with your own
            credentials.
          </li>
          <li>
            An NDA before the first conversation. The whole pitch is
            already on the public deck and on this site.
          </li>
          <li>
            A logo placement before there is a real partnership. We
            value our vendor-neutral positioning more than a vanity
            partner list.
          </li>
        </ul>
      </section>
    </div>
  );
}

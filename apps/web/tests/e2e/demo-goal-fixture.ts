/**
 * Edit these two exports to change what appears in the goal demo video.
 *
 * `DEMO_GOAL` — typed into the homepage goal box on camera.
 * `DEMO_GOAL_RESPONSE` — returned instantly by the mocked POST /goal
 *   (no planning wait). Must stay consistent with DEMO_GOAL.
 */

export const DEMO_GOAL =
  "Verify the email jane.doe@acme.com and send a welcome message";

/** Canned POST /goal body — partial plan with one routable email step. */
export const DEMO_GOAL_RESPONSE = {
  ok: true,
  executed: false,
  plan: {
    status: "executable",
    sub_tasks: [
      {
        capability: "email_verification",
        description: "Verify jane.doe@acme.com is valid and deliverable",
        inputs: {
          user_facing_step: "Verify email address",
          search_query: "email verification API",
          acceptance_criteria: "returns valid + deliverable status",
        },
      },
      {
        capability: "email_send",
        description: "Send a welcome email after verification passes",
        inputs: {
          user_facing_step: "Send welcome email",
          search_query: "transactional email",
          acceptance_criteria: "delivered to verified inbox",
        },
      },
    ],
    recipe_coverage: {
      step_count: 2,
      exportable_step_count: 1,
      gap_count: 1,
      status: "partial",
    },
    discovery: {
      candidates: [
        {
          provider_id: "hunter-email-verifier",
          display_name: "Hunter Email Verifier",
          capability: "email_verification",
          provider_type: "ai_agent",
          credibility_status: "publishable",
        },
      ],
      missing_capabilities: ["email_send"],
      decomposed_sub_tasks: [
        {
          capability: "email_verification",
          description: "Verify jane.doe@acme.com",
          inputs: { user_facing_step: "Verify email address" },
        },
        {
          capability: "email_send",
          description: "Send welcome email",
          inputs: { user_facing_step: "Send welcome email" },
        },
      ],
    },
  },
} as const;

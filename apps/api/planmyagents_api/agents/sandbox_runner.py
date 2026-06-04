"""BYO-credentials sandbox runner.

Sprint 4 T0-4 seed module. The full runtime contract is built out in
T2-4 (sandbox UI integration) and finalised when ``/sandbox/execute``
ships.

Why this lives next to `agents/protocol.py` and not under `workflows/`
---------------------------------------------------------------------
The legacy :class:`planmyagents_api.workflows.executor.WorkflowExecutor`
expects credentials to live in environment variables and routes
through :class:`planmyagents_api.agents.router.ProviderRouter`. Under
the 16-May-2026 product spec credentials NEVER live in our process
environment for customer execution — the user pastes them into the
browser sandbox UI and they flow into a single request, never get
persisted, and never go through the provider router (which is for
recommendation / planning only after the pivot).

Contract (provisional, locks in T2)
-----------------------------------
* Accept exactly one recipe step + a credential dict scoped to that
  step's provider (e.g. ``{"OPENAI_API_KEY": "sk-..."}``).
* Build a :class:`planmyagents_api.benchmark.models.ProviderRequest`,
  hand it directly to the generic-protocol adapter family
  (:mod:`planmyagents_api.agents.protocol`) — bypassing the registry
  router. The router exists for *recommendation*; sandbox execution
  is *direct, one-shot, user-driven*.
* Enforce the cost cap from
  :mod:`planmyagents_api.cost.cost_cap`, scoped to the calling
  Clerk user id (defence-in-depth — user can spam the sandbox).
* Return a structured result (``status``, ``output``, ``cost_usd``,
  ``latency_ms``, ``raw_response``, ``refusal_reason``) shaped like
  ``ProviderResponse`` so the UI does not have to learn a second
  shape.
* NEVER write credentials anywhere: not to logs, not to ledger, not
  to the request idempotency key, not to error messages. The cost
  cap ledger stores ``provider_id`` and ``capability``, never the
  credential dict itself.

Firewall invariants enforced when execute() is wired in T2-4
------------------------------------------------------------
1. Provider adapter MUST be a generic-protocol adapter — no
   ``planmyagents_api.benchmark.baselines.*`` adapter may be loaded
   from here. CI lint (T5-1) blocks any import of
   ``benchmark.baselines`` into this file.
2. No call into :class:`ProviderRouter.provider_for` — the router is
   for ranking only. Sandbox builds the adapter from the recipe step
   directly.
3. Cost cap is non-optional, no env toggle, no test-only bypass.
   Tests must use a small synthetic ``unit_cost_usd`` to avoid
   tripping the cap, not disable it.
"""

from __future__ import annotations


class SandboxNotYetWiredError(NotImplementedError):
    """Raised by the placeholder ``execute`` below.

    Removed in T2-4 when ``/sandbox/execute`` lands. The placeholder
    keeps the module importable so T1-A recipe-export tests can refer
    to its docstring as the authoritative contract while T2 work is
    in flight.
    """


def execute(*_args, **_kwargs):
    raise SandboxNotYetWiredError(
        "SandboxRunner.execute is seeded by T0-4 and wired by T2-4. "
        "Until then, sandbox calls land in /goal recommendation only."
    )

"""APIRouter modules for the PlanMyAgents FastAPI app.

History
-------
Before 2026-05-19 every endpoint lived inline inside
``planmyagents_api.web.app.create_app`` — a 3,500-line single file
with 25 endpoints, ~50 helpers, and no router separation. The audit
pre-launch flagged it as the highest-impact mechanical cleanup we
could ship without changing behaviour, so the endpoints moved into
this package one logical cluster at a time.

How the package is organised
----------------------------
* :mod:`planmyagents_api.web.routes.health` — ``/health``,
  ``/health/evidence``, ``/evidence/*``.
* :mod:`planmyagents_api.web.routes.billing` — Stripe checkout +
  webhook.
* :mod:`planmyagents_api.web.routes.account` — ``/account/*`` and
  saved-recipe CRUD.

Endpoints that have not yet migrated still live inside
``create_app``. The migration is deliberately incremental — each
router lands behind a passing ``make test`` so the OpenAPI shape
stays exactly the same.

Adding a new router
-------------------
1. Create ``routes/<name>.py`` exposing a module-level
   ``router = APIRouter(...)``.
2. Mount it from ``create_app`` via ``app.include_router(router)``.
3. Keep helper functions in ``app.py`` until two routers need them;
   if both routers need the same helper, lift it to a dedicated
   ``_helpers.py`` rather than circularly importing from ``app.py``.

Why ``APIRouter`` and not ``app.include_router(other_app)``
-----------------------------------------------------------
``APIRouter`` keeps OpenAPI generation, dependency injection, and
the middleware chain identical to inline endpoints. Mounting a
second FastAPI instance via ``mount`` would have given us a
sub-app with its own ``app.state``, its own middleware order, and
its own OpenAPI tree — none of which we want here. The single
``FastAPI`` instance + many ``APIRouter`` modules is the upstream-
recommended pattern for exactly this kind of split.
"""

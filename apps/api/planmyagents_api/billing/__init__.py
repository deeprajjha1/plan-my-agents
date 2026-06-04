"""Stripe billing integration — Pro-tier checkout + webhook handling.

T1-B-5 scope
------------

* :class:`StripeConfig`            — env-driven config + ``is_active``.
* :func:`create_checkout_session`  — POST to ``api.stripe.com`` creating
  a hosted Checkout session. Test-mode by default; refuses to call
  Stripe live unless ``STRIPE_LIVE_MODE_OPT_IN=true`` is also set.
* :func:`verify_webhook_signature` — HMAC-SHA256 verification per the
  Stripe webhook signing spec (``Stripe-Signature`` header), with
  replay-window check.
* :func:`handle_webhook_event`     — minimal event router that maps
  ``checkout.session.completed`` / ``customer.subscription.deleted`` to
  ``marketplace_store.users.plan`` updates.

Deps strategy
-------------

We deliberately do NOT pull in the heavy ``stripe`` PyPI package: the
webhook signature is a stdlib-friendly HMAC and the Checkout-session
POST is one form-encoded HTTP call. This keeps the deployment footprint
small and matches how the rest of the codebase handles third-party HTTP
(stdlib ``urllib`` everywhere, see e.g. :mod:`planmyagents_api.auth.clerk`).

Firewall invariant
------------------

Nothing in :mod:`planmyagents_api.ranking` / :mod:`planmyagents_api.benchmark`
may import from this package — the T5 CI lint will enforce it. Pro-tier
billing is an account concern, not a ranking concern.
"""

from __future__ import annotations

from planmyagents_api.billing.handlers import (
    WebhookHandlerError,
    handle_webhook_event,
)
from planmyagents_api.billing.stripe_client import (
    CheckoutSession,
    StripeApiError,
    StripeConfig,
    StripeSignatureError,
    WebhookEvent,
    create_checkout_session,
    parse_webhook_event,
    stripe_config_from_env,
    verify_webhook_signature,
)

__all__ = [
    "CheckoutSession",
    "StripeApiError",
    "StripeConfig",
    "StripeSignatureError",
    "WebhookEvent",
    "WebhookHandlerError",
    "create_checkout_session",
    "handle_webhook_event",
    "parse_webhook_event",
    "stripe_config_from_env",
    "verify_webhook_signature",
]

"""Account + Stripe billing routes.

Extracted from ``planmyagents_api.web.app`` on 2026-05-19. The
``/account/me`` probe lives here (rather than in its own module)
because it reads ``user.plan`` — a field that is exclusively
maintained by the Stripe webhook handler in the same module. Keeping
both in one file makes it easier to reason about who writes the
field and who reads it.

Endpoints
---------
* ``GET /account/me`` — read-only ``who am I`` probe; anonymous when
  no Clerk session is attached.
* ``POST /billing/checkout`` — Clerk-gated; creates a Stripe Checkout
  session for the Pro plan. Placeholder-safe: returns 503 when
  Stripe isn't configured.
* ``POST /stripe/webhook`` — signature-verified entrypoint that
  Stripe calls when a subscription event fires. Updates the user's
  plan via the marketplace store. Placeholder-safe: returns 503
  when the webhook secret isn't set.

Why these three live together
-----------------------------
All three share the ``StripeConfig`` accessor and the
``MarketplaceStore`` accessor. Splitting them across two files
would force a circular re-import of the configuration helpers,
which we deliberately want to keep in ``app.py`` until enough
routers need them that lifting to a ``_helpers.py`` is worth the
extra indirection.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from planmyagents_api.auth import CurrentUser, current_user, optional_current_user
from planmyagents_api.billing import (
    StripeApiError,
    StripeSignatureError,
    WebhookHandlerError,
    create_checkout_session,
    handle_webhook_event,
    parse_webhook_event,
    verify_webhook_signature,
)

router = APIRouter()
logger = logging.getLogger("planmyagents_api.web.routes.billing")


@router.get("/account/me", tags=["account"])
def account_me(
    user: Annotated[CurrentUser | None, Depends(optional_current_user)],
) -> dict[str, Any]:
    """Read-only "who am I" probe. Anonymous when not signed in."""

    if user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": user.user.user_id,
        "clerk_user_id": user.clerk_user_id,
        "email": user.email,
        "plan": user.plan,
    }


@router.post("/billing/checkout", tags=["billing"])
def billing_checkout(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict[str, Any]:
    """Create a Stripe Checkout session for the Pro plan.

    Returns ``{"url": "<stripe-hosted-checkout-url>"}`` for the
    frontend to redirect to. Placeholder-safe: returns 503 if
    Stripe isn't configured.
    """

    from planmyagents_api.web.app import _stripe_config

    config = _stripe_config()
    if not config.is_active():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "billing_not_configured",
                "message": (
                    "Stripe billing is not configured on this server. "
                    "Set STRIPE_SECRET_KEY and STRIPE_PRO_PRICE_ID."
                ),
            },
        )
    try:
        session = create_checkout_session(
            config=config,
            user_id=user.user_id,
            customer_email=user.email,
        )
    except StripeApiError as exc:
        logger.error("stripe.checkout.failed user=%s err=%s", user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "stripe_api_error", "message": str(exc)},
        ) from exc
    return {
        "session_id": session.session_id,
        "url": session.url,
        "mode": "test" if config.is_test_mode() else "live",
    }


@router.post("/stripe/webhook", tags=["billing"])
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Receive Stripe webhook events.

    No Clerk auth — authenticity is established by the
    ``Stripe-Signature`` HMAC. Placeholder-safe: returns 503 when
    the webhook secret is not configured (so a misconfigured Stripe
    endpoint logs cleanly instead of 500'ing).
    """

    from planmyagents_api.web.app import _marketplace_store, _stripe_config

    config = _stripe_config()
    if not config.is_webhook_active():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "webhook_not_configured",
                "message": "STRIPE_WEBHOOK_SECRET is not set on this server.",
            },
        )
    raw = await request.body()
    signature = request.headers.get("Stripe-Signature")
    try:
        verify_webhook_signature(
            payload=raw,
            signature_header=signature,
            secret=config.webhook_secret,
            tolerance_seconds=config.replay_tolerance_seconds,
        )
        event = parse_webhook_event(raw)
    except StripeSignatureError as exc:
        logger.warning("stripe.webhook.bad_signature err=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc
    try:
        outcome = handle_webhook_event(
            event=event, store=_marketplace_store()
        )
    except WebhookHandlerError as exc:
        # Surface as 500 so Stripe retries. The handler already
        # logged the underlying exception.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "handler_failed", "message": str(exc)},
        ) from exc
    return outcome

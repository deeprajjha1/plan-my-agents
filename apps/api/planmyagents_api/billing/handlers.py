"""Stripe webhook event router.

Maps the small set of Stripe events we care about today onto
``marketplace_store.users.plan`` updates. Everything else is a no-op so
adding new event types to the Stripe dashboard doesn't surprise-break
the webhook.

Events we handle
----------------

* ``checkout.session.completed``        — user finished checkout. The
  session carries ``client_reference_id`` (set by
  :func:`create_checkout_session`) = our ``users.user_id``. We mark the
  user as ``pro``.
* ``customer.subscription.deleted``     — subscription cancelled (user
  ended it, payment failed, etc.). We map them back to ``free``. The
  user id is sourced from ``metadata.planmyagents_user_id`` which we set
  at checkout time.
* ``customer.subscription.updated``     — status transitions (active ↔
  past_due ↔ canceled). We mirror ``status=active`` → pro and any other
  status → free, deferring richer plan handling (grace periods, dunning)
  to Sprint 5.

Idempotency
-----------

Stripe retries failed webhook deliveries. The handler is naturally
idempotent because :meth:`MarketplaceStore.set_plan` is — replaying the
same event simply re-applies the same plan value.

Observability
-------------

Every event yields a structured log line via the module logger so an
operator can grep for ``stripe.webhook.handled`` to see exactly which
events landed.
"""

from __future__ import annotations

import logging
from typing import Any

from planmyagents_api.billing.stripe_client import WebhookEvent
from planmyagents_api.marketplace_store import MarketplaceStore

logger = logging.getLogger("planmyagents_api.billing.handlers")


class WebhookHandlerError(Exception):
    """Raised when a webhook arrives that we expected to handle but cannot.

    Most failures (unknown event types, missing user id) are *swallowed*
    with a log line so Stripe stops retrying — this exception is reserved
    for cases where the event is well-formed but the action itself fails
    (e.g. database down).
    """


def handle_webhook_event(
    *,
    event: WebhookEvent,
    store: MarketplaceStore,
) -> dict[str, Any]:
    """Apply ``event`` to ``store``. Returns a structured outcome dict."""
    handler = _DISPATCH.get(event.event_type)
    if handler is None:
        logger.info(
            "stripe.webhook.unhandled event_id=%s type=%s",
            event.event_id,
            event.event_type,
        )
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "outcome": "ignored",
            "reason": "unhandled_event_type",
        }
    try:
        outcome = handler(event=event, store=store)
    except WebhookHandlerError:
        raise
    except Exception as exc:  # noqa: BLE001 — convert to typed error for caller
        logger.exception(
            "stripe.webhook.failed event_id=%s type=%s",
            event.event_id,
            event.event_type,
        )
        raise WebhookHandlerError(str(exc)) from exc

    logger.info(
        "stripe.webhook.handled event_id=%s type=%s outcome=%s",
        event.event_id,
        event.event_type,
        outcome.get("outcome"),
    )
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        **outcome,
    }


# ---------------------------------------------------------------------------
# Per-event handlers
# ---------------------------------------------------------------------------


def _resolve_user_id(data_object: dict[str, Any]) -> str:
    """Pick the planmyagents user id from a Stripe event payload.

    Stripe stores it in two places depending on the event:

    * ``checkout.session.*``  →  top-level ``client_reference_id``.
    * ``customer.subscription.*`` → ``metadata.planmyagents_user_id``
      (we set it explicitly at checkout creation time).
    """
    candidate = str(data_object.get("client_reference_id") or "").strip()
    if candidate:
        return candidate
    metadata = data_object.get("metadata")
    if isinstance(metadata, dict):
        candidate = str(metadata.get("planmyagents_user_id") or "").strip()
        if candidate:
            return candidate
    return ""


def _handle_checkout_session_completed(
    *, event: WebhookEvent, store: MarketplaceStore
) -> dict[str, Any]:
    user_id = _resolve_user_id(event.data_object)
    if not user_id:
        return {"outcome": "skipped", "reason": "missing_user_id"}
    payment_status = str(event.data_object.get("payment_status") or "").lower()
    if payment_status and payment_status not in {"paid", "no_payment_required"}:
        # User went through Checkout but payment did not actually succeed
        # (e.g. SCA failed). Leave plan untouched; the eventual
        # `customer.subscription.updated` event will reflect the truth.
        return {
            "outcome": "skipped",
            "reason": f"payment_status={payment_status}",
        }
    user = store.set_plan(user_id=user_id, plan="pro")
    return {"outcome": "upgraded", "user_id": user.user_id, "plan": user.plan}


def _handle_subscription_deleted(
    *, event: WebhookEvent, store: MarketplaceStore
) -> dict[str, Any]:
    user_id = _resolve_user_id(event.data_object)
    if not user_id:
        return {"outcome": "skipped", "reason": "missing_user_id"}
    user = store.set_plan(user_id=user_id, plan="free")
    return {"outcome": "downgraded", "user_id": user.user_id, "plan": user.plan}


def _handle_subscription_updated(
    *, event: WebhookEvent, store: MarketplaceStore
) -> dict[str, Any]:
    user_id = _resolve_user_id(event.data_object)
    if not user_id:
        return {"outcome": "skipped", "reason": "missing_user_id"}
    status = str(event.data_object.get("status") or "").lower()
    # `trialing` and `active` both grant Pro access. Everything else (past_due,
    # unpaid, canceled, incomplete*) revokes it until they fix the payment.
    if status in {"active", "trialing"}:
        user = store.set_plan(user_id=user_id, plan="pro")
        return {"outcome": "upgraded", "user_id": user.user_id, "plan": user.plan}
    user = store.set_plan(user_id=user_id, plan="free")
    return {
        "outcome": "downgraded",
        "user_id": user.user_id,
        "plan": user.plan,
        "stripe_status": status,
    }


_DISPATCH = {
    "checkout.session.completed": _handle_checkout_session_completed,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "customer.subscription.updated": _handle_subscription_updated,
}

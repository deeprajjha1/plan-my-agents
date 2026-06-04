"""Runtime scoring for workflow provider responses.

Benchmark scoring compares outputs against known expected answers. Runtime
workflow scoring is different: it estimates confidence from normalized provider
responses so the final artifact can expose quality signals to users.
"""

from __future__ import annotations

from typing import Any

from planmyagents_api.benchmark.models import FieldScore, ProviderResponse, ScoreResult


def score_workflow_response(capability: str, response: ProviderResponse) -> ScoreResult:
    """Return a lightweight quality score for a live workflow response."""

    if not response.succeeded:
        return ScoreResult(
            quality_score=0.0,
            succeeded=False,
            field_scores=[],
            reason=response.error or "provider_response_failed",
        )

    output = response.output or {}
    if capability == "email_verification":
        return _score_email_verification(output)
    if capability == "contact_enrichment":
        return _score_contact_enrichment(output)
    if capability == "web_scraping":
        return _score_web_scraping(output)
    if capability == "payment_authorization":
        return _score_payment_authorization(output)
    if capability == "email_send":
        return _score_email_send(output)
    if capability == "shipping_quote":
        return _score_shipping_quote(output)
    if capability == "price_comparison":
        return _score_price_comparison(output)

    return _score_generic_output(output)


def _score_email_verification(output: dict[str, Any]) -> ScoreResult:
    result = str(output.get("result", "unknown")).lower()
    provider_score = _clamp(float(output.get("score") or 0) / 100)
    result_floor = {
        "deliverable": 0.9,
        "risky": 0.55,
        "unknown": 0.35,
        "undeliverable": 0.75,
    }.get(result, 0.2)
    quality = round(max(provider_score, result_floor), 4)
    return ScoreResult(
        quality_score=quality,
        succeeded=result in {"deliverable", "risky", "unknown", "undeliverable"},
        field_scores=[
            FieldScore(
                field="result",
                weight=0.6,
                score=result_floor,
                reason=f"email_result:{result}",
            ),
            FieldScore(
                field="score",
                weight=0.4,
                score=provider_score,
                reason="provider_score_normalized",
            ),
        ],
        reason="runtime_email_verification_scored",
    )


def _score_contact_enrichment(output: dict[str, Any]) -> ScoreResult:
    found = bool(output.get("found", output.get("status") == "ok"))
    confidence = _clamp(float(output.get("confidence") or 0.0))
    required_fields = ["email", "first_name", "last_name", "title"]
    completeness = sum(1 for field in required_fields if output.get(field)) / len(required_fields)
    quality = round((0.65 * confidence) + (0.35 * completeness), 4) if found else 0.0
    return ScoreResult(
        quality_score=quality,
        succeeded=found and quality > 0,
        field_scores=[
            FieldScore("confidence", 0.65, confidence, "provider_confidence"),
            FieldScore("completeness", 0.35, completeness, "required_field_completeness"),
        ],
        reason="runtime_contact_enrichment_scored" if found else "contact_not_found",
    )


def _score_web_scraping(output: dict[str, Any]) -> ScoreResult:
    """Score a web_scraping response.

    Two distinct response shapes are supported:

    1. Firecrawl-shaped (``status="scraped"`` + ``markdown`` /
       ``char_count`` fields). Quality is driven by char_count
       — non-empty markdown is the wrapper-level success signal.
    2. Legacy web-contact-discovery shape (``status="ok"`` +
       ``email_count``). Preserved so older mocks still grade.

    Anything else falls through to the generic scorer.
    """

    status = str(output.get("status", "")).lower()

    if status == "scraped":
        char_count = int(output.get("char_count") or 0)
        link_count = int(output.get("link_count") or 0)
        # Char-count thresholds calibrated against Firecrawl's
        # behaviour on RFC-2606 reserved domains: example.com
        # returns ~150 chars of explanatory copy, content-rich
        # pages routinely return 1000+. Anything under 50 chars
        # is suspicious (often an error page rendered as content).
        if char_count >= 1000:
            content_quality = 1.0
        elif char_count >= 100:
            content_quality = 0.85
        elif char_count >= 50:
            content_quality = 0.6
        else:
            content_quality = 0.2
        quality = content_quality
        return ScoreResult(
            quality_score=round(quality, 4),
            succeeded=char_count > 0,
            field_scores=[
                FieldScore(
                    field="char_count",
                    weight=0.7,
                    score=content_quality,
                    reason=f"markdown_chars:{char_count}",
                ),
                FieldScore(
                    field="link_count",
                    weight=0.3,
                    score=1.0 if link_count > 0 else 0.0,
                    reason=f"links_extracted:{link_count}",
                ),
            ],
            reason="runtime_web_scraping_scored",
        )

    if status == "ok":
        email_count = int(output.get("email_count") or 0)
        quality = 0.85 if email_count > 0 else 0.25
        return ScoreResult(
            quality_score=quality,
            succeeded=email_count > 0,
            field_scores=[
                FieldScore(
                    "email_count", 1.0, quality, f"extracted_emails:{email_count}"
                ),
            ],
            reason="runtime_web_contact_discovery_scored",
        )

    return _score_generic_output(output)


def _score_payment_authorization(output: dict[str, Any]) -> ScoreResult:
    """Score a Stripe-shaped PaymentIntent response.

    Only payments whose response carries a Stripe-style ``status``
    field are scored against the PaymentIntent contract. Any other
    shape (mock providers, alternative payment processors with
    different normalisation, free-form provider outputs from tests)
    falls through to the generic scorer so we don't accidentally
    refuse them — the wrapper-specific contract is enforced by the
    benchmark suite, not the runtime scorer.

    For Stripe-shaped responses, ``status`` is the primary success
    signal:
      * ``succeeded`` — terminal success; full quality.
      * ``requires_action`` — 3DS / further auth needed. From a
        wrapper-quality perspective this is *honest* (the user
        triggered an SCA flow), so we score it positively but not
        full marks; the workflow stitcher decides whether to
        proceed or refuse based on capability semantics.
      * ``requires_payment_method`` / ``requires_confirmation`` —
        the call shape was wrong; partial credit so the operator
        can spot misuses without a bench failure.
      * everything else — failed.
    """

    if "status" not in output:
        # Not a Stripe-shaped response — likely a mock or an
        # alternative payment provider with its own contract. Fall
        # through to the generic scorer (which gives 0.7 for any
        # non-empty output) so the executor still treats the
        # sub-task as succeeded.
        return _score_generic_output(output)

    status = str(output.get("status", "")).lower()
    test_mode = bool(output.get("test_mode"))
    # Stripe carries the auth handle as ``intent_id``; Razorpay
    # carries it as ``order_id``. The runtime scorer doesn't care
    # which — it cares that *some* authoritative provider id came
    # back, so a downstream sub-task can quote it back to support /
    # capture / refund APIs.
    auth_id = str(output.get("intent_id") or output.get("order_id") or "")
    quality_by_status = {
        # Stripe terminal-success vocabulary.
        "succeeded": 0.95,
        "requires_action": 0.7,
        "requires_capture": 0.85,
        "requires_confirmation": 0.4,
        "requires_payment_method": 0.3,
        "processing": 0.6,
        "canceled": 0.1,
        # Razorpay terminal-success vocabulary. ``created`` is the
        # Order-create authoritative state; ``attempted`` means a
        # payment attempt was made on that order; ``paid`` means
        # the customer completed checkout. Wrapper only models the
        # first step (Order create), so ``created`` IS the success
        # state for the wrapper-level demo.
        "created": 0.95,
        "attempted": 0.85,
        "paid": 0.95,
    }
    quality = quality_by_status.get(status, 0.0)
    # Wrapper-level success: any terminal-success status with a
    # provider id present.
    success_states = {"succeeded", "created", "paid", "requires_capture"}
    succeeded = status in success_states and bool(auth_id)
    return ScoreResult(
        quality_score=round(quality, 4),
        succeeded=succeeded,
        field_scores=[
            FieldScore(
                field="status",
                weight=0.8,
                score=quality,
                reason=f"payment_status:{status}",
            ),
            FieldScore(
                field="auth_id_present",
                weight=0.2,
                score=1.0 if auth_id else 0.0,
                reason=("auth_id_present" if auth_id else "auth_id_missing"),
            ),
        ],
        reason=(
            f"runtime_payment_authorization_scored:{status}"
            + (" (test_mode)" if test_mode else "")
        ),
    )


def _score_email_send(output: dict[str, Any]) -> ScoreResult:
    """Score a Resend-shaped email_send response.

    The wrapper synthesises ``status="queued"`` for any 200
    response with an ``email_id`` set; the workflow scorer treats
    that as full quality. Test-mode sends (resend.dev test
    addresses) score the same as production sends — the wrapper's
    job is to queue, the queue API doesn't differentiate. Bounce
    / complaint state arrives later via webhook (not modelled
    here).

    For non-Resend-shaped responses (no ``email_id``) we fall
    through to the generic scorer.
    """

    if "email_id" not in output:
        return _score_generic_output(output)

    status = str(output.get("status", "")).lower()
    email_id = str(output.get("email_id", ""))
    test_mode = bool(output.get("test_mode"))
    quality_by_status = {
        "queued": 0.95,
        "sent": 0.95,
        "delivered": 1.0,
    }
    quality = quality_by_status.get(status, 0.0)
    succeeded = status in {"queued", "sent", "delivered"} and bool(email_id)
    return ScoreResult(
        quality_score=round(quality, 4),
        succeeded=succeeded,
        field_scores=[
            FieldScore(
                field="status",
                weight=0.7,
                score=quality,
                reason=f"email_status:{status}",
            ),
            FieldScore(
                field="email_id_present",
                weight=0.3,
                score=1.0 if email_id else 0.0,
                reason=("email_id_present" if email_id else "email_id_missing"),
            ),
        ],
        reason=(
            f"runtime_email_send_scored:{status}"
            + (" (test_mode)" if test_mode else "")
        ),
    )


def _score_shipping_quote(output: dict[str, Any]) -> ScoreResult:
    """Score a Shippo-shaped shipping_quote response.

    Scoring centers on rate availability: a quote that returns
    zero rates is not useful even if technically successful.
    Falls through to the generic scorer for non-Shippo shapes.
    """

    if "rate_count" not in output and "rates" not in output:
        return _score_generic_output(output)

    rate_count = int(output.get("rate_count") or 0)
    cheapest = output.get("cheapest_rate")
    test_mode = bool(output.get("test_mode"))
    has_cheapest = isinstance(cheapest, dict) and float(cheapest.get("amount") or 0) > 0
    if rate_count >= 3 and has_cheapest:
        quality = 1.0
    elif rate_count >= 1 and has_cheapest:
        quality = 0.85
    else:
        quality = 0.0
    return ScoreResult(
        quality_score=round(quality, 4),
        succeeded=rate_count > 0 and has_cheapest,
        field_scores=[
            FieldScore(
                field="rate_count",
                weight=0.6,
                score=quality,
                reason=f"rates_returned:{rate_count}",
            ),
            FieldScore(
                field="cheapest_rate",
                weight=0.4,
                score=1.0 if has_cheapest else 0.0,
                reason=(
                    "cheapest_rate_available"
                    if has_cheapest
                    else "no_priced_rate"
                ),
            ),
        ],
        reason=(
            f"runtime_shipping_quote_scored:rates={rate_count}"
            + (" (test_mode)" if test_mode else "")
        ),
    )


def _score_price_comparison(output: dict[str, Any]) -> ScoreResult:
    """Score an eBay-shaped price_comparison response.

    Quality is driven by listing breadth: more competing
    listings = more meaningful comparison. ``status="no_results"``
    is honest (real outcome for niche queries) so we score it
    above a hallucinated false-listing response, but below a
    successful match.

    Falls through to the generic scorer for non-eBay shapes.
    """

    if "result_count" not in output and "items" not in output:
        return _score_generic_output(output)

    status = str(output.get("status", "")).lower()
    result_count = int(output.get("result_count") or 0)
    min_price = output.get("min_price")
    has_min = isinstance(min_price, dict) and float(min_price.get("price") or 0) > 0

    if status == "no_results":
        # Honest "no listings for this query" — partial credit so
        # the operator can distinguish "we tried and got 0" from
        # a real failure.
        quality = 0.4
        succeeded = True
    elif result_count >= 5 and has_min:
        quality = 1.0
        succeeded = True
    elif result_count >= 1 and has_min:
        quality = 0.75
        succeeded = True
    else:
        quality = 0.0
        succeeded = False

    return ScoreResult(
        quality_score=round(quality, 4),
        succeeded=succeeded,
        field_scores=[
            FieldScore(
                field="result_count",
                weight=0.6,
                score=quality,
                reason=f"listings_returned:{result_count}",
            ),
            FieldScore(
                field="min_price",
                weight=0.4,
                score=1.0 if has_min else (0.6 if status == "no_results" else 0.0),
                reason=(
                    "min_price_present"
                    if has_min
                    else ("no_results_honest" if status == "no_results" else "no_min_price")
                ),
            ),
        ],
        reason=f"runtime_price_comparison_scored:status={status}",
    )


def _score_generic_output(output: dict[str, Any]) -> ScoreResult:
    quality = 0.7 if output else 0.0
    return ScoreResult(
        quality_score=quality,
        succeeded=bool(output),
        field_scores=[FieldScore("output", 1.0, quality, "non_empty_output")],
        reason="runtime_generic_scored" if output else "empty_output",
    )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))

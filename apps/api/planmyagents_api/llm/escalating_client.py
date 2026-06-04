"""Escalating chat client: ordered tiers, no substring rules.

Why this module exists
----------------------
Every LLM-driven decision in PlanMyAgents (intent mapping, planning,
query expansion, agent classification, candidate judging) historically
asked one of two clients directly: ``OllamaQwenClient`` (local, free,
small) or ``GroqChatClient`` (hosted, paid, larger). The selection was
done per-call-site via env vars, and on failure the system silently
fell back to deterministic substring rules — which is exactly how a
school project ended up presented as the answer to "find me the
cheapest single malt".

The fix is a single chat client that walks an ordered list of tiers,
returning the first tier whose response passes an optional caller-
supplied quality check, and refusing if no tier can answer.

Tier semantics
--------------

A *tier* is just a ``ChatClient`` paired with a short label for
provenance. The chain is tried in declaration order:

1. ``tiers[0]`` is attempted. If it succeeds at transport AND the
   caller's :class:`QualityVerdict` (or the default ACCEPT) accepts
   the content, return it.
2. On transport failure, empty response, or ``QualityVerdict.ESCALATE``,
   move to ``tiers[1]`` and repeat.
3. If every tier fails, raise :class:`NoLlmTierAvailableError` with
   structured per-tier metadata so the route handler can surface a
   clean refusal rather than emit a confidently-wrong answer.

This is the generalisation of the old "primary + fallback" design.
The two-tier API is preserved as a thin compatibility shim:
constructing with ``primary=``/``fallback=`` populates ``tiers`` under
the hood, and the legacy ``primary_*`` / ``fallback_*`` metadata fields
keep getting filled for the first two tiers.

Why an ordered list rather than a single primary
------------------------------------------------

Real-world failure modes seen on production /goal traffic:

* **Hosted model returns malformed JSON.** A specific Groq model
  (e.g. ``llama-4-scout``) produces a sub-task with an empty
  ``search_query``. The decomposer's strict parser rejects it.
  Without rotation, the only recovery path is the slow local Ollama
  Qwen fallback (60-110s/call). With rotation we try a second Groq
  model first (sub-second) and only fall to Ollama if every Groq
  attempt fails.
* **Hosted model rate-limited.** When ``llama-4-scout`` returns
  ``429: rate_limit_exceeded`` we don't want to immediately suffer
  the 60s+ Ollama latency penalty — try a larger Groq model first.
* **Quality issue specific to a model size.** Smaller models
  occasionally produce hallucinated capability ids the snake_case
  validator rejects; the next-larger model usually answers cleanly.

Caller usage
------------

Most callers just want ``client.complete(messages)`` and don't care
which tier answered. Callers that care about provenance (e.g. the
``/goal`` route surfaces a "answered by tier X" badge in the UI)
should use ``complete_with_metadata`` and read the returned
``EscalationResult.metadata``.

Callers with strict output validation (decomposer, judge) should
pass a ``quality_check`` callback. The callback receives the raw
content and returns ``QualityVerdict.ACCEPT`` or
``QualityVerdict.ESCALATE``. If a transport-success but
content-bad answer appears, ``ESCALATE`` triggers the next tier
exactly like a transport failure would.

Configuration
-------------

The :func:`build_default_escalating_client` factory constructs the
canonical chain from environment variables:

* ``GROQ_API_KEY`` — if set, Groq tier(s) are constructed.
* ``GROQ_MODELS`` — comma-separated list of Groq model ids to rotate
  through. Defaults to ``"<GROQ_MODEL>"`` (single model, current
  behaviour). Common rotation:
  ``"meta-llama/llama-4-scout-17b-16e-instruct,llama-3.3-70b-versatile"``.
* ``GROQ_MODEL`` — single Groq model id; used as the default when
  ``GROQ_MODELS`` is unset.
* ``PLANMYAGENTS_LLM_TIER_ORDER`` — ``auto`` (default) puts Groq
  before Ollama when the key is set; ``qwen_first`` flips it; ``groq_first``
  is the explicit ``auto``-when-key-set form.

If both Groq and Ollama are absent, the resulting client always raises
``NoLlmTierAvailableError`` on call — the correct signal for the route
handler to refuse rather than fabricate an answer.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from planmyagents_api.planner.groq_client import GroqChatClient, GroqChatError
from planmyagents_api.planner.local_qwen import LocalQwenPlannerError, OllamaQwenClient


class ChatClient(Protocol):
    """Single-method protocol for any chat backend (Qwen, Groq, fakes)."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


class QualityVerdict(enum.Enum):
    """Caller-supplied verdict on a tier response.

    The escalating client does not inspect the tier's content. It
    asks the caller (via the ``quality_check`` callable on
    :meth:`EscalatingChatClient.complete_with_metadata`) whether the
    response is good enough to ship. If the caller says ``ESCALATE``,
    the client tries the next tier.
    """

    ACCEPT = "accept"
    ESCALATE = "escalate"


@dataclass
class TierAttempt:
    """Per-tier outcome record. Used to build EscalationMetadata."""

    label: str
    attempted: bool = False
    error: str | None = None
    quality_check_triggered: bool = False
    elapsed_ms: int = 0
    succeeded: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "label": self.label,
            "attempted": self.attempted,
            "error": self.error,
            "quality_check_triggered": self.quality_check_triggered,
            "elapsed_ms": self.elapsed_ms,
            "succeeded": self.succeeded,
        }


@dataclass
class EscalationMetadata:
    """Provenance for a single chat completion.

    Surfaced verbatim in API responses so the UI can render an honest
    "this answer was produced by tier X (escalated from tier Y because
    Z)" badge.

    Two views of the same data live here:

    * ``tier_attempts`` — the new N-tier history. One entry per tier
      attempted (or skipped because an earlier tier succeeded).
    * ``primary_*`` / ``fallback_*`` — the legacy two-tier view, still
      populated for the FIRST TWO tiers so existing API consumers
      (UI badges, run logs, tests) keep working unchanged.
    """

    tier_used: str = "none"  # "primary" | "fallback" | "tier_<n>" | "none"
    primary_label: str = ""
    fallback_label: str = ""
    primary_attempted: bool = False
    primary_error: str | None = None
    quality_check_triggered: bool = False
    fallback_attempted: bool = False
    fallback_error: str | None = None
    tier_attempts: list[TierAttempt] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "tier_used": self.tier_used,
            "primary_label": self.primary_label,
            "fallback_label": self.fallback_label,
            "primary_attempted": self.primary_attempted,
            "primary_error": self.primary_error,
            "quality_check_triggered": self.quality_check_triggered,
            "fallback_attempted": self.fallback_attempted,
            "fallback_error": self.fallback_error,
            "tier_attempts": [a.to_json() for a in self.tier_attempts],
        }


@dataclass(frozen=True)
class EscalationResult:
    """Content + provenance for a single completion."""

    content: str
    metadata: EscalationMetadata


class NoLlmTierAvailableError(RuntimeError):
    """Raised when no tier could produce a usable response.

    Carries the :class:`EscalationMetadata` for the failed call so the
    route handler can surface a structured refusal instead of a generic
    500.
    """

    def __init__(self, metadata: EscalationMetadata, message: str = "") -> None:
        self.metadata = metadata
        if not message:
            message = _format_failure_message(metadata)
        super().__init__(message)


def _format_failure_message(metadata: EscalationMetadata) -> str:
    if metadata.tier_attempts:
        parts: list[str] = []
        for attempt in metadata.tier_attempts:
            if not attempt.attempted:
                parts.append(f"{attempt.label}: not attempted")
                continue
            reason = attempt.error or "quality check failed"
            parts.append(f"{attempt.label}: {reason}")
        return "No LLM tier available: " + " | ".join(parts)
    # Legacy path: ``EscalationMetadata`` constructed manually with the
    # primary_*/fallback_* fields (e.g. by a caller that wraps a fake
    # error before calling .raise_no_llm_tier). Render those slots so
    # the failure string still surfaces the original reason text instead
    # of the unhelpful "no tiers configured" placeholder.
    parts = []
    if metadata.primary_attempted or metadata.primary_label:
        if metadata.primary_attempted:
            reason = metadata.primary_error or "quality check failed"
            parts.append(f"primary[{metadata.primary_label}]: {reason}")
        elif metadata.primary_label:
            parts.append(f"primary[{metadata.primary_label}]: not attempted")
    else:
        parts.append("primary: not configured")
    if metadata.fallback_attempted or metadata.fallback_label:
        if metadata.fallback_attempted:
            reason = metadata.fallback_error or "empty response"
            parts.append(f"fallback[{metadata.fallback_label}]: {reason}")
        elif metadata.fallback_label:
            parts.append(f"fallback[{metadata.fallback_label}]: not attempted")
    else:
        parts.append("fallback: not configured")
    return "No LLM tier available: " + " | ".join(parts)


@dataclass
class EscalatingChatClient:
    """Chat client that walks an ordered tier list, escalating on
    transport failure or low quality, and refuses if no tier can answer.

    Construction
    ------------

    Two equivalent ways to construct:

    1. New (preferred for new code) — pass ``tiers``::

           EscalatingChatClient(
               tiers=[groq_scout, groq_70b, ollama_qwen],
               tier_labels=["llama-4-scout", "llama-3.3-70b", "ollama-qwen"],
           )

    2. Legacy (still supported) — pass ``primary`` / ``fallback``::

           EscalatingChatClient(
               primary=groq_client,
               fallback=ollama_client,
               primary_label="groq",
               fallback_label="ollama-qwen",
           )

       This is internally rewritten to a 1- or 2-element ``tiers`` list.

    The two forms are mutually exclusive: passing both ``tiers`` and
    ``primary``/``fallback`` raises ``ValueError`` to surface the
    ambiguity at construction time rather than silently picking one.
    """

    primary: ChatClient | None = None
    fallback: ChatClient | None = None
    primary_label: str = ""
    fallback_label: str = ""
    tiers: list[ChatClient] = field(default_factory=list)
    tier_labels: list[str] = field(default_factory=list)
    last_metadata: EscalationMetadata = field(
        default_factory=EscalationMetadata, repr=False, compare=False
    )
    # Per-tier "kind" label used by tier_used in metadata. For tiers
    # constructed via the legacy primary= / fallback= API, this records
    # which slot the tier originally lived in so a primary-was-None
    # call still correctly reports tier_used="fallback" rather than
    # "primary" (the latter would be a regression for any caller that
    # branches on that string). For tiers constructed via the new
    # tiers= list API, the kinds default to "primary","fallback","tier_2",…
    # — i.e. the same convention complete_with_metadata used to apply
    # by index. Maintained as a private field so it doesn't leak into
    # the repr or affect equality.
    _tier_kinds: list[str] = field(
        default_factory=list, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        # Reject mixed-construction: passing tiers AND primary/fallback
        # is almost certainly a bug — which one wins? Refuse at
        # construction time so the operator notices immediately
        # rather than during the first /goal call.
        if self.tiers and (self.primary is not None or self.fallback is not None):
            raise ValueError(
                "EscalatingChatClient: pass either 'tiers' OR "
                "'primary'/'fallback', not both."
            )
        if self.tiers:
            # tier_labels is allowed to be shorter than tiers — fill
            # the missing slots with auto-derived labels.
            labels = list(self.tier_labels)
            while len(labels) < len(self.tiers):
                labels.append(_label_for(self.tiers[len(labels)]))
            self.tier_labels = labels
            # New API ⇒ first/second positions are primary/fallback;
            # everything beyond is tier_<index>.
            self._tier_kinds = [
                "primary" if i == 0 else "fallback" if i == 1 else f"tier_{i}"
                for i in range(len(self.tiers))
            ]
            # Mirror first two tiers into the legacy primary/fallback
            # slots so existing callers reading those fields keep
            # working.
            self.primary = self.tiers[0]
            self.primary_label = self.tier_labels[0]
            if len(self.tiers) >= 2:
                self.fallback = self.tiers[1]
                self.fallback_label = self.tier_labels[1]
            return

        # Legacy path: synthesise tiers from primary / fallback.
        # Track which slot each tier came from so the metadata's
        # tier_used field is semantically correct (a fallback-only
        # client must report "fallback" even though it's at index 0
        # in the synthesised list).
        synthesised: list[ChatClient] = []
        synthesised_labels: list[str] = []
        synthesised_kinds: list[str] = []
        if self.primary is not None:
            synthesised.append(self.primary)
            synthesised_labels.append(
                self.primary_label or _label_for(self.primary)
            )
            synthesised_kinds.append("primary")
        if self.fallback is not None:
            synthesised.append(self.fallback)
            synthesised_labels.append(
                self.fallback_label or _label_for(self.fallback)
            )
            synthesised_kinds.append("fallback")
        self.tiers = synthesised
        self.tier_labels = synthesised_labels
        self._tier_kinds = synthesised_kinds

    def complete(self, messages: list[dict[str, str]]) -> str:
        """Drop-in replacement for the bare ``ChatClient.complete``
        protocol. Discards metadata; callers that need provenance
        should use :meth:`complete_with_metadata` instead (or read
        ``self.last_metadata`` after the call)."""

        return self.complete_with_metadata(messages).content

    def complete_with_metadata(
        self,
        messages: list[dict[str, str]],
        *,
        quality_check: Callable[[str], QualityVerdict] | None = None,
    ) -> EscalationResult:
        """Run the escalation chain. Returns the first tier whose
        response passes quality check; raises
        :class:`NoLlmTierAvailableError` if none do.

        Args:
            messages: chat messages in OpenAI format.
            quality_check: optional callable invoked with each tier's
                raw response. If it returns ``QualityVerdict.ESCALATE``,
                the client moves to the next tier even though the
                current tier "succeeded" at the transport layer. If it
                returns ``QualityVerdict.ACCEPT``, the response is
                returned. The same ``quality_check`` is applied to
                every tier — it must be tier-agnostic.
        """

        # Surface the FIRST primary-kind and FIRST fallback-kind labels
        # in the legacy fields so existing API consumers (UI badges,
        # run-log readers) keep seeing the same shape they always did.
        primary_label_for_metadata = ""
        fallback_label_for_metadata = ""
        for kind, label in zip(self._tier_kinds, self.tier_labels, strict=True):
            if kind == "primary" and not primary_label_for_metadata:
                primary_label_for_metadata = label
            elif kind == "fallback" and not fallback_label_for_metadata:
                fallback_label_for_metadata = label
        metadata = EscalationMetadata(
            primary_label=primary_label_for_metadata,
            fallback_label=fallback_label_for_metadata,
        )
        # Pre-populate tier_attempts so the metadata always names every
        # configured tier, even ones we never reached. Makes the failure
        # message readable.
        metadata.tier_attempts = [TierAttempt(label=label) for label in self.tier_labels]

        for index, (client, label, kind, attempt) in enumerate(
            zip(self.tiers, self.tier_labels, self._tier_kinds, metadata.tier_attempts, strict=True)
        ):
            # We log per-tier so the operator can correlate a slow
            # /goal with a slow specific tier.
            content = self._invoke_tier(
                client=client, label=label, messages=messages, attempt=attempt,
            )
            # Mirror this attempt into legacy primary_/fallback_ slots
            # based on its kind (NOT its positional index) so that a
            # fallback-only client correctly reports
            # primary_attempted=False / fallback_attempted=True even
            # though the fallback is the only tier in the list.
            self._mirror_attempt_into_legacy_fields(
                metadata=metadata, kind=kind, attempt=attempt
            )
            del index  # silence linter — kept for future per-tier metrics
            if content is None:
                continue
            verdict = QualityVerdict.ACCEPT
            if quality_check is not None:
                try:
                    verdict = quality_check(content)
                except Exception as exc:  # noqa: BLE001 - quality checks are caller-supplied
                    # Treat a quality-check exception the same as ESCALATE
                    # so caller bugs don't ship through silently.
                    attempt.error = (
                        f"quality_check raised {type(exc).__name__}: {exc}"
                    )
                    self._mirror_attempt_into_legacy_fields(
                        metadata=metadata, kind=kind, attempt=attempt
                    )
                    verdict = QualityVerdict.ESCALATE
            if verdict is QualityVerdict.ACCEPT:
                attempt.succeeded = True
                metadata.tier_used = kind
                self.last_metadata = metadata
                return EscalationResult(content=content, metadata=metadata)
            attempt.quality_check_triggered = True
            metadata.quality_check_triggered = True

        self.last_metadata = metadata
        raise NoLlmTierAvailableError(metadata)

    @staticmethod
    def _mirror_attempt_into_legacy_fields(
        *, metadata: EscalationMetadata, kind: str, attempt: TierAttempt
    ) -> None:
        """Copy the latest tier attempt's outcome into the legacy
        primary_*/fallback_* fields when the tier's kind matches.

        The legacy fields are a partial view of the new tier_attempts
        list — they only ever describe the FIRST primary-kind and the
        FIRST fallback-kind tiers. Tiers further down the chain
        (`tier_2`, `tier_3`, …) appear ONLY in tier_attempts. This
        keeps the legacy badges stable while the new fields describe
        the full chain.
        """

        if kind == "primary":
            metadata.primary_attempted = attempt.attempted
            metadata.primary_error = attempt.error
        elif kind == "fallback":
            metadata.fallback_attempted = attempt.attempted
            metadata.fallback_error = attempt.error

    def _invoke_tier(
        self,
        *,
        client: ChatClient,
        label: str,
        messages: list[dict[str, str]],
        attempt: TierAttempt,
    ) -> str | None:
        """Single-tier transport call. Records timing + outcome on the
        provided ``TierAttempt`` and returns the content (or None on
        failure). Never raises — failures escalate by returning None.
        """

        attempt.attempted = True
        _emit_log(f"escalating: tier call starting label={label}")
        started = _now()
        try:
            content = client.complete(messages)
        except Exception as exc:  # noqa: BLE001 - any client may raise many shapes
            attempt.error = f"{type(exc).__name__}: {exc}"
            attempt.elapsed_ms = _elapsed_ms(started)
            _emit_log(
                f"escalating: tier FAILED label={label} "
                f"elapsed={attempt.elapsed_ms}ms error={attempt.error}",
                level="warning",
            )
            return None
        attempt.elapsed_ms = _elapsed_ms(started)
        if not content or not content.strip():
            attempt.error = "empty_response"
            _emit_log(
                f"escalating: tier EMPTY label={label} elapsed={attempt.elapsed_ms}ms",
                level="warning",
            )
            return None
        _emit_log(
            f"escalating: tier OK label={label} "
            f"elapsed={attempt.elapsed_ms}ms chars={len(content)}"
        )
        return content


def _now() -> float:
    import time as _time

    return _time.monotonic()


def _elapsed_ms(started: float) -> int:
    import time as _time

    return int((_time.monotonic() - started) * 1000)


def _emit_log(message: str, *, level: str = "info") -> None:
    """Lazy-resolved logger so this module doesn't carry a global
    import-time dependency on the logging configuration order."""

    import logging

    logger = logging.getLogger("planmyagents_api.llm.escalating_client")
    getattr(logger, level)(message)


def _parse_groq_models() -> list[str]:
    """Return the ordered list of Groq model ids to rotate through.

    Source priority:

    1. ``GROQ_MODELS`` (comma-separated) — explicit rotation list.
    2. ``GROQ_MODEL`` — single model, becomes a 1-element list.
    3. ``GroqChatClient`` default — fallback when neither is set.

    Empty entries (e.g. ``GROQ_MODELS=" ,llama-3.3-70b-versatile, "``)
    are stripped silently — a stray comma shouldn't kill the chain.
    Order is preserved exactly as written so the operator controls
    which model is tried first. Duplicates are de-duplicated to avoid
    accidentally rotating through the same model twice (rate limit
    blocking the rotation).
    """

    raw = (os.getenv("GROQ_MODELS") or "").strip()
    if raw:
        seen: set[str] = set()
        models: list[str] = []
        for part in raw.split(","):
            cleaned = part.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            models.append(cleaned)
        if models:
            return models
    single = (os.getenv("GROQ_MODEL") or "").strip()
    if single:
        return [single]
    # Defer to the GroqChatClient's own default (which itself reads env
    # again — but if we got here that env is unset so the client's
    # hardcoded DEFAULT_GROQ_MODEL wins).
    return [""]  # empty string → GroqChatClient picks its own default


def build_default_escalating_client(
    *,
    primary: ChatClient | None = None,
    fallback: ChatClient | None = None,
) -> EscalatingChatClient:
    """Build the canonical escalating chain.

    Tier ordering is decided by ``PLANMYAGENTS_LLM_TIER_ORDER`` plus the
    presence of ``GROQ_API_KEY``:

    * ``auto`` (default) — if ``GROQ_API_KEY`` is set, **Groq is primary
      and Qwen is fallback**. Otherwise Qwen is primary and there is no
      Groq tier. Rationale: when the user has paid for Groq, they expect
      its sub-second latency to be the default UX; local Qwen (60–110s
      per call on Apple Silicon) is the offline-capable safety net.
    * ``groq_first`` — explicit "Groq primary, Qwen fallback". Same as
      ``auto`` when ``GROQ_API_KEY`` is set; without the key this
      degrades to "Qwen primary, no fallback" since we can't construct a
      Groq client without credentials. Use this to make the intent loud
      in deployment configs.
    * ``qwen_first`` — explicit "Qwen primary, Groq fallback". This is
      the historical pre-2026-05 default. Use for cost control (Qwen is
      free, Groq is metered) when latency is acceptable.

    Groq model rotation
    -------------------

    When Groq is in the chain, ``GROQ_MODELS`` controls the model
    rotation. Set it to a comma-separated list to try several Groq
    models before escalating to Ollama:

        GROQ_MODELS=meta-llama/llama-4-scout-17b-16e-instruct,llama-3.3-70b-versatile

    Each model becomes its OWN tier. Quality-check escalation moves
    between Groq tiers exactly the same way it moves to Ollama —
    a malformed JSON response from one Groq model triggers the next
    Groq model immediately, without paying the local Qwen latency.
    Unset ``GROQ_MODELS`` ⇒ single Groq tier using ``GROQ_MODEL`` (or
    the client's own default), preserving pre-rotation behaviour.

    The ``primary``/``fallback`` overrides exist purely for tests; in
    production no caller passes them. Construction itself never contacts
    a remote service — transport failures only surface at ``.complete()``
    time, so it's safe to build clients in unit tests against fakes.

    If both tiers end up empty (no Groq key AND no Ollama install) the
    returned client always raises ``NoLlmTierAvailableError`` on the
    first call. That's the correct signal for the route handler to
    refuse the goal honestly, rather than silently falling back to
    substring rules and producing a confidently-wrong answer (the
    failure mode that motivated this whole module).
    """

    # Test/legacy override path: when the caller passes explicit
    # primary/fallback, honour exactly that — don't second-guess the
    # tier list. This keeps the suite of tests that inject StubClient
    # objects unchanged.
    if primary is not None or fallback is not None:
        return EscalatingChatClient(
            primary=primary,
            fallback=fallback,
            primary_label=_label_for(primary) if primary is not None else "",
            fallback_label=_label_for(fallback) if fallback is not None else "",
        )

    tier_order = (
        os.getenv("PLANMYAGENTS_LLM_TIER_ORDER") or "auto"
    ).strip().lower()
    if tier_order not in {"auto", "groq_first", "qwen_first"}:
        # Unknown values fall back to ``auto`` rather than raising —
        # we'd rather serve traffic with the smart default than 500 the
        # process on a typo'd env var. The unknown value is logged so
        # the operator can spot the typo in ``.planmyagents_runs/api.log``.
        _emit_log(
            f"build_default_escalating_client: unknown "
            f"PLANMYAGENTS_LLM_TIER_ORDER={tier_order!r}; falling back to 'auto'",
            level="warning",
        )
        tier_order = "auto"

    has_groq_key = bool(os.getenv("GROQ_API_KEY"))
    if tier_order == "auto":
        groq_first = has_groq_key
    elif tier_order == "groq_first":
        groq_first = has_groq_key  # no key -> can't put Groq first
    else:  # qwen_first
        groq_first = False

    groq_clients: list[ChatClient] = []
    groq_labels: list[str] = []
    if has_groq_key:
        for model in _parse_groq_models():
            try:
                # ``model=""`` lets GroqChatClient resolve via env/default.
                client = GroqChatClient(model=model) if model else GroqChatClient()
            except (GroqChatError, ValueError, TypeError) as exc:
                _emit_log(
                    f"build_default_escalating_client: failed to construct "
                    f"GroqChatClient(model={model!r}): {exc}; skipping",
                    level="warning",
                )
                continue
            groq_clients.append(client)
            # Use the actual model attribute set by __post_init__ so
            # the label reflects what will actually be sent (not the
            # raw env value, which may be empty).
            groq_labels.append(_label_for(client))

    ollama_client: ChatClient | None = None
    ollama_label = ""
    try:
        ollama_client = OllamaQwenClient()
        ollama_label = _label_for(ollama_client) or "ollama-qwen"
    except (LocalQwenPlannerError, ValueError, TypeError) as exc:
        # Ollama might be unreachable; that's fine — degrade to
        # Groq-only or no-tier rather than blowing up at startup.
        _emit_log(
            f"build_default_escalating_client: OllamaQwenClient "
            f"construction failed ({exc}); proceeding without local fallback",
            level="warning",
        )

    if groq_first:
        tiers: list[ChatClient] = list(groq_clients)
        tier_labels: list[str] = list(groq_labels)
        if ollama_client is not None:
            tiers.append(ollama_client)
            tier_labels.append(ollama_label)
    else:
        tiers = []
        tier_labels = []
        if ollama_client is not None:
            tiers.append(ollama_client)
            tier_labels.append(ollama_label)
        tiers.extend(groq_clients)
        tier_labels.extend(groq_labels)

    return EscalatingChatClient(tiers=tiers, tier_labels=tier_labels)


def _label_for(client: object) -> str:
    """Best-effort short label for a chat client (model name or class).

    Used purely for the metadata pill the UI shows; never inspected by
    routing logic. Returns an empty string if the client exposes no
    obvious identifier.
    """

    if client is None:
        return ""
    model = getattr(client, "model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    return type(client).__name__


# Re-exported so callers can catch transport errors without importing
# the planner package directly. Kept as a tuple so it can be unpacked
# into ``except`` statements.
KNOWN_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    LocalQwenPlannerError,
    GroqChatError,
)

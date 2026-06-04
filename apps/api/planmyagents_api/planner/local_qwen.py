"""Local Qwen-backed planner constrained by the provider registry.

The LLM is allowed to decompose intent, but executable capabilities must still
come from the registry. Unknown executable capabilities are converted into an
unsupported plan instead of being trusted.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask

_logger = logging.getLogger("planmyagents_api.planner.local_qwen")

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


@dataclass(frozen=True)
class ThinkingCompletion:
    """Result of a chat completion that opted into the model's
    chain-of-thought ("thinking") output.

    Surfaced by :meth:`OllamaQwenClient.complete_with_thinking` so the
    "Why did the planner pick this?" UI flow can render the model's
    reasoning trace separately from the JSON answer the planner
    actually consumes. The two fields are intentionally returned
    side-by-side: a caller wanting just the answer keeps using the
    plain ``.complete()`` method, callers that want both pay the
    extra latency on demand."""

    content: str
    thinking: str
    model: str
    duration_ms: int
    model_supports_thinking: bool
# Default = Qwen 3.5 35B on Ollama. Requires ~22 GB on disk and ~27 GB
# RAM with default context — use a smaller tag (e.g. `qwen3:32b`) on
# memory-constrained machines via PLANMYAGENTS_QWEN_MODEL.
DEFAULT_QWEN_MODEL = "qwen3.5:35b"


# NOTE on the prompt below
# ------------------------
# This prompt is intentionally **domain-agnostic**. The previous version
# embedded six hand-written "Decomposition heuristics" buckets (cross-border
# finance, real estate, crypto, travel, immigration, multi-currency commerce)
# plus four worked examples — every one of them in the regulated-finance /
# travel space. That biased the model toward those six domains: any goal
# outside them got pattern-matched to the closest bucket. A query like
# "find the cheapest single malt in southern India" came back as
# `currency_normalization` + `fare_comparison` because "multi-currency
# commerce" was the closest hardcoded recipe.
#
# We removed all of it and rely on:
#  1. Open-world capability slugs — the LLM emits any descriptive
#     snake_case operation name; downstream `_plan_from_payload` validates
#     against the registry. Misses become `missing_capabilities` (which is
#     the discovery system's input). The LLM is not steered toward known
#     slugs.
#  2. JSON Schema validation post-call — we don't ask the model to follow
#     a schema "by example", we validate its output against
#     `_PLAN_JSON_SCHEMA` and retry **once** with the validation errors
#     fed back as feedback. If the second attempt also fails, we raise
#     `LocalQwenPlannerError` and the route refuses honestly.
#  3. Stronger model on failure — when local Qwen can't satisfy the schema
#     in two tries, the EscalatingChatClient kicks in (Groq Llama-3.3-70B
#     fallback). Hosted JSON-mode LLMs essentially never miss schema, so
#     the system stays correct even when the local model is weak.
#
# Litmus test for any future edit to this prompt: would you have to edit
# it to support a new domain (liquor retail, scholarships, logo design)?
# If yes, you've reintroduced the bug. The prompt should describe the
# *shape of the answer*, not the *content of any answer*.
_PLANNER_SYSTEM_PROMPT = """
You are PlanMyAgents's planner. Convert the user's goal into a strict JSON
plan. The user goal can be from ANY domain — engineering, personal life,
regulated finance, creative work, education, retail, health, anything else.
Treat every domain identically.

What to produce:
- Decompose the goal into 3-15 atomic operations the system would need.
- Each operation gets a snake_case `capability` slug that describes ONE
  concrete action an agent could perform. Examples of good slug shapes:
  `<noun>_<verb>` or `<verb>_<noun>` (e.g. `flight_search`, `email_verify`,
  `code_migrate`, `image_generate`). The vocabulary is OPEN — emit whatever
  slug best names the action. Do not try to match a closed list.
- Pick a `description` that names the concrete thing being acted on, drawn
  from the user's goal (do not generalise away the goal's specifics).
- For each sub_task, include `inputs` if the goal supplies concrete values,
  else `{}`.

Status field:
- `executable` ONLY if every emitted `capability` slug already exists in
  the registry the user provides under `registry.capabilities`. The
  downstream validator will reject the plan otherwise — do not lie about
  feasibility.
- `unsupported` if any operation is outside the registry. Put those open-
  world slugs in `missing_capabilities` so the discovery system can search
  for agents that fill the gap. `missing_capabilities` is a feature, not
  a failure: an exhaustive list there leads to better discovery.

Hard rules:

Hard rules:
- Output STRICT JSON. No markdown fences. No prose before or after.
- The output must validate against the schema in the user message.
- Never invent providers or claim a capability is routable when it isn't.
- `discovered_candidates` in the registry input is informational only — it
  signals demand and prior discovery; it is NOT executable.
""".strip()


# JSON Schema for a valid plan. This is **the** contract — the prompt
# describes intent, this enforces shape. Validation runs after every LLM
# call; on failure we retry once with the validation errors appended as
# feedback, then refuse. We deliberately avoid the `jsonschema` library
# (not in the project's dependency tree) and implement a tight subset of
# Draft-07 inline in `_validate_plan_payload` below — the schema is small
# enough that explicit validation is more readable than a generic library
# call.
_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["status", "summary"],
    # Custom extension consumed by `_validate_plan_payload`: when
    # status=="executable", `sub_tasks` must be a non-empty list. Standard
    # Draft-07 can't express this cleanly without `if/then`; we encode it
    # as a dedicated key the inline validator understands.
    "additional_required_when": {"executable": ["sub_tasks"]},
    "properties": {
        "status": {"type": "string", "enum": ["executable", "unsupported"]},
        "summary": {"type": "string", "minLength": 1},
        "sub_tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["capability", "description"],
                "properties": {
                    "capability": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "inputs": {"type": "object"},
                },
            },
        },
        "refusal_reasons": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "missing_capabilities": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


# How many times the planner re-asks the LLM after a validation failure.
# 1 = "ask once, retry once, then fail" — three total LLM calls in the
# absolute worst case (initial + retry + escalating-client fallback). We
# pick 1 not 2 because each call costs ~30-60s on local Qwen; further
# retries push the user past their patience threshold and the right answer
# at that point is to escalate to a stronger model, not retry the same
# model.
_PLANNER_VALIDATION_RETRIES = int(
    os.getenv("PLANMYAGENTS_PLANNER_VALIDATION_RETRIES", "1")
)


class LocalQwenPlannerError(RuntimeError):
    """Raised when the local Qwen planner cannot return a valid plan."""


class ChatClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant message content."""


@dataclass(frozen=True)
class OllamaQwenClient:
    model: str = os.getenv("PLANMYAGENTS_QWEN_MODEL", DEFAULT_QWEN_MODEL)
    url: str = os.getenv("PLANMYAGENTS_OLLAMA_URL", DEFAULT_OLLAMA_URL)
    # 120s default. The previous 60s was too tight for batched judge
    # prompts (~25 candidates) on local hardware — they routinely
    # surfaced as "Local Qwen planner is unavailable" when in fact
    # Ollama was just slow. Override with PLANMYAGENTS_QWEN_TIMEOUT_SECONDS
    # if you're on faster hardware and want quicker failure detection.
    timeout_seconds: float = float(os.getenv("PLANMYAGENTS_QWEN_TIMEOUT_SECONDS", "300"))
    # Disable Ollama's "thinking" mode by default. Qwen3.x models emit
    # a multi-thousand-token chain-of-thought before the actual answer
    # which adds 60-180s on local Apple Silicon for our planner-sized
    # prompts. We always want strict JSON, not the reasoning trace, so
    # we suppress it. Set PLANMYAGENTS_QWEN_THINK=true to opt back in
    # (useful for debugging why a particular plan is wrong). Non-
    # thinking models silently ignore this flag.
    think: bool = os.getenv("PLANMYAGENTS_QWEN_THINK", "false").lower() in {"1", "true", "yes"}

    def complete(self, messages: list[dict[str, str]]) -> str:
        body, _ = self._post(messages, think=self.think)
        content = body.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise LocalQwenPlannerError(
                f"Local Qwen `{self.model}` returned an empty planner response. "
                "Check `ollama logs` for OOM, model crash, or context overflow."
            )
        return content

    def complete_with_thinking(
        self, messages: list[dict[str, str]]
    ) -> ThinkingCompletion:
        """Run the same chat completion but ALWAYS request the model's
        chain-of-thought ("thinking") output, regardless of the
        instance's ``self.think`` setting.

        Powers the on-demand "Why did the planner pick this?" UI flow:
        the user sees the JSON answer fast (via :meth:`complete` with
        ``think: false``), and only pays the +60-180s latency cost
        when they explicitly click "Why?". Returns content + thinking
        side-by-side so the caller can render both.

        ``model_supports_thinking`` reflects whether the model actually
        produced a non-empty reasoning trace. Older / non-thinking
        models (qwen2.5, llama3.x) silently ignore the ``think`` flag;
        we surface that fact rather than misleading the UI into
        rendering an empty "Reasoning" panel.
        """

        body, duration_ms = self._post(messages, think=True)
        message = body.get("message") or {}
        content = message.get("content") or ""
        thinking = message.get("thinking") or ""
        if not isinstance(content, str):
            content = ""
        if not isinstance(thinking, str):
            thinking = ""
        if not content.strip():
            raise LocalQwenPlannerError(
                f"Local Qwen `{self.model}` returned an empty planner response. "
                "Check `ollama logs` for OOM, model crash, or context overflow."
            )
        return ThinkingCompletion(
            content=content,
            thinking=thinking,
            model=self.model,
            duration_ms=duration_ms,
            model_supports_thinking=bool(thinking.strip()),
        )

    def _post(
        self, messages: list[dict[str, str]], *, think: bool
    ) -> tuple[dict[str, Any], int]:
        """Shared HTTP path for both :meth:`complete` and
        :meth:`complete_with_thinking`. Returns the parsed Ollama
        response body plus the wall-clock latency in milliseconds.

        All transport / JSON / timeout failures are translated into
        :class:`LocalQwenPlannerError` with actionable hints so the
        caller can surface a clean refusal instead of a generic 500.
        """

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": think,
            "options": {
                "temperature": 0,
            },
        }
        encoded = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.url,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.monotonic()
        # Approximate prompt size for the operator log. Real token
        # counts require the model's tokenizer (which we don't bundle
        # locally), but char-length is a good-enough proxy for spotting
        # "huge prompt" vs. "small prompt" timing differences.
        prompt_chars = sum(len(m.get("content", "") or "") for m in messages)
        _logger.info(
            "ollama call: model=%s think=%s prompt_chars=%d timeout=%.0fs",
            self.model,
            think,
            prompt_chars,
            self.timeout_seconds,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            _logger.warning(
                "ollama timeout: model=%s think=%s after=%.0fs",
                self.model,
                think,
                self.timeout_seconds,
            )
            # Distinguish slow-inference from "Ollama is dead" — they
            # need very different fixes (raise the timeout vs. start
            # the daemon / switch model / disable thinking). The
            # thinking-enabled hint here uses the *call-site's* think
            # value, not self.think, because complete_with_thinking()
            # ignores the instance default.
            think_hint = (
                " Thinking mode is currently ENABLED — that alone often "
                "adds 60-180s on local hardware. If you don't actually "
                "need the reasoning trace, fall back to plain "
                "OllamaQwenClient.complete() (which respects "
                "PLANMYAGENTS_QWEN_THINK=false)."
                if think
                else ""
            )
            raise LocalQwenPlannerError(
                f"Local Qwen request to `{self.model}` timed out after "
                f"{self.timeout_seconds:.0f}s. The model is loaded but "
                f"inference is too slow for this prompt.{think_hint} "
                "Other options: switch to a smaller model "
                "(e.g. PLANMYAGENTS_QWEN_MODEL=qwen2.5:7b-instruct), "
                "raise PLANMYAGENTS_QWEN_TIMEOUT_SECONDS, or enable the "
                "Groq escalation tier "
                "(PLANMYAGENTS_PLANNER=escalating + GROQ_API_KEY)."
            ) from exc
        except error.HTTPError as exc:
            detail = ""
            try:
                raw_detail = exc.read().decode("utf-8", errors="replace").strip()
                if raw_detail:
                    parsed_detail = json.loads(raw_detail)
                    if isinstance(parsed_detail, dict):
                        detail = str(parsed_detail.get("error") or raw_detail)
                    else:
                        detail = raw_detail
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                detail = str(exc.reason or exc)
            lowered = detail.lower()
            if exc.code == 404 and "not found" in lowered:
                raise LocalQwenPlannerError(
                    f"Ollama model `{self.model}` is not installed "
                    f"({detail or 'model not found'}). Run "
                    f"`ollama pull {self.model}`, then "
                    f"`ollama list | grep {self.model}` to verify."
                ) from exc
            raise LocalQwenPlannerError(
                f"Ollama returned HTTP {exc.code} for `{self.model}` at "
                f"{self.url} ({detail or exc}). Check `ollama serve` and "
                f"`ollama logs`."
            ) from exc
        except (error.URLError, OSError) as exc:
            raise LocalQwenPlannerError(
                f"Local Qwen planner cannot reach Ollama at {self.url} "
                f"({type(exc).__name__}: {exc}). Run `ollama serve`, then "
                f"verify the model with `ollama list | grep {self.model}` "
                f"(pull with `ollama pull {self.model}` if missing)."
            ) from exc
        except json.JSONDecodeError as exc:
            raise LocalQwenPlannerError(
                f"Local Qwen returned a non-JSON response from `{self.model}`. "
                "This usually means the model produced malformed output for "
                "this prompt; retrying or switching models often fixes it."
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        # Pull useful per-call fields out of the Ollama response if
        # they're present so the operator log shows real throughput,
        # not just wall-clock. `eval_count` is the number of generated
        # tokens; `prompt_eval_count` is the prompt tokens the model
        # actually processed (may differ from our char-based estimate
        # for non-ASCII inputs).
        eval_count = body.get("eval_count") if isinstance(body, dict) else None
        prompt_eval_count = (
            body.get("prompt_eval_count") if isinstance(body, dict) else None
        )
        _logger.info(
            "ollama done: model=%s think=%s duration_ms=%d "
            "prompt_tokens=%s output_tokens=%s",
            self.model,
            think,
            duration_ms,
            prompt_eval_count,
            eval_count,
        )
        return body, duration_ms


def plan_goal_with_local_qwen(
    goal: str,
    registry: dict[str, Any],
    *,
    client: ChatClient | None = None,
) -> GoalPlan:
    """Plan a goal with local Qwen, then validate it against the registry.

    The flow is:
      1. Build messages (system prompt + goal + registry view + schema).
      2. Call the chat client.
      3. Parse JSON, validate against ``_PLAN_JSON_SCHEMA``.
      4. On validation failure, append the validator's error list to the
         conversation as a user turn and retry up to
         ``_PLANNER_VALIDATION_RETRIES`` times. After that, raise.
      5. On success, hand the validated payload to ``_plan_from_payload``
         which performs the final registry-membership check (open-world
         slugs without a registry entry become ``missing_capabilities``).
    """

    normalized = goal.strip()
    if not normalized:
        return _unsupported(
            summary="No goal was provided.",
            reasons=["Please describe the job you want PlanMyAgents to complete."],
            missing=[],
        )

    if client is None:
        from planmyagents_api.llm.escalating_client import (
            NoLlmTierAvailableError,
            build_default_escalating_client,
        )

        chat_client: ChatClient = build_default_escalating_client()
    else:
        chat_client = client

    messages = _build_messages(normalized, registry)
    last_error: str | None = None
    for attempt in range(_PLANNER_VALIDATION_RETRIES + 1):
        try:
            raw = chat_client.complete(messages)
        except Exception as exc:  # noqa: BLE001
            # Translate the escalating client's "no tier" sentinel into
            # the planner's own error type so existing callers
            # (web/planning.py, scripts/serve_demo.py) keep working
            # without a wider try/except. Re-raise everything else as
            # the same error type so the validation-retry loop above
            # cannot be silently bypassed by a transport-level fault.
            try:
                from planmyagents_api.llm.escalating_client import NoLlmTierAvailableError
            except Exception:  # pragma: no cover - defensive
                NoLlmTierAvailableError = ()  # type: ignore[assignment,misc]
            if isinstance(exc, NoLlmTierAvailableError):
                raise LocalQwenPlannerError(str(exc)) from exc
            raise

        try:
            payload = _parse_json_object(raw)
        except LocalQwenPlannerError as exc:
            last_error = str(exc)
            _logger.warning(
                "planner: attempt %d returned non-JSON content (%s); will %s",
                attempt + 1,
                last_error,
                "retry" if attempt < _PLANNER_VALIDATION_RETRIES else "fail",
            )
            messages = _append_validation_feedback(
                messages, raw, [f"Output was not valid JSON: {last_error}"]
            )
            continue

        errors = _validate_plan_payload(payload, _PLAN_JSON_SCHEMA)
        if not errors:
            return _plan_from_payload(payload, registry)

        last_error = "; ".join(errors)
        _logger.warning(
            "planner: attempt %d failed schema (%s); will %s",
            attempt + 1,
            last_error,
            "retry" if attempt < _PLANNER_VALIDATION_RETRIES else "fail",
        )
        messages = _append_validation_feedback(messages, raw, errors)

    raise LocalQwenPlannerError(
        "Planner produced output that failed JSON Schema validation after "
        f"{_PLANNER_VALIDATION_RETRIES + 1} attempts. Last error: {last_error}"
    )


def build_planner_messages(
    goal: str, registry: dict[str, Any]
) -> list[dict[str, str]]:
    """Public wrapper around the planner's prompt construction.

    Exposed so the ``/goal/explain`` route can build the *exact same*
    prompt that the planner uses, then re-run it with thinking enabled
    to capture the model's reasoning. Keeping this as a public name
    means the explain endpoint stays in lock-step with the planner
    prompt without each caller maintaining its own copy.
    """

    return _build_messages(goal, registry)


def _build_messages(goal: str, registry: dict[str, Any]) -> list[dict[str, str]]:
    registry_view = {
        "capabilities": registry.get("capabilities", []),
        "providers": [
            {
                "id": agent.get("id"),
                "display_name": agent.get("display_name"),
                "capabilities": [
                    {
                        "id": capability.get("id"),
                        "tier": capability.get("tier"),
                        "unit_cost_usd": capability.get("unit_cost_usd"),
                    }
                    for capability in agent.get("capabilities", [])
                ],
            }
            for agent in registry.get("agents", [])
            if agent.get("is_active", True)
        ],
        "discovered_candidates": [
            {
                "id": agent.get("id"),
                "display_name": agent.get("display_name"),
                "capabilities": [
                    capability.get("id") for capability in agent.get("capabilities", [])
                ],
                "lifecycle_status": agent.get("lifecycle_status"),
                "will_fail": agent.get("will_fail", True),
                "will_fail_reasons": agent.get("will_fail_reasons", []),
                "required_env_vars": agent.get("required_env_vars", []),
            }
            for agent in registry.get("discovered_agents", [])
        ],
    }

    system = _PLANNER_SYSTEM_PROMPT

    # Embed the JSON Schema directly in the user message rather than the
    # system prompt. Two reasons: (a) some local models weight the user
    # message more heavily and the schema *is* a constraint on the user
    # turn, not an identity statement; (b) putting it next to the goal +
    # registry keeps a single self-contained payload the operator can
    # paste into a debugger to reproduce planner behaviour.
    user = json.dumps(
        {
            "goal": goal,
            "registry": registry_view,
            "output_schema": _PLAN_JSON_SCHEMA,
        },
        indent=2,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LocalQwenPlannerError("Local Qwen did not return a JSON object.") from None
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LocalQwenPlannerError("Local Qwen returned malformed planner JSON.") from exc

    if not isinstance(parsed, dict):
        raise LocalQwenPlannerError("Local Qwen planner JSON must be an object.")
    return parsed


def _validate_plan_payload(
    payload: Any, schema: dict[str, Any], *, _path: str = "$"
) -> list[str]:
    """Validate ``payload`` against ``schema``; return a list of errors.

    Returns an empty list when the payload validates. We implement a tight
    Draft-07 subset (``type``, ``required``, ``properties``, ``items``,
    ``enum``, ``minLength``) plus the project-specific
    ``additional_required_when`` keyword used to require ``sub_tasks`` only
    when ``status == "executable"``.

    We deliberately don't pull in the ``jsonschema`` package: the schema is
    small, the project has zero existing schema-validation deps, and the
    inline rules below are easy to read in one screen. If we ever need a
    bigger schema we should swap in ``jsonschema`` rather than growing this
    function.
    """

    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(payload, dict):
            errors.append(f"{_path}: expected object, got {type(payload).__name__}")
            return errors
        for required_key in schema.get("required", []):
            if required_key not in payload:
                errors.append(f"{_path}: missing required key '{required_key}'")
        # Project-specific extension: conditional required keys.
        addl_required = schema.get("additional_required_when") or {}
        if isinstance(addl_required, dict):
            for trigger_value, keys in addl_required.items():
                if payload.get("status") == trigger_value:
                    for required_key in keys:
                        value = payload.get(required_key)
                        if value is None or (
                            isinstance(value, list) and len(value) == 0
                        ):
                            errors.append(
                                f"{_path}: when status='{trigger_value}', "
                                f"'{required_key}' must be present and non-empty"
                            )
        # Recurse into known properties only — extras are tolerated so the
        # LLM can return debug fields without blowing up validation.
        properties = schema.get("properties", {}) or {}
        for prop_name, prop_schema in properties.items():
            if prop_name in payload:
                errors.extend(
                    _validate_plan_payload(
                        payload[prop_name], prop_schema, _path=f"{_path}.{prop_name}"
                    )
                )
        return errors

    if expected_type == "array":
        if not isinstance(payload, list):
            errors.append(f"{_path}: expected array, got {type(payload).__name__}")
            return errors
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(payload):
                errors.extend(
                    _validate_plan_payload(
                        item, item_schema, _path=f"{_path}[{index}]"
                    )
                )
        return errors

    if expected_type == "string":
        if not isinstance(payload, str):
            errors.append(f"{_path}: expected string, got {type(payload).__name__}")
            return errors
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(payload) < min_length:
            errors.append(
                f"{_path}: string is shorter than minLength={min_length}"
            )
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and payload not in enum_values:
            errors.append(
                f"{_path}: value '{payload}' not in enum {enum_values}"
            )
        return errors

    return errors


def _append_validation_feedback(
    messages: list[dict[str, str]],
    bad_output: str,
    errors: list[str],
) -> list[dict[str, str]]:
    """Append a follow-up turn that asks the model to fix specific errors.

    Returning a new list (rather than mutating) keeps the caller's loop
    simple: every retry sees a strictly longer prompt. We include the
    model's previous output verbatim so it has the diff context, then the
    bullet list of validation errors, then a one-line instruction to emit
    a fresh JSON object only.
    """

    feedback_lines = [
        "Your previous response failed JSON Schema validation.",
        "",
        "Your previous output:",
        bad_output.strip(),
        "",
        "Validation errors:",
        *(f"  - {err}" for err in errors),
        "",
        "Reply with a single corrected JSON object that satisfies the schema. "
        "No prose. No markdown fences.",
    ]
    return [
        *messages,
        {"role": "user", "content": "\n".join(feedback_lines)},
    ]


def _plan_from_payload(payload: dict[str, Any], registry: dict[str, Any]) -> GoalPlan:
    """Parse and validate the planner's JSON output against the registry.

    Capability-id matching is intentionally a *soft* match: the planner
    emits open-world snake_case slugs (any descriptive name), and we
    resolve each one to the closest registry id via
    ``CapabilityIndex.match_slug``. This lets a planner emission like
    ``email_verify`` land as the registry's ``email_verification``
    instead of being marked missing — without us hardcoding a synonym
    table. Slugs that have no registry neighbour above the similarity
    threshold flow into ``missing_capabilities`` and become honest
    discovery targets.
    """

    # Lazy import keeps the planner module decoupled from the discovery
    # package boundary at import time (avoids a cycle if discovery code
    # ever imports from the planner). The factory is cache-backed so
    # this is cheap on the hot path.
    from planmyagents_api.discovery.capability_index import get_capability_index

    registry_capability_ids = [
        str(capability) for capability in registry.get("capabilities", [])
    ]
    supported_capabilities = {cap for cap in registry_capability_ids if cap}
    capability_index = (
        get_capability_index(registry_capability_ids)
        if supported_capabilities
        else None
    )

    def _resolve_slug(slug: str) -> str | None:
        """Return a registry slug if ``slug`` matches one (exact or
        embedding-similar), else None — the universal entry point for
        planner-slug → registry-id mapping inside this function."""

        if not slug:
            return None
        if slug in supported_capabilities:
            return slug
        if capability_index is None:
            return None
        return capability_index.match_slug(slug)

    status = str(payload.get("status", "unsupported")).lower()
    summary = str(payload.get("summary") or "Local Qwen produced a planner response.")
    refusal_reasons = _string_list(payload.get("refusal_reasons"))
    raw_missing_capabilities = _string_list(payload.get("missing_capabilities"))

    if status not in {"executable", "unsupported"}:
        return _unsupported(
            summary="Local Qwen returned an invalid planner status.",
            reasons=[f"Unexpected status: {status}"],
            missing=[],
        )

    sub_tasks_payload = payload.get("sub_tasks", [])
    if not isinstance(sub_tasks_payload, list):
        return _unsupported(
            summary="Local Qwen returned malformed sub_tasks.",
            reasons=["Planner output `sub_tasks` must be a list."],
            missing=[],
        )

    sub_tasks: list[PlannedSubTask] = []
    unknown_executable_capabilities: list[str] = []
    unresolved_references: list[str] = []
    for item in sub_tasks_payload:
        if not isinstance(item, dict):
            continue
        emitted_capability = str(item.get("capability", "")).strip()
        if not emitted_capability:
            continue
        resolved_capability = _resolve_slug(emitted_capability)
        if resolved_capability is None:
            unknown_executable_capabilities.append(emitted_capability)
            continue
        description = str(item.get("description") or f"Run {resolved_capability}")
        inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
        if _contains_unresolved_reference(inputs):
            unresolved_references.append(resolved_capability)
            continue
        sub_tasks.extend(
            _expand_sub_tasks(
                capability=resolved_capability, description=description, inputs=inputs
            )
        )

    if unknown_executable_capabilities:
        return _unsupported(
            summary="Local Qwen planned capabilities outside the registry.",
            reasons=[
                "Planner output was rejected because executable capabilities must exist in the registry."
            ],
            missing=unknown_executable_capabilities,
        )

    if unresolved_references:
        return _unsupported(
            summary="Local Qwen produced a plan with unresolved dependency placeholders.",
            reasons=[
                (
                    "Planner output referenced upstream task results, but dependency-aware "
                    "execution is not implemented in the current runtime."
                )
            ],
            missing=["dependency_resolution"],
        )

    if status == "executable":
        if not sub_tasks:
            return _unsupported(
                summary="Local Qwen did not produce any executable sub-tasks.",
                reasons=["Planner returned status executable without registered sub_tasks."],
                missing=[],
            )
        return GoalPlan(status="executable", summary=summary, sub_tasks=sub_tasks)

    if not refusal_reasons:
        refusal_reasons = [
            "Local Qwen determined this goal is not executable with the current registry."
        ]
    # On the unsupported path, we keep the LLM-emitted missing slugs
    # *verbatim* rather than rewriting them through the resolver. The
    # discovery system needs the open-world slug as a search target;
    # mapping `crypto_offramp` to a near-but-wrong registry slug would
    # cause discovery to skip the missing capability and would mask the
    # real gap from operators reading logs and the leaderboard.
    return _unsupported(
        summary=summary,
        reasons=refusal_reasons,
        missing=raw_missing_capabilities,
    )


def _unsupported(summary: str, reasons: list[str], missing: list[str]) -> GoalPlan:
    return GoalPlan(
        status="unsupported",
        summary=summary,
        refusal_reasons=reasons,
        missing_capabilities=sorted(set(missing)),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _expand_sub_tasks(
    capability: str, description: str, inputs: dict[str, Any]
) -> list[PlannedSubTask]:
    if capability != "email_verification":
        return [PlannedSubTask(capability=capability, description=description, inputs=inputs)]

    emails = inputs.get("emails")
    if isinstance(emails, list):
        base_inputs = {key: value for key, value in inputs.items() if key != "emails"}
        return [
            PlannedSubTask(
                capability=capability,
                description=f"{description}: {email}",
                inputs={**base_inputs, "email": str(email).strip()},
            )
            for email in emails
            if str(email).strip()
        ]

    return [PlannedSubTask(capability=capability, description=description, inputs=inputs)]


def _contains_unresolved_reference(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.startswith("from_") or lowered.endswith("_results") or "from_" in lowered
    if isinstance(value, list):
        return any(_contains_unresolved_reference(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_unresolved_reference(item) for item in value.values())
    return False

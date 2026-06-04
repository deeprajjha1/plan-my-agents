"""Registry-aware endpoint + auth lookup for recipe exporters.

Recipe exporters (``n8n_json``, ``cli``) need to know, for each
recommended provider, the authoritative HTTP method, path, and auth
header so the emitted workflow / curl actually authenticates against
the real vendor. Before this module landed (Phase 1 / B1-B4 fixes
from ``docs/manual-test-log.md``), the exporters guessed:

* URL by string-replacing underscores in the capability slug, which
  produced things like ``/contact-enrichment`` instead of Apollo's
  real ``/v1/people/match``.
* Method by hard-coding ``GET``, which broke Apollo, Razorpay,
  Resend, and every other ``POST`` endpoint.
* Auth header by chopping the env-var name, which produced
  ``Authorization`` for Apollo (which actually uses ``X-Api-Key``).
* CLI scheme by always prefixing ``Bearer ``, even for Apollo
  (api-key header) and Razorpay (basic auth).

This module is the single source of truth for endpoint + auth shape.
It reads ``packages/registry/agents.json`` once (LRU-cached) and
returns ``EndpointInfo`` for any registered ``(provider_id,
capability)`` pair. Unknown pairs return ``None`` so the renderers can
fall back to their pre-existing heuristic (which is what synthetic
test fixtures rely on).

Firewall note: this is data-only. It reads the public registry catalog
the user already sees on the website. It does NOT import from
``planmyagents_api.benchmark.baselines`` or
``planmyagents_api.agents.router``, so it does not violate the
recipe-exporter firewall invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

REGISTRY_PATH = (
    Path(__file__).resolve().parents[5] / "packages" / "registry" / "agents.json"
)


@dataclass(frozen=True)
class EndpointInfo:
    """Authoritative endpoint + auth shape for one provider/capability."""

    method: str
    path: str  # already starts with "/"
    header_name: str
    # bearer | header | basic | query | none
    # ``basic`` covers Razorpay-style basic auth (KEY_ID + KEY_SECRET).
    # ``header`` covers any non-Bearer custom header (Apollo X-Api-Key,
    # Hunter api-key query → mapped to ``query`` separately).
    scheme: str
    env_var: str


@cache
def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@cache
def _build_index() -> dict[tuple[str, str], EndpointInfo]:
    """Build {(provider_id, capability_id): EndpointInfo}.

    Cached at import-time on first call; the registry is a versioned
    file that only changes between releases, so we never need to
    invalidate during a single process lifetime.
    """
    registry = _load_registry()
    index: dict[tuple[str, str], EndpointInfo] = {}
    for agent in registry.get("agents", []):
        if not isinstance(agent, dict):
            continue
        provider_id = str(agent.get("id") or "").strip()
        if not provider_id:
            continue
        auth = agent.get("auth") if isinstance(agent.get("auth"), dict) else {}
        auth_type = str(auth.get("type") or "").strip().lower()
        header_name, scheme = _normalise_auth(auth_type, auth)
        env_var = str(auth.get("env_var") or "").strip()
        for capability in agent.get("capabilities", []):
            if not isinstance(capability, dict):
                continue
            cap_id = str(capability.get("id") or "").strip()
            if not cap_id:
                continue
            method, path = _parse_endpoint(capability.get("endpoint"))
            if not method or not path:
                continue
            index[(provider_id, cap_id)] = EndpointInfo(
                method=method,
                path=path,
                header_name=header_name,
                scheme=scheme,
                env_var=env_var,
            )
    return index


def _parse_endpoint(raw: Any) -> tuple[str, str]:
    """Parse ``"POST /v1/orders"`` into ``("POST", "/v1/orders")``.

    Tolerates extra whitespace and missing leading slash. Returns
    ``("", "")`` for anything we can't confidently parse so the
    fallback path in the renderer fires.
    """
    if not isinstance(raw, str):
        return ("", "")
    tokens = raw.strip().split(None, 1)
    if len(tokens) != 2:
        return ("", "")
    method = tokens[0].strip().upper()
    path = tokens[1].strip()
    if not path.startswith("/"):
        path = "/" + path
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        return ("", "")
    return (method, path)


def _normalise_auth(auth_type: str, auth: dict[str, Any]) -> tuple[str, str]:
    """Map registry ``auth.type`` to ``(header_name, scheme)``.

    Schemes:
    * ``bearer`` — header ``Authorization: Bearer <token>``
    * ``header`` — header ``<header_name>: <token>`` (no prefix)
    * ``basic``  — basic auth via ``-u``; header name unused
    * ``query``  — query param; header name unused (renderer prints note)
    * ``none``   — no auth
    """
    explicit_header = str(auth.get("header_name") or "").strip()
    if auth_type == "bearer":
        return ("Authorization", "bearer")
    if auth_type == "basic":
        return ("", "basic")
    # Hunter and a few others use ``query_param`` with ``param_name``;
    # treat both as query-string auth so the renderer prints a note
    # instead of guessing a header that does not exist.
    if auth_type in {"query", "query_param"}:
        return ("", "query")
    if auth_type in {"header", "api_key", "apikey"} or explicit_header:
        return (explicit_header or "Authorization", "header")
    return ("", "none")


def lookup_endpoint(provider_id: str, capability: str) -> EndpointInfo | None:
    """Return the authoritative endpoint+auth for a provider/capability pair.

    Returns ``None`` when the pair is not in
    ``packages/registry/agents.json`` (e.g. discovered candidates,
    synthetic test fixtures). Callers fall back to their pre-existing
    heuristic in that case.
    """
    if not provider_id or not capability:
        return None
    return _build_index().get((provider_id.strip(), capability.strip()))

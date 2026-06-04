"""Loader for ``packages/registry/capability_descriptions.json``.

Why this module exists
----------------------
The goal decomposer's catalog_hint used to be a flat list of capability
slug strings like ``["calendar_management", "code_review", ...,
"web_search"]``. With nothing else to go on, the LLM cannot tell that
``web_search`` is the right reuse target for a sub-task like "find gift
ideas for a 3-year-old" — the slug name alone is too ambiguous. The
decomposer therefore coined fresh synonymous slugs on every request
(``gift_suggestion``, ``ecommerce_checkout``, ``logistics_delivery``,
...) which scouts could not match and the router could not execute,
producing the "0 candidates" symptom we kept seeing on gift / shopping
style goals.

The fix is to give the decomposer per-slug *descriptions* and *example
sub-tasks* so it can make an informed reuse decision. The descriptions
live in ``packages/registry/capability_descriptions.json`` (versioned
alongside ``agents.json`` and hand-curated) and this module loads them
into a stable, validated in-memory shape.

Contract for callers
--------------------
* :func:`load_capability_descriptions` returns the raw mapping
  ``{slug: {description, examples}}`` exactly as the JSON ships, so the
  decomposer prompt embeds the same structure a human contributor sees
  in the file. No silent reformatting; no synonyms; no shimming.
* The loader is cached via ``functools.lru_cache``. Tests that need a
  custom file (e.g. to exercise a missing-file failure mode) MUST call
  :func:`load_capability_descriptions.cache_clear` between cases.
* Failures degrade to an empty mapping AND log a single warning. The
  decomposer treats "no descriptions available" the same as the legacy
  flat-list catalog_hint — slightly worse reuse rate, but the request
  still completes. We intentionally do NOT raise: a missing or
  malformed descriptions file must never break ``/goal``.

Why we don't merge this into ``registry/loader.py``
---------------------------------------------------
``registry/loader.py`` validates the full provider registry shape
(agents, capabilities, auth blocks). Adding a soft, optional
descriptions field there would either weaken that loader's strict
contract or require every consumer to opt out. Keeping descriptions in
a sibling module lets the strict loader stay strict and lets the
decomposer fail soft on description issues.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Default location resolved relative to the repo root. The repo layout
# is ``apps/api/planmyagents_api/registry/`` for this file and
# ``packages/registry/`` for the data file, so we walk up 4 levels.
# Resolving in code (not at import time) keeps the path lookup cheap
# under ``lru_cache`` AND keeps the module importable from test
# harnesses that monkeypatch the path.
_DEFAULT_RELATIVE_PATH = Path("packages/registry/capability_descriptions.json")


def _default_path() -> Path:
    """Return the absolute default path to ``capability_descriptions.json``.

    Lives in a function (not a module-level constant) so test code can
    monkeypatch ``Path.cwd`` if it ever needs to relocate the registry
    root without re-importing this module."""

    return Path(__file__).resolve().parents[4] / _DEFAULT_RELATIVE_PATH


@lru_cache(maxsize=4)
def load_capability_descriptions(
    path: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load the descriptions JSON and return ``{slug: {description, examples}}``.

    Args:
        path: Optional override path. When ``None`` (production), uses
            :func:`_default_path`. Tests pass a tmp_path string to
            exercise alternate files; the lru_cache keys on the
            argument so each unique path gets its own cached parse.

    Returns:
        A mapping of slug to a dict with two keys:

        * ``description`` — one or two sentences describing the
          capability, suitable for inclusion in an LLM prompt verbatim.
        * ``examples`` — a list of 2-5 short example sub-tasks the
          capability covers. The LLM uses these as analogical anchors;
          they're higher-signal than the description alone.

        Returns an empty dict (and logs a warning) on any failure:
        missing file, JSON parse error, malformed shape, etc. The
        decomposer treats an empty mapping as "no descriptions, fall
        back to flat-list hint" which is its previous behaviour.

    The function does NOT validate against the JSON schema. Schema
    validation belongs in CI / pre-commit, not on the request hot path
    — every ``/goal`` call would otherwise pay schema-load + jsonschema
    walk costs for a file that changes once a sprint.
    """

    resolved = Path(path) if path else _default_path()
    try:
        raw_text = resolved.read_text()
    except FileNotFoundError:
        LOGGER.warning(
            "capability_descriptions: file not found at %s; "
            "decomposer will fall back to flat-list catalog hint",
            resolved,
        )
        return {}
    except OSError as exc:
        LOGGER.warning(
            "capability_descriptions: could not read %s: %s; "
            "decomposer will fall back to flat-list catalog hint",
            resolved,
            exc,
        )
        return {}

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        LOGGER.warning(
            "capability_descriptions: malformed JSON at %s: %s; "
            "decomposer will fall back to flat-list catalog hint",
            resolved,
            exc,
        )
        return {}

    capabilities = payload.get("capabilities") if isinstance(payload, dict) else None
    if not isinstance(capabilities, dict):
        LOGGER.warning(
            "capability_descriptions: top-level `capabilities` key missing "
            "or non-dict at %s; falling back to flat-list catalog hint",
            resolved,
        )
        return {}

    result: dict[str, dict[str, Any]] = {}
    for slug, entry in capabilities.items():
        if not isinstance(slug, str) or not slug.strip():
            continue
        if not isinstance(entry, dict):
            LOGGER.warning(
                "capability_descriptions: skipping non-dict entry for %r in %s",
                slug,
                resolved,
            )
            continue
        description = entry.get("description")
        examples = entry.get("examples")
        # Both fields are required by the schema; per-entry validation
        # here is defence in depth so a partially-edited file still
        # surfaces *some* descriptions instead of failing all of them.
        if not isinstance(description, str) or not description.strip():
            LOGGER.warning(
                "capability_descriptions: entry %r missing description in %s; skipping",
                slug,
                resolved,
            )
            continue
        if not isinstance(examples, list) or not examples:
            LOGGER.warning(
                "capability_descriptions: entry %r missing examples in %s; skipping",
                slug,
                resolved,
            )
            continue
        cleaned_examples = [
            str(example).strip()
            for example in examples
            if isinstance(example, str) and example.strip()
        ]
        if not cleaned_examples:
            LOGGER.warning(
                "capability_descriptions: entry %r had no non-empty examples in %s; skipping",
                slug,
                resolved,
            )
            continue
        result[slug.strip()] = {
            "description": description.strip(),
            "examples": cleaned_examples,
        }
    return result


def describe_capabilities(
    slugs: list[str],
    *,
    path: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return descriptions for ONLY the supplied slugs, preserving slug order.

    Used by the decomposer to filter the global descriptions map down to
    the slugs the current request's router actually supports. Returning
    a slug-ordered dict matters because the decomposer's prompt
    serialises entries in iteration order — keeping that stable means
    prompt outputs are reproducible across requests with the same slug
    set, which makes cache keys (in any future prompt-cache layer) work
    correctly.

    Slugs not present in the descriptions file are silently dropped.
    Callers that want to know which slugs were missing should diff
    ``set(slugs) - set(result)`` after the call.
    """

    all_descriptions = load_capability_descriptions(path=path)
    if not all_descriptions:
        return {}
    return {
        slug: all_descriptions[slug]
        for slug in slugs
        if slug in all_descriptions
    }

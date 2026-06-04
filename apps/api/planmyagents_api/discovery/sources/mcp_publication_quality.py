"""Shared junk-filter for MCP-aggregator discovery sources.

The official MCP registry, MCP Marketplace, and Smithery all suffer from
the same class of low-quality publications: school-project test forks,
repeatedly-bumped deploy entries, and obvious "*-copy" / "*-fork" /
"*-test" suffixes that the LLM ``CandidateJudge`` would catch later but
shouldn't be allowed to clutter the index in the first place.

This module centralises the cheap-regex pre-filter that historically
lived inside ``official_mcp_registry.py``. Extracted so the new
``mcp_marketplace.py`` and ``smithery.py`` sources can apply exactly the
same rules without copy-paste drift, and so the test cases and rationale
have one canonical home.

Three signal classes, kept separate so a future rule-tweak only touches
one of them:

1. **Token signals** — the repo segment, when tokenised on
   ``-`` / ``_`` / ``.``, contains a token that's *unmistakably* a
   test/demo/school/tutorial marker. Tokenising avoids the classic
   false-positive where ``BigVik193-reddit-ads-mcp-api`` gets flagged
   because "193" is 3 digits — substring matching flagged too many
   legitimate usernames + protocol references (e.g. "x402"). With
   tokens we only flag when the marker is its own word.

2. **Numeric-suffix signals** — full-name substrings that are obvious
   "I bumped the test until it deployed" tells:

   * 6+ consecutive digits (``project123123123``)
   * 4+ repeated digit (``1111``, ``00000``)

   Conservative on purpose. Ordinary numeric suffixes in usernames
   (3-5 digits) are *allowed* because they're too often legitimate
   (``BigVik193``, ``Lattiq x402``).

3. **Suffix signals** — name ends in ``-copy`` / ``-fork`` /
   ``-clone`` / ``-vN`` / ``-test*``. These are unambiguous fork
   markers.

The LLM ``CandidateJudge`` runs at retrieval time and is expected to
catch anything this misses, so it's correct for these rules to lean
toward under-rejection (false negatives are recoverable; false
positives are not — a wrongly-rejected entry never enters the index).
"""

from __future__ import annotations

import re

# Token signals: must appear as a standalone token after splitting on
# ``-`` / ``_`` / ``.`` / ``/``. Multi-segment markers (containing
# ``-`` or ``_``) bypass the tokenizer and match as substrings against
# the repo segment instead — see ``is_obvious_junk`` below.
JUNK_TOKENS: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "demo",
        "demos",
        "sandbox",
        "example",
        "examples",
        "scratch",
        "playground",
        "tutorial",
        "tutorials",
        "school",
        "class",
        "homework",
        "assignment",
        "exercise",
        "hw1",
        "hw2",
        "hw3",
        "hw4",
        "hw5",
        # Common auto-generated test prefixes Smithery emits
        "py-test",
        "ts-test",
        "test_m",
    }
)

JUNK_NUMERIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{6,}"),
    re.compile(r"(\d)\1{3,}"),
)

JUNK_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-(copy|fork|clone|v\d+)$", re.IGNORECASE),
    # "-test", "-test-N", "-test_N" suffix on the repo segment
    re.compile(r"[-_]test([-_]?\w*)?$", re.IGNORECASE),
)


def is_obvious_junk(name: str) -> bool:
    """Cheap regex pre-filter for clearly non-production registry entries.

    Token-aware: tokenises the repo segment on ``-`` / ``_`` / ``.``
    and rejects when any token is a known test/demo/school marker.
    Falls back to numeric-suffix regex (6+ consecutive digits or 4+
    repeated digit) for the ``...project123123123`` shape and to suffix
    regex for ``-copy``/``-fork``/``-vN``/``-test*``.

    An empty / falsy ``name`` is considered junk so callers can use this
    as a single guard without a separate length check.
    """

    if not name:
        return True
    name_lower = name.lower()
    repo_segment = name.split("/")[-1] if "/" in name else name
    repo_lower = repo_segment.lower()

    # Token signal: split *both* the namespace and repo segment into
    # atomic words and check each against the junk-token set. Splits
    # only on non-alphanumeric chars (dash / underscore / slash / dot)
    # so "hw3" survives as a single token (matches the
    # {"hw1"..."hw5"} markers) while "py-test-0" yields
    # {"py", "test", "0"}. CamelCase boundaries are not split here on
    # purpose — usernames like "BowenXU" should not contribute the
    # word "x" to the token set.
    tokens = {t for t in re.split(r"[^a-z0-9]+", name_lower) if t}
    if tokens & JUNK_TOKENS:
        return True
    # Multi-segment markers like "py-test" / "ts-test" / "test_m" are
    # tested by direct substring against the repo segment (the
    # tokeniser above would split them too aggressively).
    multiword_markers = {t for t in JUNK_TOKENS if "-" in t or "_" in t}
    for marker in multiword_markers:
        if marker in repo_lower:
            return True

    for pattern in JUNK_NUMERIC_PATTERNS:
        if pattern.search(repo_lower):
            return True
    for pattern in JUNK_SUFFIX_PATTERNS:
        if pattern.search(repo_lower):
            return True
    return False

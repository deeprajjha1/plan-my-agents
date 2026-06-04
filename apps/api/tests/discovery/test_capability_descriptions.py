"""Unit tests for the capability-descriptions loader.

The descriptions feed the decomposer's catalog_hint and are the single
mechanism that prevents the LLM from coining synonyms for
router-supported slugs (the bug that made gift-buying goals discover
zero agents). These tests pin:

* The shipped JSON file is valid AND covers every router-supported
  capability in ``agents.json``. If someone adds a capability to the
  registry without a matching description entry, the failing test
  here points them at the gap before the decomposer regresses to the
  legacy synonym-coining behaviour at runtime.
* The loader degrades softly on missing / malformed files — a typo'd
  description file must NEVER break ``/goal``.
* The slug-filtering helper preserves order and silently drops
  unknown slugs (the decomposer is the only legitimate consumer of
  this helper and treats unknown slugs as "no description available").
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.registry.capability_descriptions import (
    describe_capabilities,
    load_capability_descriptions,
)


def _registry_capability_ids() -> set[str]:
    """Capabilities declared by ``packages/registry/agents.json``."""
    payload = json.loads((ROOT / "packages/registry/agents.json").read_text())
    return set(payload["capabilities"])


class ShippedDescriptionsTests(unittest.TestCase):
    """The shipped JSON file is the contract the decomposer reads on
    every request. If it's wrong, every request is wrong — so the
    invariants live in tests, not docs."""

    def setUp(self) -> None:
        load_capability_descriptions.cache_clear()

    def test_loads_every_router_supported_capability(self) -> None:
        descriptions = load_capability_descriptions()
        missing = _registry_capability_ids() - set(descriptions)
        self.assertEqual(
            set(),
            missing,
            "agents.json declares capabilities with no entry in "
            "capability_descriptions.json — the decomposer will coin "
            "synonyms for these slugs and discovery scouts won't match. "
            f"Add entries for: {sorted(missing)}",
        )

    def test_no_extra_descriptions_for_unknown_slugs(self) -> None:
        """An extra description for a slug NOT in the registry is a
        sign the description file or the registry drifted apart. The
        loader will happily surface the extra entry to the LLM, which
        will then reuse a slug the router can't execute. This test
        catches the drift before it ships."""
        descriptions = load_capability_descriptions()
        extras = set(descriptions) - _registry_capability_ids()
        self.assertEqual(
            set(),
            extras,
            "capability_descriptions.json contains slugs not in "
            f"agents.json: {sorted(extras)}. Remove them or add them "
            "to agents.json.",
        )

    def test_every_entry_has_description_and_examples(self) -> None:
        descriptions = load_capability_descriptions()
        for slug, entry in descriptions.items():
            self.assertTrue(
                entry["description"].strip(),
                f"capability {slug!r} has empty description",
            )
            self.assertGreaterEqual(
                len(entry["examples"]),
                2,
                f"capability {slug!r} has fewer than 2 examples — the LLM "
                "uses examples as analogical anchors and one example is "
                "rarely enough for confident reuse",
            )

    def test_critical_shopping_slugs_have_canonical_reuse_phrases(self) -> None:
        """Regression-pin the phrases the prompt-rule mentions by name
        for the shopping-style goals that motivated this whole fix. If
        a future contributor rewrites these descriptions in a way that
        drops the "buy / purchase" or "compare prices" cues, the LLM
        will silently lose the cross-reference between the prompt-rule
        examples and the descriptions, and we'll regress to "0 agents
        discovered" for gift-buying goals.

        Asserting on literal phrases here, not semantic equivalents,
        because the LLM matches lexically against the prompt
        examples — a synonym in the description doesn't help."""
        descriptions = load_capability_descriptions()
        cases = {
            "web_search": "gift",  # the most failure-mode-shaped example
            "price_comparison": "compare",
            "payment_authorization": "checkout",
            "shipping_quote": "shipping",
            "store_locator": "stor",  # store / stores
        }
        for slug, phrase in cases.items():
            entry = descriptions[slug]
            combined = " ".join(
                [entry["description"], *entry["examples"]]
            ).lower()
            self.assertIn(
                phrase,
                combined,
                f"capability {slug!r} no longer mentions {phrase!r} — "
                "the prompt's example-mapping rule relies on this phrase "
                "to teach the LLM that 'buy / compare / pay / ship / store-find' "
                "sub-tasks should reuse this slug, not coin a synonym",
            )


class LoaderRobustnessTests(unittest.TestCase):
    """The loader must NEVER break ``/goal``. Tests for the
    degradation behaviour on bad input."""

    def setUp(self) -> None:
        load_capability_descriptions.cache_clear()

    def tearDown(self) -> None:
        load_capability_descriptions.cache_clear()

    def _write_tmp(self, contents: str) -> Path:
        import tempfile
        # NamedTemporaryFile is a context manager that returns an open
        # file handle; we want the path on disk, not the handle, so we
        # close immediately and re-open via write_text. Without the
        # close() we leak a Python 3.14 ResourceWarning on every test.
        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp = Path(handle.name)
        handle.close()
        tmp.write_text(contents)
        self.addCleanup(tmp.unlink, missing_ok=True)
        return tmp

    def test_missing_file_returns_empty_dict(self) -> None:
        """Missing file → empty dict → decomposer falls back to legacy
        flat-list hint. NEVER raise."""
        nonexistent = self._write_tmp("{}")
        nonexistent.unlink()  # delete after creation so the path is missing
        self.assertEqual({}, load_capability_descriptions(path=str(nonexistent)))

    def test_invalid_json_returns_empty_dict(self) -> None:
        tmp = self._write_tmp("this is not json {")
        self.assertEqual({}, load_capability_descriptions(path=str(tmp)))

    def test_missing_capabilities_key_returns_empty_dict(self) -> None:
        tmp = self._write_tmp(json.dumps({"version": "0.0.1"}))
        self.assertEqual({}, load_capability_descriptions(path=str(tmp)))

    def test_non_dict_top_level_returns_empty_dict(self) -> None:
        tmp = self._write_tmp(json.dumps(["not", "an", "object"]))
        self.assertEqual({}, load_capability_descriptions(path=str(tmp)))

    def test_skips_entries_missing_description_keeps_others(self) -> None:
        """Partial corruption: one entry is broken, others survive.
        Defensive parsing — the decomposer still gets a useful
        catalog_hint."""
        tmp = self._write_tmp(json.dumps({
            "capabilities": {
                "good_one": {
                    "description": "a real capability",
                    "examples": ["do a thing", "do another thing"],
                },
                "missing_desc": {"examples": ["x", "y"]},
                "empty_desc": {"description": "  ", "examples": ["x", "y"]},
                "missing_examples": {"description": "something"},
                "empty_examples": {"description": "something", "examples": []},
                "junk_examples": {"description": "something", "examples": [42, None]},
            }
        }))
        descriptions = load_capability_descriptions(path=str(tmp))
        self.assertEqual({"good_one"}, set(descriptions))

    def test_strips_whitespace_in_description_and_examples(self) -> None:
        tmp = self._write_tmp(json.dumps({
            "capabilities": {
                "trimmed": {
                    "description": "  trimmed description  ",
                    "examples": ["  ex 1  ", " ex 2"],
                },
            }
        }))
        descriptions = load_capability_descriptions(path=str(tmp))
        self.assertEqual("trimmed description", descriptions["trimmed"]["description"])
        self.assertEqual(["ex 1", "ex 2"], descriptions["trimmed"]["examples"])


class DescribeCapabilitiesHelperTests(unittest.TestCase):
    """The slug-filter helper is the decomposer's actual entry point;
    its behaviour matters more than the loader's raw output shape."""

    def setUp(self) -> None:
        load_capability_descriptions.cache_clear()

    def test_returns_only_requested_slugs(self) -> None:
        result = describe_capabilities(["web_search", "payment_authorization"])
        self.assertEqual({"web_search", "payment_authorization"}, set(result))

    def test_preserves_input_order(self) -> None:
        """Stable iteration order is a soft contract for any future
        prompt-cache layer. Pinning it here so a dict-ordering refactor
        doesn't silently break cache hit rates."""
        ordered = describe_capabilities(
            ["payment_authorization", "web_search", "shipping_quote"]
        )
        self.assertEqual(
            ["payment_authorization", "web_search", "shipping_quote"],
            list(ordered),
        )

    def test_silently_drops_unknown_slugs(self) -> None:
        """Unknown slug = "no description available" = LLM falls back
        to the secondary pool. Same contract as missing-file."""
        result = describe_capabilities(["web_search", "totally_unknown_slug"])
        self.assertEqual({"web_search"}, set(result))

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual({}, describe_capabilities([]))


if __name__ == "__main__":
    unittest.main()

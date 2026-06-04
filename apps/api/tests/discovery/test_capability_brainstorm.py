"""Tests for the capability brainstorm pass."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.capability_brainstorm import (
    _parse_capabilities,
    brainstorm_capabilities,
)


class _StubChat:
    def __init__(self, completion: str = "{}", *, raise_on_call: bool = False) -> None:
        self.completion = completion
        self.raise_on_call = raise_on_call
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self.raise_on_call:
            raise RuntimeError("stub failure")
        return self.completion


class ParseCapabilitiesTests(unittest.TestCase):
    def test_returns_empty_for_garbage(self) -> None:
        self.assertEqual(_parse_capabilities("", max_proposed=5), [])
        self.assertEqual(_parse_capabilities("not json", max_proposed=5), [])
        self.assertEqual(
            _parse_capabilities('{"new_capabilities": "not a list"}', max_proposed=5),
            [],
        )

    def test_filters_invalid_ids_and_dedupes(self) -> None:
        raw = json.dumps(
            {
                "new_capabilities": [
                    "kyc_aml_check",
                    "BAD",  # uppercase rejected
                    "ab",  # too short
                    "kyc_aml_check",  # dup
                    "stablecoin transfer",  # space → underscore → kept
                    "stablecoin-transfer",  # hyphen → underscore → dup
                    "x" * 80,  # too long
                ]
            }
        )
        out = _parse_capabilities(raw, max_proposed=10)
        self.assertEqual(out, ["kyc_aml_check", "stablecoin_transfer"])

    def test_caps_at_max_proposed(self) -> None:
        raw = json.dumps(
            {"new_capabilities": [f"cap_{i}" for i in range(20)]}
        )
        self.assertEqual(len(_parse_capabilities(raw, max_proposed=5)), 5)


class BrainstormCapabilitiesTests(unittest.TestCase):
    def test_skips_when_goal_is_empty(self) -> None:
        result = brainstorm_capabilities(
            goal="",
            catalog_capability_ids=[],
            existing_missing=[],
            chat_client=_StubChat(),
        )
        self.assertTrue(result.skipped)
        self.assertEqual(result.skip_reason, "empty_goal")

    def test_returns_skipped_on_chat_failure(self) -> None:
        result = brainstorm_capabilities(
            goal="buy a house",
            catalog_capability_ids=[],
            existing_missing=[],
            chat_client=_StubChat(raise_on_call=True),
        )
        self.assertTrue(result.skipped)
        self.assertTrue(result.skip_reason.startswith("chat_error:"))

    def test_drops_capabilities_already_in_catalog_or_existing(self) -> None:
        chat = _StubChat(
            completion=json.dumps(
                {
                    "new_capabilities": [
                        "kyc_aml_check",  # new
                        "semantic_search",  # in catalog → drop
                        "currency_conversion",  # in existing → drop
                        "escrow_management",  # new
                    ]
                }
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            persist = Path(tmp) / "demand.jsonl"
            result = brainstorm_capabilities(
                goal="buy a house in Dubai using crypto",
                catalog_capability_ids=["semantic_search"],
                existing_missing=["currency_conversion"],
                chat_client=chat,
                persist_path=persist,
            )

            self.assertEqual(
                result.proposed_capabilities,
                ["kyc_aml_check", "escrow_management"],
            )
            self.assertTrue(persist.exists())
            line = persist.read_text().strip()
            record = json.loads(line)
            self.assertEqual(
                record["capabilities"], ["kyc_aml_check", "escrow_management"]
            )
            self.assertEqual(record["source"], "planner_brainstorm")

    def test_does_not_persist_when_no_proposals(self) -> None:
        chat = _StubChat(completion=json.dumps({"new_capabilities": []}))
        with tempfile.TemporaryDirectory() as tmp:
            persist = Path(tmp) / "demand.jsonl"
            result = brainstorm_capabilities(
                goal="anything",
                catalog_capability_ids=[],
                existing_missing=[],
                chat_client=chat,
                persist_path=persist,
            )
            self.assertEqual(result.proposed_capabilities, [])
            self.assertFalse(persist.exists())


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()

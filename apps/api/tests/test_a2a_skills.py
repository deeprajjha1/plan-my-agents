"""A2A AgentCard parsing tests covering capabilities-vs-skills + docs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.discovery.sources.a2a import _candidate_from_agent_card


class A2ACardParsingTests(unittest.TestCase):
    def test_protocol_capabilities_dict_falls_back_to_skills(self) -> None:
        raw = _candidate_from_agent_card(
            {
                "name": "currency-agent",
                "url": "https://currency.example",
                "capabilities": {"streaming": True, "pushNotifications": False},
                "skills": [
                    {
                        "id": "convert",
                        "description": "Convert between currencies",
                        "examples": ["What is 100 USD in EUR?"],
                    }
                ],
            }
        )

        self.assertEqual(len(raw["capabilities"]), 1)
        self.assertEqual(raw["capabilities"][0]["id"], "convert")
        self.assertEqual(len(raw["skills"]), 1)
        self.assertEqual(raw["skills"][0]["description"], "Convert between currencies")
        self.assertEqual(
            raw["skills"][0]["examples"], ["What is 100 USD in EUR?"]
        )

    def test_string_skills_become_capabilities_and_skills(self) -> None:
        raw = _candidate_from_agent_card(
            {
                "name": "demo",
                "url": "https://demo.example",
                "skills": ["semantic_search", "summarisation"],
            }
        )
        self.assertEqual(
            sorted(c["id"] for c in raw["capabilities"]),
            ["semantic_search", "summarisation"],
        )
        self.assertEqual(
            [s["name"] for s in raw["skills"]],
            ["semantic_search", "summarisation"],
        )

    def test_legacy_capabilities_list_still_supported(self) -> None:
        raw = _candidate_from_agent_card(
            {
                "name": "legacy",
                "url": "https://legacy.example",
                "capabilities": ["foo", "bar"],
            }
        )
        self.assertEqual(
            sorted(c["id"] for c in raw["capabilities"]), ["bar", "foo"]
        )

    def test_authentication_block_is_extracted_into_docs(self) -> None:
        raw = _candidate_from_agent_card(
            {
                "name": "x",
                "url": "https://x.example",
                "documentationUrl": "https://x.example/docs",
                "authentication": {
                    "scheme": "oauth2",
                    "scopes": ["read", "write"],
                },
                "skills": ["x"],
            }
        )
        self.assertEqual(raw["docs"]["setup_url"], "https://x.example/docs")
        self.assertEqual(raw["docs"]["auth_method"], "oauth2")
        self.assertEqual(raw["docs"]["auth_scopes"], ["read", "write"])

    def test_normaliser_round_trips_skills_into_candidate(self) -> None:
        raw = _candidate_from_agent_card(
            {
                "name": "z",
                "url": "https://z.example",
                "skills": [
                    {"id": "search", "description": "search", "examples": ["e1"]},
                    "summary",
                ],
            }
        )
        candidate = normalize_candidate(raw, source="curated_a2a")
        names = {skill.name for skill in candidate.skills}
        self.assertEqual(names, {"search", "summary"})
        search = next(skill for skill in candidate.skills if skill.name == "search")
        self.assertEqual(search.description, "search")
        self.assertEqual(search.examples, ["e1"])


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()

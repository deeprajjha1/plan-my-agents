from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.registry.loader import load_registry, providers_for_capability


class RegistryLoaderTest(unittest.TestCase):
    def test_loads_registry_and_validates_core_shape(self) -> None:
        registry = load_registry(ROOT / "packages" / "registry" / "agents.json")

        self.assertIn("agents", registry)
        self.assertGreaterEqual(len(registry["agents"]), 5)
        self.assertIn("email_verification", registry["capabilities"])

    def test_finds_providers_for_capability(self) -> None:
        registry = load_registry(ROOT / "packages" / "registry" / "agents.json")

        providers = providers_for_capability(registry, "email_verification")
        provider_ids = {provider["id"] for provider in providers}

        self.assertIn("apollo", provider_ids)
        self.assertIn("hunter", provider_ids)


if __name__ == "__main__":
    unittest.main()
